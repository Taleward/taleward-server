"""Server 0.4.89: S31 (Geheimes aus Bogen/Hintergrund bleibt geheim) mit den kleineren Punkten zur Auswertung, S29
(Einladungscodes mit 12 Ziffern), Abrechnung M1/M2/L1/L2/N3-Rest, Sitzungen auf „Guthaben“, S21.4- und S21.5-Rest."""
import re
from pathlib import Path

import pytest
from sqlalchemy import select

from tests.test_abrechnung_0485 import _anna, _gutschreiben, _modus, _sl_anmelden
from tests.test_charakterauswertung_0418 import _charakter, _dritte, _entscheiden, _schalten
from tests.test_step2a import neue_session
from tests.test_step2b import _kleine_teile  # noqa: F401 (Fixture)
from tests.test_step5 import einstellen
from tests.test_unterlagen import Unterlagenmodell, api, verarbeiten, vorschlag  # noqa: F401
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

API = "/api/v1"


def _beitreten(client, w, h, name="Dora"):
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    c = client.post(f"{API}/campaigns/join", json={"code": code, "characterName": name}, headers=h).json()
    return next(m["id"] for m in c["members"] if m["characterName"] == name)


def _auswerten_und_annehmen(client, w, dbs, api_, titel="Orden der Asche"):
    api_["modell"] = Unterlagenmodell([vorschlag(title=titel, entryType="faction", gmNotes=None,
                                                 detail="Jagt Tharvok seit Jahren.")])
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234")
    _schalten(client, w)
    client.patch(f"{API}/campaigns/{w['cid']}", headers=w["gm"], json={"allowCloudSummary": True})
    _charakter(client, w, w["pl"])
    a = client.post(f"{API}/campaigns/{w['cid']}/members/me/backstory-analysis", headers=w["pl"]).json()
    _entscheiden(client, a["id"], w["gm"])
    assert verarbeiten(dbs)
    return a


def _namen(client, w, h):
    return {x["name"] for x in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=h).json()}


# ---------------------------------------------------------------- S31
def test_s31_nur_sl_und_urheberin_auch_spaeter(client, world, dbs, api, make_user, login):  # noqa: F811
    from app.zusammenfassung import eingabe_bauen, vorschlag_eingabe

    w = world
    _auswerten_und_annehmen(client, w, dbs, api)
    [v] = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    assert v["hiddenFromMemberIds"] == []  # niemand sonst da
    assert client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"}).status_code == 200
    assert "Orden der Asche" in _namen(client, w, w["pl"])
    make_user("dora")
    dora = login("dora")
    _beitreten(client, w, dora)
    assert "Orden der Asche" not in _namen(client, w, dora)  # Nachzüglerin sieht es nicht
    # … und für die KI gilt es nicht als bekannt
    from app.models import GameSession

    s = dbs.get(GameSession, neue_session(client, w)["id"])
    basis = eingabe_bauen(dbs, s)
    assert "Orden der Asche" not in [b.name for b in basis.bibel]
    assert "Orden der Asche" in [e["name"] for e in vorschlag_eingabe(dbs, s, basis)["geheim"]]


def test_s31_andere_vor_dem_annehmen_ausgetreten(client, world, dbs, api, make_user, login):  # noqa: F811
    w = world
    cleo = _dritte(client, w)
    _auswerten_und_annehmen(client, w, dbs, api)
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{cleo}", headers=w["out"]).status_code == 204
    [v] = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"})
    make_user("dora")
    dora = login("dora")
    _beitreten(client, w, dora)
    assert "Orden der Asche" not in _namen(client, w, dora)
    # Legt die SL selbst fest, wer es weiß, gilt ihre Entscheidung (auch für spätere Nachzügler)
    e = next(x for x in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"]).json()
             if x["name"] == "Orden der Asche")
    assert client.patch(f"{API}/entries/{e['id']}", headers=w["gm"],
                        json={"hiddenFromMemberIds": []}).status_code == 200
    assert "Orden der Asche" in _namen(client, w, dora)
    from app.models import Entry

    dbs.expire_all()
    assert dbs.get(Entry, e["id"]).origin_analysis_id is None and dbs.get(Entry, e["id"]).allgemein_bekannt


def test_s31_umzug_als_sl_wissen(client, world, dbs, api):  # noqa: F811
    from app import umzug
    from app.models import Campaign, Entry

    w = world
    _auswerten_und_annehmen(client, w, dbs, api)
    [v] = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"})
    dbs.expire_all()
    e = dbs.scalar(select(Entry).where(Entry.name == "Orden der Asche"))
    assert e.origin_analysis_id
    daten, _dateien = umzug.packliste(dbs, dbs.get(Campaign, w["cid"]), set())
    [x] = [x for x in daten["entries"] if x["name"] == "Orden der Asche"]
    assert x["visibility"] == "gm_only"  # die Marke reist nicht mit – also als SL-Wissen


def test_s31_ausgetreten_waehrend_der_auswertung(client, world, dbs, api):  # noqa: F811
    from app import charakterauswertung, queue
    from app.models import CharacterAnalysis, Proposal

    w = world
    einstellen(dbs, art="attrappe")
    _schalten(client, w)
    _charakter(client, w, w["pl"])
    a = client.post(f"{API}/campaigns/{w['cid']}/members/me/backstory-analysis", headers=w["pl"]).json()
    _entscheiden(client, a["id"], w["gm"])
    dbs.expire_all()
    from tests.test_abrechnung_0485 import _knecht

    job = queue.claim(dbs, _knecht(dbs), ["llm"])
    assert job is not None and job.analysis_id == a["id"]
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["pl"]).status_code == 204
    dbs.expire_all()
    x = dbs.get(CharacterAnalysis, a["id"])
    assert x.state == "processing"  # läuft zu Ende …
    charakterauswertung.speichern(dbs, x, {"proposals": [vorschlag()]}, "local", None, 1.0)
    dbs.commit()
    assert x.state == "rejected" and x.abschnitte is None  # … aber ohne Vorschläge aus ihrem Material
    assert dbs.query(Proposal).filter(Proposal.analysis_id == a["id"]).count() == 0


def test_s31_fehlermeldung_und_links(client, world, dbs):
    from app import charakterauswertung
    from app.models import CharacterAnalysis
    from app.queue import status_message

    w = world
    _schalten(client, w)
    _charakter(client, w, w["pl"])
    a = client.post(f"{API}/campaigns/{w['cid']}/members/me/backstory-analysis", headers=w["pl"]).json()
    x = dbs.get(CharacterAnalysis, a["id"])
    x.state, x.message = "failed", status_message("doc.failed", detail="Traceback: /srv/geheim/pfad.py")
    dbs.commit()
    spieler = client.get(f"{API}/campaigns/{w['cid']}/character-analyses", headers=w["pl"]).json()[0]["message"]
    sl = client.get(f"{API}/campaigns/{w['cid']}/character-analyses", headers=w["gm"]).json()[0]["message"]
    assert "Traceback" not in spieler and "noch einmal abgeben" in spieler
    assert "Traceback" not in sl and "Die Spielerin kann" in sl  # YAML 0.4.18 (Nachtrag): nur feste Sätze, auch für die SL
    text = "Siehe foo.xyz/x, mail@example.test, ftp://a.b/c und www.c.de sowie Grauwacht.Dort bleibt."
    rein = charakterauswertung._ohne_links(text)
    assert not re.search(r"foo\.xyz|@|ftp|www", rein) and "Grauwacht.Dort" in rein


# ---------------------------------------------------------------- S29
def test_s29_zwoelf_ziffern_alte_codes_gelten_weiter(client, world, dbs, make_user, login):
    from datetime import timedelta

    from app.db import utcnow
    from app.models import Invite, User

    w = world
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    assert re.fullmatch(r"[A-Z]+-\d{12}", code)
    anna = dbs.scalar(select(User).where(User.username == "anna"))
    dbs.add(Invite(code="RABE-12345678", campaign_id=w["cid"], created_by=anna.id,
                   expires_at=utcnow() + timedelta(days=3)))
    dbs.commit()
    make_user("dora")
    r = client.post(f"{API}/campaigns/join", json={"code": "rabe-12345678", "characterName": "Ilva"},
                    headers=login("dora"))
    assert r.status_code == 200, r.text


# ---------------------------------------------------------------- Abrechnung
def _kostenauftrag(dbs, weg="extern", art="transcribe", schaetzung=40, zustand_job="leased"):
    from app.models import Job, KostenAuftrag

    g = _gutschreiben(dbs, _anna(dbs), 1000)
    job = Job(type=art, required_capability="asr", state=zustand_job)
    dbs.add(job)
    dbs.flush()
    k = KostenAuftrag(job_id=job.id, guthaben_id=g.id, weg=weg, art=art, menge=600, schaetzung_cent=schaetzung)
    dbs.add(k)
    dbs.commit()
    return g, job, k


def test_m1_cloud_geloescht_oder_gescheitert_kostet_mindestens_die_schaetzung(client, world, dbs):
    from app import abrechnung

    _modus(dbs)
    g, job, k = _kostenauftrag(dbs)
    dbs.delete(job)
    dbs.commit()
    assert abrechnung.abrechnen(dbs, k) == 40
    dbs.commit()
    assert abrechnung.saldo(dbs, g.id) == 960


def test_m1_teilverbrauch_wird_festgehalten(client, world, dbs, tmp_path):
    from app import extern
    from app.models import Job, UsageLog

    w = world
    s = neue_session(client, w)
    job = Job(type="transcribe", session_id=s["id"], required_capability="asr", state="leased", attempts=1)
    dbs.add(job)
    dbs.commit()
    k = type("K", (), {"cent_pro_minute": 6.0})()
    extern._gescheitert(dbs, job.id, w["cid"], s["id"], 300.0, k, "external_error", "kaputt", False)
    log = dbs.scalar(select(UsageLog).where(UsageLog.session_id == s["id"]))
    assert log.engine == "external" and log.cost_cents == 30
    dbs.expire_all()
    assert dbs.get(Job, job.id).state == "failed"
    extern._gescheitert(dbs, "weg", w["cid"], s["id"], 0.0, k, "x", "y", False)  # Auftrag weg: kein Absturz


def test_l1_abgeloest_kostet_wie_begonnen_und_l2_nur_einmal(client, world, dbs):
    from app import abrechnung
    from app.models import Buchung

    _modus(dbs, worker_transkription_h=600)
    g, job, k = _kostenauftrag(dbs, weg="worker", zustand_job="failed")
    job.error_code = "superseded"
    dbs.commit()
    assert abrechnung.abrechnen(dbs, k) == 100  # 10 Minuten zu 6 €/h
    dbs.commit()
    k.zustand = "laeuft"  # wie ein zweiter Prozess mit altem Stand
    from sqlalchemy.orm.attributes import set_committed_value

    set_committed_value(k, "zustand", "laeuft")
    assert abrechnung.abrechnen(dbs, k) == 0
    dbs.commit()
    assert dbs.query(Buchung).filter(Buchung.art == "verbrauch").count() == 1 and abrechnung.saldo(dbs, g.id) == 900


def test_m2_laenge_nach_dekodieren_und_nachtragen(client, world, dbs, tmp_path, monkeypatch):
    import subprocess

    from app import abrechnung, audio

    datei = tmp_path / "a.ogg"
    datei.write_bytes(b"\0" * 2_000_000)
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"1.0\n", b""))
    monkeypatch.setattr(audio, "dauer_dekodiert", lambda d: 36000.0)
    assert audio.dauer_datei(datei) == 36000.0  # Kopf sagt 1 s bei 2 MB → dekodiert gemessen
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"100.0\n", b""))
    assert audio.dauer_datei(datei) == 100.0  # 20 kB/s: glaubwürdig
    _modus(dbs)
    _, job, k = _kostenauftrag(dbs, weg="worker")
    abrechnung.menge_nachtragen(dbs, job, 3600.4)
    assert k.menge == 3601
    abrechnung.menge_nachtragen(dbs, job, 10)
    assert k.menge == 3601  # nie weniger


def test_n3_token_preise_nur_mit_zweitem_faktor(client, dbs, admin):  # noqa: F811
    from app.einstellungen import meta_lesen

    _modus(dbs)
    basis = {"csrf": admin, "art": "api", "anbieter": "mistral", "lokal_modell": "auto", "lokal_kontext": "12288",
             "api_key": "sk-abcdefghijklmnop1234", "api_modell": "mistral-medium-latest"}
    r = client.post("/verwaltung/zusammenfassung", data={**basis, "cent_ein": "1", "cent_aus": "2"})
    assert r.status_code == 403 and "zweiten Faktor" in r.text and not meta_lesen(dbs, "llm.cent_ein")
    r = client.post("/verwaltung/zusammenfassung", data=basis, follow_redirects=False)
    assert r.status_code == 303  # ohne Preisänderung wie bisher


def test_zweiter_faktor_beendet_sitzungen_auf_guthaben(client, world, dbs):
    import time

    from app.einstellungen import meta_schreiben
    from app.models import User

    _modus(dbs)
    _sl_anmelden(client)
    assert "Benutzername" not in client.get("/guthaben").text
    anna = dbs.scalar(select(User).where(User.username == "anna"))
    meta_schreiben(dbs, f"verwaltung.abgemeldet.{anna.id}", str(int(time.time()) + 5))
    dbs.commit()
    assert "Benutzername" in client.get("/guthaben").text


# ---------------------------------------------------------------- S21.4- und S21.5-Rest
@pytest.mark.usefixtures("_kleine_teile")
def test_s21_4_wortkorrektur_zaehlt_als_bearbeitung(client, world, dbs, tmp_path):
    from app import unsicher
    from app.models import GameSession, Recap
    from tests.test_pruefung_046 import _zur_pruefung

    w = world
    s = _zur_pruefung(client, w, dbs, tmp_path)
    r = dbs.get(Recap, s["id"])
    wort = r.text.split()[0].strip(".,")
    r.edited_at = None
    dbs.commit()
    unsicher.ersetzen(dbs, dbs.get(GameSession, s["id"]), [(wort, "Ersetzt")])
    dbs.commit()
    assert dbs.get(Recap, s["id"]).edited_at is not None


def test_s21_5_kein_rueckfall_als_root():
    skript = Path("deploy/aktualisieren.sh").read_text(encoding="utf-8")
    assert 'mv -fT "$1.neu" "$1"' in skript and 'mv -fT "$tmp" "$STATUS"' in skript
    teil = skript.split("melden() {", 1)[1].split("\n}\n", 1)[0]
    assert teil.index("ordner_ohne_container") < teil.index("mktemp")  # Rückfall als root nur in root-Ordnern
    pruefung = skript.split("ordner_ohne_container() {", 1)[1].split("\n}\n", 1)[0]
    assert 'stat -c %u "$ORDNER"' in pruefung and "command -v setpriv" in pruefung and '"$(id -u)" != 0' in pruefung
