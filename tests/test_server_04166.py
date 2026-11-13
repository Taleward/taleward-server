"""Server 0.4.166: Wartegrund statt Sackgasse (Schnittstelle 0.4.36), Stripe-Anschrift, S69 (Zusatz-Netz)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_server_04132 import cloud_einrichten
from tests.test_step2a import neue_session

API = "/api/v1"


def _wartend(dbs, w, client, art="transcribe", minuten=11):
    """Runde, deren Auftrag seit `minuten` in der Schlange steht."""
    from app import queue
    from app.db import utcnow
    from app.models import GameSession, Job

    sid = neue_session(client, w)["id"]
    s = dbs.get(GameSession, sid)
    job = Job(type=art, session_id=sid, required_capability="asr" if art == "transcribe" else "llm",
              engine="local")
    dbs.add(job)
    queue.set_state(s, "queued" if art == "transcribe" else "summarizing", progress=0.0)
    dbs.flush()
    job.created_at = utcnow() - timedelta(minutes=minuten)
    dbs.commit()
    return sid


def _status(client, w, sid, wer="gm"):
    r = client.get(f"{API}/sessions/{sid}/status", headers=w[wer])
    assert r.status_code == 200, r.text
    return r.json()


def _worker(dbs, faehigkeiten="asr,llm", pausiert=False, llm=True):
    import json

    from app.db import utcnow
    from app.models import Worker

    dbs.add(Worker(name="PC", token_hash="x" * 64, capabilities=faehigkeiten, last_seen_at=utcnow(), paused=pausiert,
                   info=json.dumps({"llm": "gemma"} if llm else {})))
    dbs.commit()


def _llm(dbs, art):
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "llm.art", art)
    dbs.commit()


def _spieler_sicht(dbs, sid, name="ben"):
    from app.models import GameSession, User
    from app.services import processing_status

    s = dbs.get(GameSession, sid)
    return processing_status(dbs, s, "de", dbs.scalar(select(User).where(User.username == name)))


# ---------------------------------------------------------------- Zeitpunkt und Grund
def test_erst_nach_zehn_minuten(client, world, dbs):
    sid = _wartend(dbs, world, client, minuten=5)
    st = _status(client, world, sid)
    assert st["waitReason"] is None and st["waitActions"] == []


def test_kein_worker(client, world, dbs):
    sid = _wartend(dbs, world, client)
    st = _status(client, world, sid)
    assert st["waitReason"] == "no_worker" and st["waitActions"] == []


def test_worker_da_heisst_weg_frei(client, world, dbs):
    _worker(dbs)
    sid = _wartend(dbs, world, client)
    assert _status(client, world, sid)["waitReason"] is None


def test_worker_pausiert(client, world, dbs):
    _worker(dbs, pausiert=True)
    sid = _wartend(dbs, world, client)
    assert _status(client, world, sid)["waitReason"] == "paused"


def test_abschrift_cloud_nicht_freigegeben(client, world, dbs):
    cloud_einrichten(dbs)
    sid = _wartend(dbs, world, client)
    st = _status(client, world, sid)
    assert st["waitReason"] == "cloud_not_allowed" and st["waitActions"] == ["allow_external_transcription"]
    # einschalten geht nur über …/settings; danach ist der Weg frei (die Cloud übernimmt zur eingestellten Zeit)
    from app.models import Campaign

    dbs.get(Campaign, world["cid"]).allow_external_transcription = True
    dbs.commit()
    st = _status(client, world, sid)
    assert st["waitReason"] is None and st["waitActions"] == []


def test_kapitel_cloud_nicht_freigegeben(client, world, dbs):
    cloud_einrichten(dbs)
    sid = _wartend(dbs, world, client, art="summarize")
    st = _status(client, world, sid)
    assert st["waitReason"] == "cloud_not_allowed" and st["waitActions"] == ["allow_cloud_summary"]
    from app.models import Campaign

    dbs.get(Campaign, world["cid"]).allow_cloud_summary = True
    dbs.commit()
    assert _status(client, world, sid)["waitReason"] is None


def test_kapitel_lokal_ohne_worker_und_abgeschaltet(client, world, dbs):
    _llm(dbs, "lokal")
    sid = _wartend(dbs, world, client, art="summarize")
    st = _status(client, world, sid)
    assert st["waitReason"] == "no_worker" and st["waitActions"] == []  # Cloud gesperrt: kein Schalter
    _worker(dbs)
    assert _status(client, world, sid)["waitReason"] is None
    _llm(dbs, "aus")
    assert _status(client, world, sid)["waitReason"] == "paused"


def test_kostenlimit_haelt_an(client, world, dbs, monkeypatch):
    from app import kosten
    from app.models import Campaign

    cloud_einrichten(dbs)
    dbs.get(Campaign, world["cid"]).allow_cloud_summary = True
    dbs.commit()
    monkeypatch.setattr(kosten, "erreicht", lambda db: True)
    sid = _wartend(dbs, world, client, art="summarize")
    assert _status(client, world, sid)["waitReason"] == "paused"


def test_schalter_nur_wenn_angeboten(client, world, dbs):
    """Ohne eingerichtete Cloud sind die Schalter gesperrt – dann kein Schalter in waitActions."""
    sid = _wartend(dbs, world, client)
    assert _status(client, world, sid)["waitActions"] == []


def test_guthaben(client, world, dbs, monkeypatch):
    from app import abrechnung

    monkeypatch.setattr(abrechnung, "an", lambda db: True)
    monkeypatch.setattr(abrechnung, "warte_grund", lambda db, job: "leer")
    monkeypatch.setattr(abrechnung, "hinweis", lambda db, job, lang: None)
    monkeypatch.setattr(abrechnung, "ist_aktive_sl", lambda db, uid, c: True)
    monkeypatch.setattr(abrechnung, "warten_fuer", lambda db, job, uid: {"reason": "empty", "estimate_cents": None,
                                                                         "credit_id": None, "job_id": None})
    sid = _wartend(dbs, world, client)
    st = _status(client, world, sid)
    assert st["waitReason"] == "credit" and st["creditWait"]["reason"] == "empty"
    sp = _spieler_sicht(dbs, sid)
    assert sp.wait_reason == "waiting" and sp.wait_actions == [] and sp.credit_wait is None


# ---------------------------------------------------------------- Wer sieht was
def test_spielende_nur_waiting(client, world, dbs):
    cloud_einrichten(dbs)
    sid = _wartend(dbs, world, client, art="summarize")
    sp = _spieler_sicht(dbs, sid)
    assert sp.wait_reason == "waiting" and sp.wait_actions == [] and sp.credit_wait is None
    roh = sp.model_dump(mode="json", by_alias=True)
    assert roh["waitReason"] == "waiting" and roh["waitActions"] == []
    # vor Ablauf der Wartezeit auch für Spielende null
    sid2 = _wartend(dbs, world, client, minuten=1)
    assert _spieler_sicht(dbs, sid2).wait_reason is None


def test_fremde_und_spielende_404_ueber_die_schnittstelle(client, world, dbs):
    sid = _wartend(dbs, world, client)
    for wer in ("pl", "out"):
        assert client.get(f"{API}/sessions/{sid}/status", headers=world[wer]).status_code == 404


def test_ausgetretene_sl_bekommt_keinen_grund(client, world, dbs):
    from app.models import Member

    sid = _wartend(dbs, world, client)
    m = dbs.get(Member, world["gm_member"])
    m.role = "player"  # nur zur Prüfung der Rollenprüfung: als Spielerin nur waiting
    dbs.commit()
    assert _spieler_sicht(dbs, sid, "anna").wait_reason == "waiting"


def test_arbeitet_schon_jemand_daran(client, world, dbs):
    from app.models import Job

    sid = _wartend(dbs, world, client)
    job = dbs.scalar(select(Job).where(Job.session_id == sid))
    job.state = "leased"
    dbs.commit()
    assert _status(client, world, sid)["waitReason"] is None


# ---------------------------------------------------------------- Stripe-Anschrift
def test_stripe_anschrift():
    from app import rechtstexte

    texte = repr(rechtstexte.ANBIETER_ANSCHRIFT) + repr(rechtstexte.ANBIETER_ANSCHRIFT_EN)
    assert "Grand Canal" not in texte
    assert "One Wilton Park, Wilton Place, Dublin 2, D02 FX04" in texte
    assert "Irland" in repr(rechtstexte.ANBIETER_ANSCHRIFT) and "Ireland" in repr(rechtstexte.ANBIETER_ANSCHRIFT_EN)



# ---------------------------------------------------------------- S69: Zusatz-Netz
def test_caddy_leitet_nur_an_feste_adressen():
    import re

    import yaml

    from tests.test_server_04164 import DEPLOY

    compose = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    kern = compose["networks"]["kern"]
    assert kern["internal"] is True
    assert kern["ipam"]["config"] == [{"subnet": "${TALEWARD_KERN_NETZ:-10.213.47}.0/28"}]
    dienste = compose["services"]
    assert dienste["server"]["networks"]["kern"] == {"ipv4_address": "${TALEWARD_KERN_NETZ:-10.213.47}.2"}
    assert dienste["testserver"]["networks"]["kern"] == {"ipv4_address": "${TALEWARD_KERN_NETZ:-10.213.47}.3"}
    umgebung = dienste["caddy"]["environment"]
    assert umgebung["TALEWARD_SERVER_ZIEL"] == "${TALEWARD_KERN_NETZ:-10.213.47}.2:8000"
    assert umgebung["TALEWARD_TEST_ZIEL"] == "${TALEWARD_KERN_NETZ:-10.213.47}.3:8000"
    for name, dienst in dienste.items():  # im Kern-Netz nur Caddy, Server und Testinstanz
        if name not in ("caddy", "server", "testserver"):
            assert "kern" not in (dienst.get("networks") or {}), name
    caddyfile = (DEPLOY / "Caddyfile").read_text()
    ziele = re.findall(r"reverse_proxy (\S+)", caddyfile)
    assert ziele == ["{$TALEWARD_SERVER_ZIEL:server:8000}"]
    test = (DEPLOY / "test-instanz.sh").read_text()
    assert re.findall(r"reverse_proxy (\S+)", test) == ["{\\$TALEWARD_TEST_ZIEL:testserver:8000}"]
    for skript in ("website-neu-laden.sh", "website_regeln.py"):  # die Website leitet nirgends hin weiter
        assert "reverse_proxy" not in (DEPLOY / skript).read_text()


@pytest.mark.parametrize("ziel_", ["127.0.0.1:2019", "172.18.0.2:8000", "server.:8000", "1server:80", "app-:80",
                                   "app.:80", "10.213.47.2:8000", "[::1]:80", "_app:80"])
def test_s69_ziele_abgelehnt(tmp_path, ziel_):
    from tests.test_server_04164 import _aufruf, _ordner

    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", ziel_).returncode != 0
    assert not list((ziel / "extra-caddy").glob("*.caddy"))


def test_s69_ziel_muss_im_zusatz_netz_haengen(tmp_path):
    from tests.test_server_04164 import _aufruf, _ordner, netz

    ziel, stub = _ordner(tmp_path)
    r = _aufruf(ziel, stub, "an", "app.example.org", "unbekannt:8080")
    assert r.returncode != 0 and "taleward-zusatz" in r.stderr
    # auch im Netz von Taleward → abgelehnt
    netz(tmp_path, ["taleward-caddy-1 taleward_default=caddy, taleward_kern=caddy, taleward-zusatz=caddy,",
                    "zwitter-1 taleward-zusatz=zwitter, taleward_default=zwitter,"])
    r = _aufruf(ziel, stub, "an", "app.example.org", "zwitter:8080")
    assert r.returncode != 0 and "Netz von Taleward" in r.stderr
    assert not list((ziel / "extra-caddy").glob("*.caddy"))
    # nur im Zusatz-Netz → angenommen
    netz(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", "meine-app:8080").returncode == 0


@pytest.mark.parametrize("belegt", ["server", "testserver", "caddy", "worker-cpu", "ollama", "taleward-server-1"])
def test_s69_fremder_taleward_name_schaltet_alles_ab(tmp_path, belegt):
    from tests.test_server_04164 import _aufruf, _ordner, netz

    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", "meine-app:8080").returncode == 0
    netz(tmp_path, ["taleward-caddy-1 taleward_default=caddy, taleward_kern=caddy, taleward-zusatz=caddy,",
                    "zusatz-meine-app-1 taleward-zusatz=meine-app,zusatz-meine-app-1,",
                    f"boese-1 taleward-zusatz=boese,{belegt},"])
    r = _aufruf(ziel, stub, "an", "neu.example.org", "meine-app:8080")
    assert r.returncode != 0 and belegt in r.stderr
    assert not list((ziel / "extra-caddy").glob("*.caddy"))
    assert (ziel / "extra-caddy" / "aus" / "app.example.org.caddy").exists()
    assert _aufruf(ziel, stub, "liste").returncode != 0


def test_s69_update_parkt_bei_fremdem_namen(tmp_path):
    from tests.test_server_04164 import _absichern

    ziel, r = _absichern(tmp_path, {"gut.example.org.caddy": "gut"}, [
        "taleward-caddy-1 taleward_default=caddy, taleward_kern=caddy, taleward-zusatz=caddy,",
        "fremd-1 taleward-zusatz=fremd,server,"])
    assert r.returncode == 0 and "ACHTUNG" in r.stdout
    assert (ziel / "extra-caddy" / "aus" / "gut.example.org.caddy").exists()
    assert not (ziel / "extra-caddy" / "gut.example.org.caddy").exists()


def test_s69_alte_testregel_wird_umgestellt(tmp_path):
    from tests.test_server_04164 import _aufruf, _ordner

    ziel, stub = _ordner(tmp_path)
    (ziel / "test-caddy").mkdir()
    regel = ziel / "test-caddy" / "test.caddy"
    regel.write_text("test.example.org {\n\treverse_proxy testserver:8000\n}\n")
    assert _aufruf(ziel, stub, "liste").returncode == 0
    assert regel.read_text() == "test.example.org {\n\treverse_proxy {$TALEWARD_TEST_ZIEL:testserver:8000}\n}\n"
    assert "restart caddy" in (tmp_path / "aufrufe").read_text()


def test_s69_betriebsanleitung():
    from tests.test_server_04164 import WURZEL

    text = (WURZEL / "docs" / "BETRIEB.md").read_text()
    assert "nicht wie Taleward-Dienste heißen" in text


def test_doku_ausnahme_nur_genau_der_pfad():
    """Hinweis 13.11.: nur /api/v1/docs (und mit Schrägstrich) ohne HTML-Kopfzeilen, nichts, was nur so anfängt."""
    from app.verwaltung import router

    quelle = __import__("inspect").getsource(router)
    assert 'pfad not in (DOKU_PFAD, DOKU_PFAD + "/")' in quelle


# ---------------------------------------------------------------- Design-Prüfung 13.11. (Verwaltung)
def test_design_pruefung_verwaltung():
    from tests.test_server_04164 import WURZEL

    app = WURZEL / "app"
    vorlagen = app / "verwaltung" / "templates"
    for datei in list(vorlagen.glob("*.html")) + [app / "sprachmodell.py"]:
        text = datei.read_text(encoding="utf-8")
        assert "Arbeitsrechner" not in text and "der Rechner ist" not in text, datei.name
    css = (app / "verwaltung" / "static" / "verwaltung.css").read_text(encoding="utf-8")
    assert "uppercase" not in css  # Satzschreibung statt Versalien
    assert ".checkliste .offen .icon { color:var(--ink-muted); }" in css  # siegel nur für Achtung
    faktor = (vorlagen / "_zweiter_faktor.html").read_text(encoding="utf-8")
    assert "statt des Codes auch einen Passkey" in faktor
    discord = (vorlagen / "discord.html").read_text(encoding="utf-8")
    assert '_("Schalter")' not in discord and "Bot ein- und ausschalten" in discord
    assert '<a href="/discord-bot/datenschutz">' in discord and '<a href="/discord-bot/nutzungsbedingungen">' in discord
    assert '<details class="karte" id="spurschluessel"' in discord and discord.count('id="spurschluessel"') == 1
