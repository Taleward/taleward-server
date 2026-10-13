"""Server 0.4.85: Abrechnung mit Guthaben (S28 G1–G3, G5, Gutschrift von Hand, Gutscheine; ohne Zahlungsanbieter)."""
import io
import re
import threading
import time

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError, OperationalError

from tests.test_step2a import API, audio_abschnitte, hochladen, neue_session, status, worker_starten, worker_token
from tests.test_verwaltung import admin, csrf_von  # noqa: F401 (Fixture)

pytestmark = pytest.mark.usefixtures("_kleine_teile")


@pytest.fixture()
def _kleine_teile(monkeypatch):
    monkeypatch.setenv("CHUNK_SIZE_BYTES", str(64 * 1024))


def _modus(dbs, wert="guthaben", **preise):
    from app import abrechnung

    abrechnung.modus_schreiben(dbs, wert, "test")
    if preise:
        p = abrechnung.preise(dbs)
        for k, v in preise.items():
            setattr(p, k, v)
        abrechnung.preise_schreiben(dbs, p, "test")
    dbs.commit()


def _anna(dbs):
    from app.models import User

    return dbs.scalar(select(User).where(User.username == "anna"))


def _gutschreiben(dbs, user, cent):
    from app import abrechnung

    g = abrechnung.guthaben_konto(dbs, user.id)
    abrechnung.buchen(dbs, g, "gutschrift", cent, grund="Test")
    dbs.commit()
    return g


def _wartende_runde(client, w, dbs, tmp_path, sekunden=(30,)):
    s = neue_session(client, w)
    hochladen(client, w["gm"], s["id"], audio_abschnitte(tmp_path, sekunden))
    return s


def _knecht(dbs):
    from app.models import Worker

    k = Worker(name="knecht", token_hash="x", capabilities="asr")
    dbs.add(k)
    dbs.commit()
    return k


# ---------------------------------------------------------------- G1 Buchungsbuch
def test_buchungen_lassen_sich_nicht_aendern_oder_loeschen(client, world, dbs):
    _modus(dbs)
    g = _gutschreiben(dbs, _anna(dbs), 500)
    from app import abrechnung

    assert abrechnung.saldo(dbs, g.id) == 500
    for befehl in ("UPDATE buchungen SET betrag_cent = 99999", "DELETE FROM buchungen"):
        with pytest.raises((IntegrityError, OperationalError)):
            dbs.execute(text(befehl))
        dbs.rollback()
    assert abrechnung.saldo(dbs, g.id) == 500
    with pytest.raises((IntegrityError, OperationalError)):
        dbs.execute(text("DELETE FROM abrechnung_protokoll"))
    dbs.rollback()


def test_hoechstens_ein_laufender_auftrag_je_guthaben_in_der_datenbank(client, world, dbs):
    from app.models import KostenAuftrag

    _modus(dbs)
    g = _gutschreiben(dbs, _anna(dbs), 100)
    dbs.add(KostenAuftrag(job_id="a", guthaben_id=g.id, weg="worker", art="transcribe", zustand="laeuft"))
    dbs.commit()
    dbs.add(KostenAuftrag(job_id="b", guthaben_id=g.id, weg="worker", art="transcribe", zustand="laeuft"))
    with pytest.raises(IntegrityError):
        dbs.commit()
    dbs.rollback()
    dbs.add(KostenAuftrag(job_id="c", guthaben_id=g.id, weg="worker", art="transcribe", zustand="abgerechnet"))
    dbs.commit()


# ---------------------------------------------------------------- G5 Schalter aus
def test_schalter_aus_alles_wie_bisher(client, world, dbs, tmp_path):
    from app import queue
    from app.models import KostenAuftrag

    s = _wartende_runde(client, world, dbs, tmp_path)
    assert client.get("/guthaben").status_code == 404
    assert client.post("/guthaben/anmelden", data={"username": "anna", "password": "geheim123"}).status_code == 404
    job = queue.claim(dbs, _knecht(dbs), ["asr"])
    assert job is not None and job.session_id == s["id"]
    assert dbs.scalar(select(KostenAuftrag)) is None


# ---------------------------------------------------------------- G2 Startprüfung
def test_leeres_guthaben_wartet_und_startet_nach_gutschrift(client, world, dbs, tmp_path):
    from app import abrechnung, queue
    from app.models import Job, KostenAuftrag

    _modus(dbs, worker_transkription_h=120, aufschlag_prozent=10)  # 1,20 € je Stunde Aufnahme
    s = _wartende_runde(client, world, dbs, tmp_path, (30, 30))
    knecht = _knecht(dbs)
    assert queue.claim(dbs, knecht, ["asr"]) is None
    st = status(client, world["gm"], s["id"])
    assert "Guthaben ist aufgebraucht" in st["message"] and "/guthaben" in st["message"]
    assert "credit is used up" in status(client, world["gm"], s["id"], "en")["message"]
    g = _gutschreiben(dbs, _anna(dbs), 5)
    job = queue.claim(dbs, knecht, ["asr"])
    assert job is not None
    k = dbs.get(KostenAuftrag, job.id)
    assert k.zustand == "laeuft" and k.guthaben_id == g.id and 55 <= k.menge <= 65  # vom Server gemessen (60 s)
    # Fertig → im selben Schritt abgerechnet: 60 s × 1,20 €/h = 2 ct, + 10 % → aufgerundet 3 ct
    dbs.get(Job, job.id).state = "done"
    dbs.commit()
    assert abrechnung.offene_abrechnen(dbs) == 0  # seit 0.4.87 schon beim Abschluss gebucht
    assert abrechnung.saldo(dbs, g.id) == 5 - abrechnung.betrag_fuer(dbs, "transcribe", "worker", k.menge)
    assert dbs.get(KostenAuftrag, job.id).zustand == "abgerechnet"


def test_deckel_und_freigabe(client, world, dbs, tmp_path):
    from app import abrechnung, queue
    from app.models import Job

    _modus(dbs, worker_transkription_h=36000)  # absichtlich teuer: 60 s ≈ 6 €
    s = _wartende_runde(client, world, dbs, tmp_path, (30, 30))
    knecht = _knecht(dbs)
    g = _gutschreiben(dbs, _anna(dbs), 1)
    job = dbs.scalar(select(Job).where(Job.session_id == s["id"]))
    assert abrechnung.warte_grund(dbs, job) == "freigabe"  # über 2 € → wartet auf „Verarbeiten“
    assert "mit „Verarbeiten“" in status(client, world["gm"], s["id"])["message"]
    abrechnung.freigeben(dbs, job, _anna(dbs).id, g)
    dbs.commit()
    assert abrechnung.warte_grund(dbs, job) == "zu_knapp"  # 1 ct − 6 € liegt unter dem Deckel von −1,50 €
    assert queue.claim(dbs, knecht, ["asr"]) is None
    abrechnung.buchen(dbs, g, "gutschrift", 500)
    dbs.commit()
    assert abrechnung.warte_grund(dbs, job) is None
    assert queue.claim(dbs, knecht, ["asr"]) is not None  # 5,01 € − 6 € ≥ −1,50 €


def test_zwei_auftraege_desselben_guthabens_nacheinander(client, world, dbs, tmp_path):
    from app import queue
    from app.models import Job

    _modus(dbs)
    _gutschreiben(dbs, _anna(dbs), 1000)
    a = _wartende_runde(client, world, dbs, tmp_path)
    b = _wartende_runde(client, world, dbs, tmp_path)
    k1, k2 = _knecht(dbs), _knecht(dbs)
    erster = queue.claim(dbs, k1, ["asr"])
    assert erster.session_id == a["id"]
    assert queue.claim(dbs, k2, ["asr"]) is None  # derselbe Zahler hat schon einen laufenden Auftrag
    # Automatische Wiederholung gehört zum selben Kostenauftrag
    queue.fail_job(dbs, dbs.get(Job, erster.id), "x", "kurz weg", retryable=True)
    dbs.commit()
    assert queue.claim(dbs, k1, ["asr"]).id == erster.id
    dbs.get(Job, erster.id).state = "done"
    dbs.commit()
    assert queue.claim(dbs, k2, ["asr"]).session_id == b["id"]


def test_gleichzeitige_starts_bei_einem_cent(client, world, dbs, tmp_path):
    """G13: zwei gleichzeitige Starts bei 1 Cent → nur einer läuft."""
    from app import queue
    from app.db import session_factory

    _modus(dbs)
    _gutschreiben(dbs, _anna(dbs), 1)
    _wartende_runde(client, world, dbs, tmp_path)
    _wartende_runde(client, world, dbs, tmp_path)
    knechte = [_knecht(dbs).id, _knecht(dbs).id]
    ergebnisse = []

    def holen(kid):
        from app.models import Worker

        with session_factory()() as eigene:
            try:
                ergebnisse.append(queue.claim(eigene, eigene.get(Worker, kid), ["asr"]))
            except Exception as e:  # noqa: BLE001 – gesperrt zählt wie „nichts bekommen“
                ergebnisse.append(e)

    faeden = [threading.Thread(target=holen, args=(k,)) for k in knechte]
    for f in faeden:
        f.start()
    for f in faeden:
        f.join(30)
    assert sum(1 for e in ergebnisse if e is not None and not isinstance(e, Exception)) == 1


def test_cloud_kosten_mit_aufschlag(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.models import Job, KostenAuftrag, UsageLog

    _modus(dbs, aufschlag_prozent=20)
    g = _gutschreiben(dbs, _anna(dbs), 1000)
    s = _wartende_runde(client, world, dbs, tmp_path)
    job = dbs.scalar(select(Job).where(Job.session_id == s["id"]))
    assert abrechnung.starten(dbs, job, "extern") is None
    dbs.commit()
    dbs.add(UsageLog(campaign_id=world["cid"], session_id=s["id"], kind="transcription", engine="external",
                     cost_cents=7))
    dbs.add(UsageLog(campaign_id=world["cid"], session_id=s["id"], kind="probe", engine="external", cost_cents=500))
    dbs.get(Job, job.id).state = "done"
    dbs.commit()
    abrechnung.offene_abrechnen(dbs)
    assert dbs.get(KostenAuftrag, job.id).betrag_cent == 9  # 7 ct + 20 % = 8,4 → 9; Probelauf zählt nicht
    assert abrechnung.saldo(dbs, g.id) == 991


def test_testmodus_und_gescheiterter_worker_kosten_nichts(client, world, dbs, tmp_path):
    from app import abrechnung, queue
    from app.models import Job, KostenAuftrag

    _modus(dbs, worker_transkription_h=10000)
    g = _gutschreiben(dbs, _anna(dbs), 1000)
    s = _wartende_runde(client, world, dbs, tmp_path)
    job = queue.claim(dbs, _knecht(dbs), ["asr"])
    assert job.session_id == s["id"]
    queue.fail_job(dbs, dbs.get(Job, job.id), "x", "kaputt", retryable=False)
    dbs.commit()
    abrechnung.offene_abrechnen(dbs)
    assert dbs.get(KostenAuftrag, job.id).betrag_cent == 0 and abrechnung.saldo(dbs, g.id) == 1000


def test_ende_zu_ende_mit_worker(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.models import KostenAuftrag

    _modus(dbs, worker_transkription_h=600)
    g = _gutschreiben(dbs, _anna(dbs), 300)
    s = _wartende_runde(client, world, dbs, tmp_path, (45,))
    assert worker_starten(client, worker_token(dbs), tmp_path).einen_auftrag()
    dbs.expire_all()
    abrechnung.offene_abrechnen(dbs)
    k = dbs.scalar(select(KostenAuftrag))
    assert k.zustand == "abgerechnet" and k.betrag_cent == 8  # 45 s × 6 €/h = 7,5 ct → 8 ct
    assert abrechnung.saldo(dbs, g.id) == 292
    assert client.get(f"{API}/sessions/{s['id']}/status", headers=world["gm"]).json()["state"] != "queued"


# ---------------------------------------------------------------- G3 Wer zahlt
def test_zahlende_sl_abgeben_und_uebernehmen(client, world, dbs, make_user, login):
    from app import abrechnung
    from app.models import Campaign, Member, User

    _modus(dbs)
    make_user("dora")
    dora = dbs.scalar(select(User).where(User.username == "dora"))
    dbs.add(Member(campaign_id=world["cid"], user_id=dora.id, role="gm"))
    dbs.commit()
    c = dbs.get(Campaign, world["cid"])
    anna = _anna(dbs)
    assert abrechnung.zahler_user_id(dbs, c) == anna.id  # wer angelegt hat
    with pytest.raises(ValueError):
        abrechnung.zahlung_uebernehmen(dbs, c, dora.id)  # ohne Angebot nicht
    ben = dbs.scalar(select(User).where(User.username == "ben"))
    with pytest.raises(ValueError):
        abrechnung.zahlung_anbieten(dbs, c, anna.id, ben.id)  # Spieler zahlen nie
    abrechnung.zahlung_anbieten(dbs, c, anna.id, dora.id)
    abrechnung.zahlung_uebernehmen(dbs, c, dora.id)
    dbs.commit()
    assert abrechnung.zahler_user_id(dbs, c) == dora.id
    # Zahlende SL tritt aus → niemand zahlt, bis eine andere SL übernimmt
    dbs.scalar(select(Member).where(Member.user_id == dora.id)).left_at = __import__("app.db").db.utcnow()
    dbs.commit()
    assert abrechnung.zahler_user_id(dbs, c) is None
    abrechnung.zahlung_uebernehmen(dbs, c, anna.id)
    dbs.commit()
    assert abrechnung.zahler_user_id(dbs, c) == anna.id


def test_topf_zahlt_die_organisation(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.models import Campaign, Job

    _modus(dbs, "topf")
    c = dbs.get(Campaign, world["cid"])
    assert c.organization_id
    s = _wartende_runde(client, world, dbs, tmp_path)
    job = dbs.scalar(select(Job).where(Job.session_id == s["id"]))
    g = abrechnung.zahler(dbs, c)
    assert g.art == "organisation" and g.id is None and abrechnung.warte_grund(dbs, job) == "leer"
    assert "Guthaben der Organisation" in status(client, world["gm"], s["id"])["message"]
    # Die SL ist nicht Verwalterin – sie sieht den Topf nicht
    assert not abrechnung.darf_sehen(dbs, _anna(dbs).id, g)


# ---------------------------------------------------------------- Grenzen
def _pdf(seiten: int) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for _ in range(seiten):
        w.add_blank_page(100, 100)
    puffer = io.BytesIO()
    w.write(puffer)
    return puffer.getvalue()


def test_grenzen_fuer_charakterboegen(client, world, dbs):
    def bogen(h, daten, name="bogen.pdf"):
        return client.post(f"{API}/campaigns/{world['cid']}/documents", headers=h,
                           files={"file": (name, daten, "application/pdf")}, data={"kind": "character_sheet"})

    assert bogen(world["pl"], _pdf(25)).status_code == 201  # Schalter aus: wie bisher
    _modus(dbs)
    r = bogen(world["pl"], _pdf(21))
    assert r.status_code == 400 and r.json()["code"] == "sheet_too_many_pages"
    assert "höchstens 20" in r.json()["message"]
    assert bogen(world["pl"], _pdf(20)).status_code == 201
    assert bogen(world["pl"], _pdf(1)).status_code == 201
    r = bogen(world["pl"], _pdf(1))  # schon 3 Bögen (einer von vorher)
    assert r.status_code == 400 and r.json()["code"] == "sheet_limit_reached"
    assert bogen(world["gm"], _pdf(30)).status_code == 201  # die SL ist nicht begrenzt


def test_aufnahme_ueber_acht_stunden(client, world, dbs, tmp_path, monkeypatch):
    from app import abrechnung

    _modus(dbs)
    s = neue_session(client, world)
    monkeypatch.setattr(abrechnung, "aufnahme_messen", lambda up: 9 * 3600.0)
    up = hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (5,)), abschliessen=False)
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=world["gm"])
    assert r.status_code == 409 and r.json()["code"] == "recording_too_long"
    assert client.get(f"{API}/sessions/{s['id']}", headers=world["gm"]).json()["state"] == "created"


def test_laenge_selbst_gemessen(client, world, dbs, tmp_path):
    from app.models import Upload

    _modus(dbs)
    s = neue_session(client, world)
    hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (20, 10)))
    up = dbs.scalar(select(Upload).where(Upload.session_id == s["id"]))
    assert 29 <= up.dauer_sekunden <= 31


# ---------------------------------------------------------------- Seite „Guthaben“
def _sl_anmelden(client, name="anna"):
    r = client.post("/guthaben/anmelden", data={"username": name, "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    seite = client.get("/guthaben").text
    return re.search(r'name="csrf" value="([^"]+)"', seite).group(1), seite


def test_seite_guthaben(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.models import Job

    _modus(dbs, worker_transkription_h=36000)
    r = client.get("/guthaben")
    assert r.status_code == 200 and "Benutzername" in r.text
    assert r.headers["x-frame-options"] == "DENY"
    falsch = client.post("/guthaben/anmelden", data={"username": "anna", "password": "x"})
    assert falsch.status_code == 401
    s = _wartende_runde(client, world, dbs, tmp_path, (30, 30))
    csrf, seite = _sl_anmelden(client)
    assert "Dein Guthaben" in seite and "0,00 €" in seite and "Verarbeiten" in seite and "Rabenfels" in seite
    job = dbs.scalar(select(Job).where(Job.session_id == s["id"]))
    assert client.post(f"/guthaben/auftraege/{job.id}/verarbeiten", data={"csrf": "falsch"}).status_code == 403
    r = client.post(f"/guthaben/auftraege/{job.id}/verarbeiten", data={"csrf": csrf}, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert dbs.get(Job, job.id).kosten_freigegeben_at is not None
    # Einstellungen
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    r = client.post("/guthaben/einstellungen", data={"csrf": csrf, "guthaben_id": g.id, "freigabe_ab": "5,50",
                                                     "bogen_seiten": "12", "boegen_je_spieler": "2"},
                    follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    assert (g.freigabe_ab_cent, g.bogen_seiten, g.boegen_je_spieler) == (550, 12, 2)
    # Fremdes Guthaben → 404
    from app.models import User

    ben = dbs.scalar(select(User).where(User.username == "ben"))
    fremd = abrechnung.guthaben_konto(dbs, ben.id)
    dbs.commit()
    r = client.post("/guthaben/einstellungen", data={"csrf": csrf, "guthaben_id": fremd.id, "freigabe_ab": "1",
                                                     "bogen_seiten": "1", "boegen_je_spieler": "1"})
    assert r.status_code == 404


def test_gutscheine_ende_zu_ende(client, world, dbs, admin):  # noqa: F811
    from app import abrechnung
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf
    from tests.test_server_0484 import _code

    _modus(dbs)
    daten = {"csrf": admin, "anzahl": "3", "betrag": "2,50", "aktion": "Verlosung", "bestaetigt": "1"}
    r = client.post("/verwaltung/abrechnung/gutscheine", data=daten)
    assert r.status_code == 403 and "zweiten Faktor" in r.text  # ohne zweiten Faktor nicht
    chef = dbs.scalar(select(User).where(User.username == "chef"))
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    r = client.post("/verwaltung/abrechnung/gutscheine", data=daten)
    assert r.status_code == 200
    codes = re.findall(r"<code>([A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4}-[A-Z0-9]{4})</code>", r.text)
    assert len(codes) == 3
    from app.models import Gutschein

    assert all(codes[0] not in g.code_hash for g in dbs.scalars(select(Gutschein)))  # nur der Hash liegt vor
    client.cookies.clear()
    csrf, _ = _sl_anmelden(client)
    r = client.post("/guthaben/gutschein", data={"csrf": csrf, "code": codes[0].lower()}, follow_redirects=False)
    assert r.status_code == 303
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    assert abrechnung.saldo(dbs, g.id) == 250
    r = client.post("/guthaben/gutschein", data={"csrf": csrf, "code": codes[0]})
    assert r.status_code == 400 and "schon eingelöst" in r.text  # nur einmal
    r = client.post("/guthaben/gutschein", data={"csrf": csrf, "code": codes[1]})
    assert r.status_code == 400 and "derselben Aktion" in r.text  # je Konto einer je Aktion
    assert abrechnung.saldo(dbs, g.id) == 250


def test_gutschein_abgelaufen_und_versuche_begrenzt(client, world, dbs):
    from datetime import timedelta

    from app import abrechnung
    from app.db import utcnow

    _modus(dbs)
    code = abrechnung.gutscheine_erzeugen(dbs, 1, 100, "Alt", utcnow() - timedelta(days=1), "test")[0]
    dbs.commit()
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    with pytest.raises(ValueError):
        abrechnung.gutschein_einloesen(dbs, g, code, "x")
    dbs.rollback()
    csrf, _ = _sl_anmelden(client)
    for _ in range(10):
        client.post("/guthaben/gutschein", data={"csrf": csrf, "code": "AAAA-BBBB-CCCC-DDDD"})
    r = client.post("/guthaben/gutschein", data={"csrf": csrf, "code": "AAAA-BBBB-CCCC-DDDD"})
    assert "Zu viele Versuche" in r.text


def test_gutschrift_von_hand_mit_rueckfrage_und_storno(client, world, dbs, admin):  # noqa: F811
    from app import abrechnung
    from app.models import AbrechnungProtokoll, Buchung, User
    from app.verwaltung import zweiter_faktor as zf
    from tests.test_server_0484 import _code

    _modus(dbs, gutschrift_max=1000)
    chef = dbs.scalar(select(User).where(User.username == "chef"))
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    seite = client.get("/verwaltung/abrechnung")
    assert seite.status_code == 200 and "Gutschrift von Hand" in seite.text
    daten = {"csrf": admin, "ziel_konto": "anna", "betrag": "4,00", "grund": "Testmitspielerin"}
    r = client.post("/verwaltung/abrechnung/gutschrift", data=daten)
    assert r.status_code == 200 and "Gutschrift bestätigen" in r.text  # Rückfrage, noch nichts gebucht
    assert dbs.scalar(select(Buchung)) is None
    r = client.post("/verwaltung/abrechnung/gutschrift", data={**daten, "betrag": "20,00", "bestaetigt": "1"})
    assert r.status_code == 400  # über der Obergrenze
    r = client.post("/verwaltung/abrechnung/gutschrift", data={**daten, "bestaetigt": "1"}, follow_redirects=False)
    assert r.status_code == 303
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    assert abrechnung.saldo(dbs, g.id) == 400
    b = dbs.scalar(select(Buchung).where(Buchung.art == "gutschrift"))
    assert b.von == chef.id and b.grund == "Testmitspielerin"
    r = client.post(f"/verwaltung/abrechnung/storno/{b.id}", data={"csrf": admin, "bestaetigt": "1"},
                    follow_redirects=False)
    assert r.status_code == 303 and abrechnung.saldo(dbs, g.id) == 0
    r = client.post(f"/verwaltung/abrechnung/storno/{b.id}", data={"csrf": admin, "bestaetigt": "1"})
    assert r.status_code == 400 and abrechnung.saldo(dbs, g.id) == 0  # nur einmal
    was = {z.was for z in dbs.scalars(select(AbrechnungProtokoll))}
    assert {"modus", "preise", "gutschrift", "storno"} <= was


def test_verwaltung_schalter_und_preise(client, dbs, admin):  # noqa: F811
    from app import abrechnung

    r = client.post("/verwaltung/abrechnung/gutschrift", data={"csrf": admin, "ziel_konto": "chef", "betrag": "1",
                                                              "grund": "x"})
    assert r.status_code == 404  # Schalter aus
    from app.models import User
    from app.verwaltung import zweiter_faktor as zf
    from tests.test_server_0484 import _code

    chef = dbs.scalar(select(User).where(User.username == "chef"))  # seit 0.4.87 nur mit zweitem Faktor (N3)
    zf.beginnen(dbs, chef.id)
    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    dbs.commit()
    r = client.post("/verwaltung/abrechnung/modus", data={"csrf": admin, "modus": "guthaben"}, follow_redirects=False)
    assert r.status_code == 303 and abrechnung.modus(dbs) == "guthaben"
    felder = {name: "0" for name, _t, _e in __import__("app.verwaltung.abrechnung_seite",
                                                       fromlist=["x"]).PREISFELDER}
    felder.update({"csrf": admin, "aufschlag_prozent": "15", "worker_transkription_h": "1,20", "deckel": "2"})
    r = client.post("/verwaltung/abrechnung/preise", data=felder, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    p = abrechnung.preise(dbs)
    assert (p.aufschlag_prozent, p.worker_transkription_h, p.deckel) == (15, 120, 200)
    assert "Abrechnung" in client.get("/verwaltung/").text


def test_kontoloeschung_behaelt_buchungen_ohne_namen(client, world, dbs, make_user):
    from app import abrechnung
    from app.konto import entfernen
    from app.models import Buchung, Guthaben, User

    _modus(dbs)
    make_user("eva")
    eva = dbs.scalar(select(User).where(User.username == "eva"))
    g = _gutschreiben(dbs, eva, 300)
    entfernen(dbs, eva)
    dbs.delete(eva) if dbs.get(User, eva.id) is not None else None
    dbs.commit()
    dbs.expire_all()
    assert dbs.get(Guthaben, g.id).user_id is None
    assert dbs.scalar(select(Buchung).where(Buchung.guthaben_id == g.id)).betrag_cent == 300
    assert abrechnung.saldo(dbs, g.id) == 300
