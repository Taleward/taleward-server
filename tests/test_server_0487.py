"""Server 0.4.87: Abrechnung nach der Prüfung von 0.4.85 (N1–N6), Einrichtung im Heimnetz (S6/S21.3), Passwort bei der
Einrichtung (S21.2), Statusdatei im Update-Skript (S21.5)."""
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from tests.test_abrechnung_0485 import _anna, _gutschreiben, _kleine_teile, _knecht, _modus, _wartende_runde  # noqa: F401
from tests.test_server_0484 import _code
from tests.test_step2a import API, audio_abschnitte, hochladen, neue_session, worker_starten, worker_token
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

pytestmark = pytest.mark.usefixtures("_kleine_teile")


# ---------------------------------------------------------------- N1: abrechnen beim Abschluss und vor dem Löschen
def test_n1_abgerechnet_im_selben_schritt_wie_der_abschluss(client, world, dbs, tmp_path):
    from app.models import Buchung, KostenAuftrag

    _modus(dbs, worker_transkription_h=600)
    g = _gutschreiben(dbs, _anna(dbs), 300)
    s = _wartende_runde(client, world, dbs, tmp_path, (45,))
    assert worker_starten(client, worker_token(dbs), tmp_path).einen_auftrag()
    dbs.expire_all()
    k = dbs.scalar(select(KostenAuftrag))
    assert k.zustand == "abgerechnet" and k.betrag_cent == 8 and k.session_id == s["id"]  # ohne Wartung
    assert dbs.scalar(select(Buchung).where(Buchung.art == "verbrauch")).betrag_cent == -8
    # Runde gleich danach löschen: gebucht bleibt gebucht
    assert client.delete(f"{API}/sessions/{s['id']}", headers=world["gm"]).status_code == 204
    from app import abrechnung

    dbs.expire_all()
    assert abrechnung.saldo(dbs, g.id) == 292


def test_n1_laufender_auftrag_wird_vor_dem_loeschen_gebucht(client, world, dbs, tmp_path):
    from app import abrechnung, queue
    from app.models import KostenAuftrag

    _modus(dbs, worker_transkription_h=600)
    g = _gutschreiben(dbs, _anna(dbs), 300)
    s = _wartende_runde(client, world, dbs, tmp_path, (45,))
    job = queue.claim(dbs, _knecht(dbs), ["asr"])
    assert job is not None and job.session_id == s["id"]
    job_id = job.id
    assert client.delete(f"{API}/sessions/{s['id']}", headers=world["gm"]).status_code == 204
    dbs.expire_all()
    k = dbs.get(KostenAuftrag, job_id)
    assert k.zustand == "abgerechnet" and k.betrag_cent == 8  # begonnen ist begonnen
    assert abrechnung.saldo(dbs, g.id) == 292


def test_n1_cloud_kosten_auch_ohne_auftrag(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.models import Job, KostenAuftrag, UsageLog

    _modus(dbs, aufschlag_prozent=20)
    g = _gutschreiben(dbs, _anna(dbs), 1000)
    s = _wartende_runde(client, world, dbs, tmp_path)
    job = dbs.scalar(select(Job).where(Job.session_id == s["id"]))
    job_id = job.id
    assert abrechnung.starten(dbs, job, "extern") is None
    dbs.commit()
    dbs.add(UsageLog(campaign_id=world["cid"], session_id=s["id"], kind="transcription", engine="external",
                     cost_cents=7))
    dbs.commit()
    assert client.delete(f"{API}/sessions/{s['id']}", headers=world["gm"]).status_code == 204
    dbs.expire_all()
    assert dbs.get(Job, job_id) is None
    assert dbs.get(KostenAuftrag, job_id).betrag_cent == 9 and abrechnung.saldo(dbs, g.id) == 991


def test_n1_kampagne_loeschen_bucht_vorher(client, world, dbs, tmp_path):
    from app import abrechnung, queue
    from app.models import KostenAuftrag

    _modus(dbs, worker_transkription_h=600)
    g = _gutschreiben(dbs, _anna(dbs), 300)
    _wartende_runde(client, world, dbs, tmp_path, (45,))
    job_id = queue.claim(dbs, _knecht(dbs), ["asr"]).id
    r = client.request("DELETE", f"{API}/campaigns/{world['cid']}", headers=world["gm"],
                       json={"confirmTitle": "Rabenfels"})
    assert r.status_code == 204
    dbs.expire_all()
    assert dbs.get(KostenAuftrag, job_id).zustand == "abgerechnet" and abrechnung.saldo(dbs, g.id) == 292


def test_n1_gescheitert_kostet_beim_worker_nichts_und_wird_sofort_gebucht(client, world, dbs, tmp_path):
    from app import abrechnung, queue
    from app.models import Job, KostenAuftrag

    _modus(dbs, worker_transkription_h=10000)
    g = _gutschreiben(dbs, _anna(dbs), 1000)
    _wartende_runde(client, world, dbs, tmp_path)
    job = queue.claim(dbs, _knecht(dbs), ["asr"])
    queue.fail_job(dbs, dbs.get(Job, job.id), "x", "kaputt", retryable=False)
    dbs.commit()
    dbs.expire_all()
    assert dbs.get(KostenAuftrag, job.id).zustand == "abgerechnet" and abrechnung.saldo(dbs, g.id) == 1000


# ---------------------------------------------------------------- N2: /guthaben mit zweitem Faktor
def test_n2_guthaben_verlangt_den_code(client, world, dbs, admin):  # noqa: F811
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf

    _modus(dbs, "topf")
    chef = dbs.scalar(select(User).where(User.username == "chef"))
    zf.beginnen(dbs, chef.id)
    codes = zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))  # der Code dieses Zeitfensters ist damit verbraucht
    dbs.commit()
    client.cookies.clear()
    r = client.post("/guthaben/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 200 and "Code aus deiner Authenticator-App" in r.text
    assert 'action="/guthaben/anmelden/code"' in r.text and "tw_guthaben=" not in r.headers.get("set-cookie", "")
    assert "Benutzername" in client.get("/guthaben").text  # noch nicht angemeldet
    r = client.post("/guthaben/anmelden/code", data={"code": "000000"})
    assert r.status_code == 400
    r = client.post("/guthaben/anmelden/code", data={"code": codes[0]}, follow_redirects=False)
    assert r.status_code == 303 and "tw_guthaben=" in r.headers["set-cookie"]
    assert "Benutzername" not in client.get("/guthaben").text
    # Ohne Code-Cookie führt der zweite Schritt nirgends hin
    client.cookies.clear()
    r = client.post("/guthaben/anmelden/code", data={"code": codes[1]}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/guthaben"
    # Konten ohne zweiten Faktor (SL) wie bisher nur mit Passwort
    r = client.post("/guthaben/anmelden", data={"username": "anna", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303 and "tw_guthaben=" in r.headers["set-cookie"]


# ---------------------------------------------------------------- N3: Schalter und Preise nur mit zweitem Faktor
def test_n3_schalter_und_preise_nur_mit_zweitem_faktor(client, dbs, admin):  # noqa: F811
    from app import abrechnung
    from app.models import User
    from app.verwaltung import abrechnung_seite
    from app.verwaltung import zweiter_faktor as zf

    seite = client.get("/verwaltung/abrechnung").text
    assert "Zweiten Faktor einrichten" in seite
    r = client.post("/verwaltung/abrechnung/modus", data={"csrf": admin, "modus": "guthaben"})
    assert r.status_code == 403 and abrechnung.modus(dbs) == "aus"
    felder = {name: "0" for name, _t, _e in abrechnung_seite.PREISFELDER}
    r = client.post("/verwaltung/abrechnung/preise", data={**felder, "csrf": admin, "aufschlag_prozent": "90"})
    assert r.status_code == 403 and abrechnung.preise(dbs).aufschlag_prozent == 0
    chef = dbs.scalar(select(User).where(User.username == "chef"))
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    zf.frisch_merken(dbs, chef.id)  # 0.4.143 (S44)
    dbs.commit()
    r = client.post("/verwaltung/abrechnung/modus", data={"csrf": admin, "modus": "guthaben"}, follow_redirects=False)
    assert r.status_code == 303 and abrechnung.modus(dbs) == "guthaben"
    zf.ausschalten(dbs, chef.id)
    dbs.commit()
    # Ausschalten geht immer (Notbremse)
    r = client.post("/verwaltung/abrechnung/modus", data={"csrf": admin, "modus": "aus"}, follow_redirects=False)
    assert r.status_code == 303 and abrechnung.modus(dbs) == "aus"


# ---------------------------------------------------------------- N4: Länge der Aufnahme
def test_n4_nicht_messbar_zaehlt_das_groessere(client, world, dbs, tmp_path, monkeypatch):
    from app import abrechnung, storage
    from app.models import GameSession, Upload

    monkeypatch.setattr(abrechnung, "aufnahme_messen", lambda up: None)
    s = neue_session(client, world)
    hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (20,)))  # vor dem Einschalten hochgeladen
    _modus(dbs)
    groesse = abrechnung.aufnahme_sekunden(dbs, s["id"])
    assert groesse > 0
    dbs.get(GameSession, s["id"]).duration_seconds = 1  # SL meldet zu wenig
    dbs.commit()
    assert abrechnung.aufnahme_sekunden(dbs, s["id"]) == groesse
    dbs.get(GameSession, s["id"]).duration_seconds = groesse + 500
    dbs.commit()
    assert abrechnung.aufnahme_sekunden(dbs, s["id"]) == groesse + 500
    # 0.4.99 (M3): Mit Abrechnung wird eine nicht messbare Aufnahme gar nicht erst angenommen
    _gutschreiben(dbs, _anna(dbs), 300)  # 0.4.129: ohne Guthaben wartet sonst schon die erste Aufnahme
    s2 = neue_session(client, world)
    up = hochladen(client, world["gm"], s2["id"], audio_abschnitte(tmp_path, (5,)), abschliessen=False)
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=world["gm"])
    assert r.status_code == 409 and r.json()["code"] == "recording_unreadable"
    dbs.expire_all()
    assert dbs.get(Upload, up["uploadId"]).state == "aborted"
    assert not storage.upload_dir(up["uploadId"]).exists()


# ---------------------------------------------------------------- N5: ein Gutschein je Aktion in der Datenbank
def test_n5_ein_gutschein_je_aktion_im_index(client, world, dbs):
    from app import abrechnung

    _modus(dbs)
    codes = abrechnung.gutscheine_erzeugen(dbs, 2, 100, "Verlosung", None, "test")
    dbs.commit()
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    dbs.commit()
    abrechnung.gutschein_einloesen(dbs, g, codes[0], "x")
    dbs.commit()
    with pytest.raises(IntegrityError):  # auch an der Zählung vorbei nicht möglich
        dbs.execute(text("UPDATE gutscheine SET eingeloest_von = :g, eingeloest_at = CURRENT_TIMESTAMP "
                         "WHERE eingeloest_von IS NULL"), {"g": g.id})
    dbs.rollback()
    with pytest.raises(ValueError, match="aktion"):
        abrechnung.gutschein_einloesen(dbs, g, codes[1], "x")


def test_n5_gleichzeitige_einloesung_endet_als_aktion(client, world, dbs, monkeypatch):
    """Die Zählung hat (gleichzeitig) noch nichts gesehen – der Index fängt es, die Meldung bleibt dieselbe."""
    from app import abrechnung

    _modus(dbs)
    codes = abrechnung.gutscheine_erzeugen(dbs, 2, 100, "Verlosung", None, "test")
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    dbs.commit()
    abrechnung.gutschein_einloesen(dbs, g, codes[0], "x")
    dbs.commit()
    echt = dbs.scalar
    monkeypatch.setattr(dbs, "scalar", lambda q, *a, **k: 0 if "count" in str(q).lower() else echt(q, *a, **k))
    with pytest.raises(ValueError, match="aktion"):
        abrechnung.gutschein_einloesen(dbs, g, codes[1], "x")
    monkeypatch.undo()
    assert abrechnung.saldo(dbs, g.id) == 100


# ---------------------------------------------------------------- N6: Hinweis ohne Cloud-Schätzwerte
def test_n6_hinweis_ohne_cloud_schaetzwerte(client, dbs, admin, monkeypatch):  # noqa: F811
    from app import abrechnung

    # 0.4.93: ohne Läufe schätzt der Server aus den Preisen des Anbieters – hier ein Anbieter ohne bekannte Preise
    monkeypatch.setattr(abrechnung, "preis_aus_anbieter", lambda db, art: 0.0)
    seite = client.get("/verwaltung/abrechnung").text
    assert "Für die Cloud fehlen noch Schätzwerte" in seite and "Transkription" in seite
    p = abrechnung.preise(dbs)
    p.cloud_transkription_h = p.cloud_kapitel_h = p.cloud_korrektur = p.cloud_unterlage_seite = 50
    abrechnung.preise_schreiben(dbs, p, "test")
    dbs.commit()
    assert "Für die Cloud fehlen noch Schätzwerte" not in client.get("/verwaltung/abrechnung").text


# ---------------------------------------------------------------- S6 / S21.3, S21.2, S21.5
def _anfrage(host):
    return SimpleNamespace(client=SimpleNamespace(host=host))


def test_s6_frist_ab_dem_ersten_start(dbs, monkeypatch):
    from app import config, einrichtung
    from app.einstellungen import meta_lesen, meta_schreiben

    monkeypatch.setenv("EINRICHTUNG_IM_HEIMNETZ", "true")
    config.get_settings.cache_clear()
    dbs.execute(text("DELETE FROM server_meta WHERE key = :k"), {"k": einrichtung.ERSTER_START})
    dbs.commit()
    try:
        assert not einrichtung.heimnetz_ohne_code(_anfrage("192.168.178.20"), dbs)  # kein erster Start bekannt
        einrichtung.beim_start(dbs)
        erster = meta_lesen(dbs, einrichtung.ERSTER_START)
        assert erster and einrichtung.heimnetz_ohne_code(_anfrage("192.168.178.20"), dbs)
        einrichtung.beim_start(dbs)  # Neustart: der erste Start bleibt
        assert meta_lesen(dbs, einrichtung.ERSTER_START) == erster
        meta_schreiben(dbs, einrichtung.ERSTER_START, str(time.time() - einrichtung.HEIMNETZ_FRIST_S - 1))
        dbs.commit()
        assert not einrichtung.heimnetz_ohne_code(_anfrage("192.168.178.20"), dbs)  # auch nach Neustart nur mit Code
        einrichtung.beim_start(dbs)
        assert not einrichtung.heimnetz_ohne_code(_anfrage("192.168.178.20"), dbs)
    finally:
        config.get_settings.cache_clear()


def test_s21_2_einrichtung_prueft_passwort_wie_konten(client, dbs):
    from app import einrichtung

    code = einrichtung.beim_start(dbs)
    body = {"username": "markus", "display_name": "Markus", "password": "aaaaaaaaaa", "password2": "aaaaaaaaaa",
            "code": code}
    r = client.post("/verwaltung/einrichtung", data=body)
    assert r.status_code == 400 and "zu leicht" in r.text


def test_s21_5_statusdatei_mit_rechten_des_containers():
    skript = Path("deploy/aktualisieren.sh").read_text(encoding="utf-8")
    assert "setpriv --reuid=1000 --regid=1000 --clear-groups" in skript
    assert 'als_container sh -c' in skript and 'als_container rm -f "$ORDNER/server-jetzt"' in skript
