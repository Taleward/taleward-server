"""Server 0.4.84: Repo-Umzug (B5), Schnittstelle 0.4.16/0.4.17 (publicUrl, serverId, Teile im Kapitel), kleinere
Sicherheitspunkte (S26), zweiter Faktor für die Verwaltung (S4), Stoppdatei für den lokalen Worker."""
import os
import shutil
import subprocess
import sys
import time
from urllib.parse import parse_qs, urlsplit

import pytest

from tests.freigabe_hilfe import freigabe
from tests.test_anmeldung import dienst  # noqa: F401 (Fixture)
from tests.test_pruefung_046 import _zur_pruefung
from tests.test_step2a import API
from tests.test_step2b import _kleine_teile, zusammenfassen  # noqa: F401 (Fixture)
from tests.test_verwaltung import admin, csrf_von  # noqa: F401 (Fixture)

pytestmark = pytest.mark.usefixtures("_kleine_teile")


# ---------------------------------------------------------------- B5: Repo-Umzug
def test_freigabe_unter_altem_und_neuem_namen():
    from app import freigabe as fg

    for name in ("Tinkerworkss/taleward-server", "Taleward/taleward-server", "TALEWARD/Taleward-Server"):
        text, sig = freigabe(name, "v0.4.84")
        for erwartet in ("Taleward/taleward-server", "Tinkerworkss/taleward-server"):
            assert fg.lesen(text, sig, erwartet, "v0.4.84").tag == "v0.4.84"
    for name in ("Fremd/taleward-server", "Taleward/taleward-app", "Tinkerworkss/taleward-worker", "Taleward/server",
                 "Taleward/taleward-server/x"):
        text, sig = freigabe(name, "v0.4.84")
        with pytest.raises(fg.FreigabeFehler):
            fg.lesen(text, sig, "Taleward/taleward-server", "v0.4.84")
    text, sig = freigabe("Fork/taleward-server", "v1.0.0")  # eigene Konfiguration: nur genau dieser Name
    assert fg.lesen(text, sig, "Fork/taleward-server", "v1.0.0")
    with pytest.raises(fg.FreigabeFehler):
        fg.lesen(text, sig, "Taleward/taleward-server", "v1.0.0")


def test_standard_adressen_neu():
    from app.config import Settings

    s = Settings(jwt_secret="x" * 40)
    assert s.update_server_repo == "Taleward/taleward-server" and s.update_app_repo == "Taleward/taleward-app"
    assert s.update_worker_repo == "Taleward/taleward-worker"
    for name in ("deploy/aktualisieren.sh", "deploy/docker-compose.yml", "install.sh"):
        assert "Tinkerworkss/" not in open(name, encoding="utf-8").read().replace("REPO_ALT=Tinkerworkss/", "")


@pytest.mark.skipif(not shutil.which("ssh-keygen") or sys.platform == "win32", reason="nur Linux mit ssh-keygen")
def test_skript_nimmt_beide_namen(tmp_path):
    from tests.test_freigabe import _skript_mit_testschluessel

    skript = _skript_mit_testschluessel(tmp_path)
    ordner = tmp_path / "f"
    ordner.mkdir()

    def pruefen(repo):
        text, sig = freigabe(repo, "v0.4.84", commit="c" * 40)
        (ordner / "freigabe.txt").write_bytes(text)
        (ordner / "freigabe.txt.sig").write_text(sig, encoding="utf-8")
        return subprocess.run(["bash", str(skript), "--freigabe-pruefen", str(ordner), "v0.4.84"], capture_output=True,
                              text=True)

    for repo in ("Taleward/taleward-server", "Tinkerworkss/taleward-server"):
        r = pruefen(repo)
        assert r.returncode == 0 and r.stdout.strip() == "c" * 40, repo
    for repo in ("Fremd/taleward-server", "Taleward/taleward-app", "Taleward/taleward.server"):
        assert pruefen(repo).returncode != 0, repo


# ---------------------------------------------------------------- Schnittstelle 0.4.16
def test_info_mit_public_url_und_server_id(client, dbs):
    from app.db import server_id
    from app.einstellungen import meta_schreiben
    from app.routers.auth import oeffentliche_herkunft

    i = client.get(f"{API}/info").json()
    assert i["apiVersion"] == "0.4.25" and i["serverId"] == server_id() and i["publicUrl"] is None
    meta_schreiben(dbs, "angabe.public_url", "https://taleward.example.org/pfad/")
    dbs.commit()
    assert client.get(f"{API}/info").json()["publicUrl"] == "https://taleward.example.org"
    assert oeffentliche_herkunft("http://192.168.178.24:8000/") == "http://192.168.178.24:8000"
    assert oeffentliche_herkunft("http://taleward.local:8000") == "http://taleward.local:8000"
    assert oeffentliche_herkunft("http://taleward.example.org") is None  # http nur im Heimnetz
    assert oeffentliche_herkunft("https://user:pw@taleward.example.org") is None
    assert oeffentliche_herkunft("ftp://192.168.1.2") is None and oeffentliche_herkunft("") is None
    assert oeffentliche_herkunft("https://[2001:db8::1]:8443/x") == "https://[2001:db8::1]:8443"


# ---------------------------------------------------------------- Schnittstelle 0.4.17: Teile im Kapitel
@pytest.fixture()
def kapitel(client, world, dbs, tmp_path):
    w = world
    sid = _zur_pruefung(client, w, dbs, tmp_path)["id"]
    text = "Erster Absatz.\n\nZweiter Absatz.\n\nDritter Absatz.\n\nVierter Absatz."
    assert client.put(f"{API}/sessions/{sid}/recap", headers=w["gm"], json={"text": text}).status_code == 200
    return w, sid


def test_teile_lesen_und_schreiben(client, dbs, kapitel):
    w, sid = kapitel
    url = f"{API}/sessions/{sid}/recap"
    assert client.get(url, headers=w["gm"]).json()["parts"] == []
    teile = [{"title": "Die Anwerbung", "firstParagraph": 0}, {"title": "  Eine Woche   später ", "firstParagraph": 2}]
    r = client.put(url, headers=w["gm"], json={"parts": teile})
    assert r.status_code == 200
    assert r.json()["parts"] == [{"title": "Die Anwerbung", "firstParagraph": 0},
                                 {"title": "Eine Woche später", "firstParagraph": 2}]
    # Ungültig → 400 invalid_parts, nichts geändert
    for falsch in ([{"title": "A", "firstParagraph": 4}], [{"title": "A", "firstParagraph": 2},
                                                          {"title": "B", "firstParagraph": 1}],
                   [{"title": " ", "firstParagraph": 0}], [{"title": "x" * 121, "firstParagraph": 0}],
                   [{"title": "A", "firstParagraph": 1}, {"title": "B", "firstParagraph": 1}]):
        r = client.put(url, headers=w["gm"], json={"parts": falsch})
        assert r.status_code == 400 and r.json()["code"] == "invalid_parts", falsch
    assert len(client.get(url, headers=w["gm"]).json()["parts"]) == 2
    # Text und Teile in einer Anfrage: Teile beziehen sich auf den neuen Text
    r = client.put(url, headers=w["gm"], json={"text": "Eins.\n\nZwei.", "parts": [{"title": "B", "firstParagraph": 1}]})
    assert r.json()["parts"] == [{"title": "B", "firstParagraph": 1}]
    # Ältere App ohne `parts`: gleiche Absatzzahl behält die Teile, andere verwirft sie
    r = client.put(url, headers=w["gm"], json={"text": "Eins neu.\n\nZwei neu."})
    assert r.json()["parts"] == [{"title": "B", "firstParagraph": 1}]
    r = client.put(url, headers=w["gm"], json={"text": "Alles in einem Absatz."})
    assert r.json()["parts"] == []
    # Leere Liste entfernt die Teile
    client.put(url, headers=w["gm"], json={"text": "A.\n\nB.", "parts": [{"title": "T", "firstParagraph": 0}]})
    assert client.put(url, headers=w["gm"], json={"parts": []}).json()["parts"] == []


def test_teile_fuer_spieler_nach_veroeffentlichung(client, dbs, kapitel):
    w, sid = kapitel
    url = f"{API}/sessions/{sid}/recap"
    client.put(url, headers=w["gm"], json={"parts": [{"title": "Teil zwei", "firstParagraph": 1}]})
    assert client.post(f"{API}/sessions/{sid}/publish", headers=w["gm"]).status_code in (200, 204)
    r = client.get(url, headers=w["pl"]).json()
    assert r["parts"] == [{"title": "Teil zwei", "firstParagraph": 1}] and "review" not in r


def test_teile_aus_dem_ergebnis(client, dbs, world, tmp_path):
    from app.models import GameSession, Recap
    from app.zusammenfassung import ergebnis_aus, speichern

    sid = _zur_pruefung(client, world, dbs, tmp_path)["id"]
    s = dbs.get(GameSession, sid)
    d = {"title": "T", "text": "A.\n\nB.\n\nC.", "openThreads": [], "proposals": [], "model": "m",
         "parts": [{"title": "Erster Teil", "firstParagraph": 0}, {"title": "Zweiter Teil", "firstParagraph": 2}]}
    speichern(dbs, s, ergebnis_aus(d), 1.0)
    dbs.commit()
    assert len(client.get(f"{API}/sessions/{sid}/recap", headers=world["gm"]).json()["parts"]) == 2
    # Passt nicht zum Text → keine Teile, das Kapitel bleibt lesbar
    speichern(dbs, s, ergebnis_aus({**d, "parts": [{"title": "X", "firstParagraph": 9}]}), 1.0)
    dbs.commit()
    assert dbs.get(Recap, sid).parts is None


# ---------------------------------------------------------------- S26
def test_s26_1_bestaetigungsseite_mit_kopfzeilen(client, world, dbs, dienst):  # noqa: F811
    from tests.test_anmeldung import VERIFIER, challenge_von

    r = client.get(f"{API}/auth/oidc/google/start", params={"challenge": challenge_von(VERIFIER), "purpose": "login"},
                   follow_redirects=False)
    q = {k: v[0] for k, v in parse_qs(urlsplit(r.headers["location"]).query).items()}
    dienst.nonce = q["nonce"]
    dienst.claims = {"sub": "g-26", "email": "a26@example.org", "email_verified": True, "name": "A"}
    r = client.get("/auth/oidc/google/callback", params={"code": "gut", "state": q["state"]}, follow_redirects=False)
    assert r.status_code == 200
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["x-content-type-options"] == "nosniff"


def _probe(dbs, w, tmp_path, client):
    from app.kapitelprobe import Probe, _speichern
    from app.models import User

    sid = _zur_pruefung(client, w, dbs, tmp_path)["id"]
    anna = dbs.query(User).filter_by(username="anna").one()
    p = Probe(id="22222222-2222-2222-2222-222222222222", session_id=sid, kampagne="K", kapitel=1, quelle="Runde",
              zeilen=1, modell="m", gestartet="2026-10-13T00:00:00Z", zustand="läuft", text="A.\n\nB.",
              campaign_id=w["cid"], besitzer=anna.id)
    _speichern(p)
    return p


def test_s26_2_wartender_probelauf_prueft_freigabe_erneut(client, world, dbs, tmp_path, monkeypatch):
    from app import kapitelprobe
    from app.models import Campaign

    p = _probe(dbs, world, tmp_path, client)
    aufrufe = []
    monkeypatch.setattr(kapitelprobe, "_rechnen_jetzt", lambda *a: aufrufe.append(a))
    k = type("K", (), {"art": "api"})()
    dbs.get(Campaign, world["cid"]).allow_cloud_summary = False  # während der Lauf wartet abgeschaltet
    dbs.commit()
    kapitelprobe._rechnen(p, k, {}, {}, world["cid"], p.session_id)
    assert not aufrufe and p.zustand == "fehler" and "abgeschaltet" in p.fehler
    dbs.get(Campaign, world["cid"]).allow_cloud_summary = True
    dbs.commit()
    kapitelprobe._rechnen(p, k, {}, {}, world["cid"], p.session_id)
    assert len(aufrufe) == 1
    # Spielleitung abgegeben → ebenfalls nicht
    from app.models import Member

    dbs.query(Member).filter_by(campaign_id=world["cid"], role="gm").update({"role": "player"})
    dbs.commit()
    kapitelprobe._rechnen(p, k, {}, {}, world["cid"], p.session_id)
    assert len(aufrufe) == 1 and "Spielleitung" in p.fehler


def test_s26_5_wartende_korrektur_prueft_freigabe_erneut(client, world, dbs, tmp_path, monkeypatch):
    from app import kapitelprobe, sprachmodell
    from app.models import Campaign

    p = _probe(dbs, world, tmp_path, client)
    p.zustand = "fertig"
    kapitelprobe._speichern(p)
    aufgerufen = []
    monkeypatch.setattr(sprachmodell.Ablauf, "korrigieren", lambda *a, **k: aufgerufen.append(1))
    monkeypatch.setattr("app.zusammenfassung.api_klient", lambda _k: object())
    dbs.get(Campaign, world["cid"]).allow_cloud_summary = False
    dbs.commit()
    kapitelprobe._korrigieren(p, type("K", (), {"art": "api"})(), {}, {0: "Falsch."}, "")
    runde = kapitelprobe.lesen(p.id).korrekturen[0]
    assert not aufgerufen and "abgeschaltet" in runde["fehler"] and not kapitelprobe.lesen(p.id).korrektur_laeuft


def test_s26_5_zwei_schnelle_klicks_ein_durchgang(client, world, dbs, tmp_path, monkeypatch):
    from app import kapitelprobe
    from app.einstellungen import meta_schreiben
    from app.models import Campaign, User

    p = _probe(dbs, world, tmp_path, client)
    p.zustand = "fertig"
    kapitelprobe._speichern(p)
    meta_schreiben(dbs, "llm.art", "attrappe")
    dbs.get(Campaign, world["cid"]).allow_cloud_summary = True
    dbs.commit()
    gestartet = []
    monkeypatch.setattr(kapitelprobe.threading, "Thread",
                        lambda **kw: type("T", (), {"start": lambda self: gestartet.append(kw)})())
    anna = dbs.query(User).filter_by(username="anna").one()
    kapitelprobe.korrigieren_starten(dbs, p, {0: "Falsch."}, "", anna.id)
    with pytest.raises(ValueError, match="korrektur_laeuft"):
        kapitelprobe.korrigieren_starten(dbs, p, {0: "Falsch."}, "", anna.id)  # altes Objekt, frischer Stand zählt
    assert len(gestartet) == 1


def test_s26_3_mistral_nur_mit_exaktem_host(dbs):
    from app.einstellungen import ist_mistral_adresse

    assert ist_mistral_adresse("https://api.mistral.ai/v1") and ist_mistral_adresse("https://API.mistral.ai")
    for falsch in ("https://api.mistral.ai.example.net/v1", "http://api.mistral.ai/v1", "https://x.api.mistral.ai",
                   "https://example.net/api.mistral.ai", "", None):
        assert not ist_mistral_adresse(falsch), falsch


def test_s26_4_laufende_sperre_bleibt_bei_vielen_netzen(monkeypatch):
    from app import begrenzung

    monkeypatch.setattr(begrenzung, "MAX_SCHLUESSEL", 200)
    z = begrenzung.Zaehler()
    for _ in range(10):
        z.zaehlen("login-name:opfer", 900)
    for i in range(5000):  # viele Netze, je zwei Versuche
        z.zaehlen(f"login-adr:2001:db8:{i:x}::/64", 900)
        z.zaehlen(f"login-adr:2001:db8:{i:x}::/64", 900)
    assert z.voll("login-name:opfer", 10, 900) and len(z._daten) <= 200


# ---------------------------------------------------------------- S4: zweiter Faktor
def test_totp_nach_rfc_6238():
    from app.verwaltung import zweiter_faktor as zf

    geheim = "GEZDGNBVGY3TQOJQGEZDGNBVGY3TQOJQ"  # „12345678901234567890“
    assert zf.code(geheim, 59 // 30) == "287082"
    assert zf.code(geheim, 1111111109 // 30) == "081804"


def _code(dbs, user_id, jetzt=None):
    from app.verwaltung import zweiter_faktor as zf

    dbs.expire_all()
    geheim = zf.stand(dbs, user_id)["geheim"] if zf.stand(dbs, user_id) else zf.angefangen(dbs, user_id)
    return zf.code(geheim, int((jetzt or time.time()) // 30))


def test_zweiter_faktor_einrichten_anmelden_ausschalten(client, dbs, admin, monkeypatch):  # noqa: F811
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf

    chef = dbs.query(User).filter_by(username="chef").one()
    seite = client.get("/verwaltung/anmeldung").text  # seit 0.4.86 unter Anmeldung
    assert "Dein zweiter Faktor" in seite and "Einrichten" in seite
    r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": admin, "password": "falsch"})
    assert r.status_code == 400 and not zf.angefangen(dbs, chef.id)
    r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": admin, "password": "geheim123"})
    assert r.status_code == 200 and "data:image/svg+xml" in r.text
    r = client.post("/verwaltung/zweiter-faktor/bestaetigen", data={"csrf": admin, "code": "000000"})
    assert r.status_code == 400 and not zf.aktiv(dbs, chef.id)
    r = client.post("/verwaltung/zweiter-faktor/bestaetigen", data={"csrf": admin, "code": _code(dbs, chef.id)})
    assert r.status_code == 200
    codes = [c for c in r.text.split("<code>")[1:]]
    codes = [c.split("</code>")[0] for c in codes if "-" in c.split("</code>")[0]]
    assert len(codes) == zf.ANZAHL_CODES
    dbs.expire_all()
    assert zf.aktiv(dbs, chef.id)
    # Neue Anmeldung: Passwort allein reicht nicht
    client.cookies.clear()
    r = client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/anmelden/code"
    assert "tw_verwaltung=" not in r.headers.get("set-cookie", "").replace("tw_verwaltung_code=", "")
    assert client.get("/verwaltung/", follow_redirects=False).status_code == 303  # noch nicht angemeldet
    assert "Code aus deiner Authenticator-App" in client.get("/verwaltung/anmelden/code").text
    r = client.post("/verwaltung/anmelden/code", data={"code": "123456"}, follow_redirects=False)
    assert r.status_code == 400
    # Der beim Einrichten benutzte Code gilt nicht noch einmal; ein Wiederherstellungscode schon (einmal)
    r = client.post("/verwaltung/anmelden/code", data={"code": codes[0].lower()}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/"
    assert client.get("/verwaltung/", follow_redirects=False).status_code == 200
    dbs.expire_all()
    assert zf.restliche_codes(dbs, chef.id) == zf.ANZAHL_CODES - 1
    assert not zf.pruefen(dbs, chef.id, codes[0])
    # TOTP-Code: gilt einmal (nächstes Zeitfenster simuliert)
    spaeter = time.time() + 60
    c = _code(dbs, chef.id, spaeter)
    assert zf.pruefen(dbs, chef.id, c, jetzt=spaeter) and not zf.pruefen(dbs, chef.id, c, jetzt=spaeter)
    dbs.commit()
    # Ohne Cookie für den Code-Schritt kein Weg zur Seite
    client.cookies.clear()
    assert client.get("/verwaltung/anmelden/code", follow_redirects=False).status_code == 303
    assert client.post("/verwaltung/anmelden/code", data={"code": codes[1]}, follow_redirects=False).status_code == 303
    dbs.expire_all()
    assert zf.restliche_codes(dbs, chef.id) == zf.ANZAHL_CODES - 1  # nichts verbraucht
    # Ausschalten nur mit Passwort und Code
    client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"})
    client.post("/verwaltung/anmelden/code", data={"code": codes[2]})
    csrf = csrf_von(client.get("/verwaltung/").text)
    r = client.post("/verwaltung/zweiter-faktor/aus", data={"csrf": csrf, "password": "geheim123", "code": "999999"})
    assert r.status_code == 400
    dbs.expire_all()
    assert zf.aktiv(dbs, chef.id)
    r = client.post("/verwaltung/zweiter-faktor/aus", data={"csrf": csrf, "password": "geheim123", "code": codes[3]},
                    follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert not zf.aktiv(dbs, chef.id)


def test_code_schritt_ist_begrenzt(client, dbs, admin):  # noqa: F811
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf
    from app.verwaltung.router import SPERRE_VERSUCHE

    chef = dbs.query(User).filter_by(username="chef").one()
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    client.cookies.clear()
    client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"})
    for _ in range(SPERRE_VERSUCHE):
        assert client.post("/verwaltung/anmelden/code", data={"code": "000000"}).status_code == 400
    r = client.post("/verwaltung/anmelden/code", data={"code": "000000"})
    assert "Zu viele Fehlversuche" in r.text


def test_verwaltung_nur_aus_netzen(client, monkeypatch):
    from app.config import get_settings
    from app.verwaltung.router import netz_erlaubt

    assert netz_erlaubt("203.0.113.9")  # leer = überall
    monkeypatch.setenv("VERWALTUNG_NUR_AUS", "192.168.178.0/24, 10.8.0.0/24, kaputt")
    get_settings.cache_clear()
    try:
        assert netz_erlaubt("192.168.178.20") and netz_erlaubt("10.8.0.5") and netz_erlaubt("::ffff:10.8.0.5")
        assert not netz_erlaubt("203.0.113.9") and not netz_erlaubt(None) and not netz_erlaubt("testclient")
        r = client.get("/verwaltung/anmelden")  # der Testclient kommt von „testclient“
        assert r.status_code == 404 and r.headers["x-frame-options"] == "DENY"
        assert client.get(f"{API}/info").status_code == 200  # die App-Schnittstelle bleibt erreichbar
    finally:
        monkeypatch.delenv("VERWALTUNG_NUR_AUS")
        get_settings.cache_clear()


def test_zweiter_faktor_aus_per_befehl(client, dbs, admin):  # noqa: F811
    from typer.testing import CliRunner

    from app.cli import app
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf

    chef = dbs.query(User).filter_by(username="chef").one()
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    r = CliRunner().invoke(app, ["zweiter-faktor-aus", "-u", "chef"])
    assert r.exit_code == 0, r.output
    dbs.expire_all()
    assert not zf.aktiv(dbs, chef.id)


# ---------------------------------------------------------------- Lokaler Worker: Stoppdatei (Windows)
def test_worker_achtet_auf_stoppdatei(tmp_path):
    datei = tmp_path / "stop"
    code = ("import time\nfrom app.cli import _auf_stoppdatei_achten\n"
            "try:\n    _auf_stoppdatei_achten()\n    print('bereit', flush=True)\n    time.sleep(30)\n"
            "except KeyboardInterrupt:\n    print('angehalten', flush=True)\n")
    p = subprocess.Popen([sys.executable, "-c", code], env={**os.environ, "WORKER_STOP_DATEI": str(datei)},
                         stdout=subprocess.PIPE, text=True, cwd=os.getcwd())
    assert p.stdout.readline().strip() == "bereit"  # erst dann die Datei – das Laden kann unter Last dauern
    datei.touch()
    out, _ = p.communicate(timeout=20)
    assert "angehalten" in out


def test_lokaler_worker_stoppt_ohne_signal(client, dbs, admin, monkeypatch):  # noqa: F811
    """Wie unter Windows: Der Worker reagiert nicht auf SIGINT, nur auf die Stoppdatei."""
    from app.verwaltung import lokaler_worker

    def ersatz(modus, server_url):
        code = ("import os, signal, sys, time\n"
                "signal.signal(signal.SIGINT, signal.SIG_IGN)\n"
                "print('start', flush=True)\n"
                "for _ in range(600):\n"
                "    if os.path.exists(os.environ['WORKER_STOP_DATEI']):\n"
                "        print('beendet', flush=True); sys.exit(0)\n"
                "    time.sleep(0.1)\n")
        return [sys.executable, "-c", code]

    knecht = lokaler_worker.LokalerKnecht(befehl=ersatz)
    knecht.starten(dbs, "attrappe")
    for _ in range(50):
        if any("start" in z for z in knecht.log_ende()):
            break
        time.sleep(0.1)
    knecht.beenden(warten=10)
    assert not knecht.laeuft() and any("beendet" in z for z in knecht.log_ende())
    knecht.starten(dbs, "attrappe")  # Neustart räumt die alte Stoppdatei weg
    time.sleep(1)
    assert knecht.laeuft()
    knecht.beenden(warten=10)
