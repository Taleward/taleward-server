"""Server 0.4.132 / Schnittstelle 0.4.25: Guthaben in der App (/credits, CreditWait, zahlende SL) und
Kampagnen-Einstellungen an einem Ort – mit den Bedingungen G14–G17 des Sicherheits-Chats."""
import re

from sqlalchemy import select

from tests.test_abrechnung_0485 import _kleine_teile  # noqa: F401 (Fixture)
from tests.test_abrechnung_0485 import _anna, _gutschreiben, _modus, _sl_anmelden
from tests.test_charakterauswertung_0418 import _bogen, _entscheiden, _schalten
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)
from tests.test_unterlagen import hochladen

API = "/api/v1"


def _user(dbs, name):
    from app.models import User

    return dbs.scalar(select(User).where(User.username == name))


def _adresse(dbs, url):
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "angabe.public_url", url)
    dbs.commit()


def cloud_einrichten(dbs):
    """Cloud-Sprachmodell und Cloud-Transkription eingerichtet – sonst sind beide Schalter gesperrt (unavailable)."""
    from app.einstellungen import meta_schreiben

    for k, v in {"llm.art": "api", "llm.api_url": "https://api.mistral.ai/v1", "llm.api_key": "geheim-1234",
                 "llm.api_modell": "mistral-medium-latest", "extern.anbieter": "mistral",
                 "extern.api_key": "geheim-1234"}.items():
        meta_schreiben(dbs, k, v)
    dbs.commit()


def test_ohne_sperren_wenn_eingerichtet(client, world, dbs):
    cloud_einrichten(dbs)
    s = client.get(f"{API}/campaigns/{world['cid']}/settings", headers=world["gm"]).json()
    assert s["locks"] == []


def _teure_unterlage(client, w, dbs):
    """Unterlage der SL, die wegen des Freigabe-Betrags wartet (5 € je Seite, Freigabe ab 2 €)."""
    from app.models import Job

    _modus(dbs, worker_unterlage_seite=500)
    d = hochladen(client, w, "geheim.txt", "Der Graf ist der Verräter.".encode(), kind="gm",
                  titel="Der Graf ist der Verräter").json()
    job = dbs.scalar(select(Job).where(Job.document_id == d["id"]))
    return d, job


def _auswertung(client, w, h, a_id):
    return next(a for a in client.get(f"{API}/campaigns/{w['cid']}/character-analyses", headers=h).json()
                if a["id"] == a_id)


def _topf_verwalterin(dbs, w, name="cleo"):
    """cleo verwaltet die Organisation der Kampagne, ist dort aber keine SL (G14)."""
    from app.models import Campaign, OrgMember

    c = dbs.get(Campaign, w["cid"])
    uid = _user(dbs, name).id
    om = dbs.scalar(select(OrgMember).where(OrgMember.organization_id == c.organization_id, OrgMember.user_id == uid))
    if om is None:
        dbs.add(OrgMember(organization_id=c.organization_id, user_id=uid, role="admin"))
    else:
        om.role = "admin"
    dbs.commit()


# ---------------------------------------------------------------- ServerInfo.billing, Abrechnung aus (Bedingung 2)
def test_billing_in_info_und_aus_heisst_404(client, world, dbs):
    assert client.get(f"{API}/info").json()["billing"] is None
    for weg in ("/credits", "/credits/x/bookings", "/credits/x/waiting"):
        assert client.get(API + weg, headers=world["gm"]).status_code == 404
    assert client.post(f"{API}/campaigns/{world['cid']}/payer/accept", headers=world["gm"]).status_code == 404
    k = client.get(f"{API}/campaigns/{world['cid']}", headers=world["gm"]).json()
    assert k["billing"] is None and k["settingsVersion"] == 1

    _modus(dbs)
    b = client.get(f"{API}/info").json()["billing"]
    assert b == {"mode": "credit", "currency": "EUR"}  # G15: ohne eingetragene Adresse keine topUpUrl
    _adresse(dbs, "http://chronik.example.org")
    assert "topUpUrl" not in client.get(f"{API}/info").json()["billing"]  # nur https (außer Heimnetz)
    _adresse(dbs, "http://192.168.1.20:8000")
    assert client.get(f"{API}/info").json()["billing"]["topUpUrl"] == "http://192.168.1.20:8000/guthaben"
    _adresse(dbs, "https://chronik.example.org")
    r = client.get(f"{API}/info", headers={"Host": "boese.example.net"})
    assert r.json()["billing"]["topUpUrl"] == "https://chronik.example.org/guthaben"  # nie aus der Host-Kopfzeile
    _modus(dbs, "topf")
    assert client.get(f"{API}/info").json()["billing"]["mode"] == "pot"


# ---------------------------------------------------------------- /credits
def test_eigenes_guthaben_buchungen_und_fremde_404(client, world, dbs):
    from app import abrechnung
    from app.models import Buchung

    _modus(dbs, warnung_ab=150)
    liste = client.get(f"{API}/credits", headers=world["gm"]).json()
    assert len(liste) == 1 and liste[0]["kind"] == "account" and liste[0]["balanceCents"] == 0
    assert liste[0]["status"] == "empty" and liste[0]["canManage"] is True and liste[0]["approvalThresholdCents"] == 200
    gid = liste[0]["id"]
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    abrechnung.gutschreiben(dbs, g, 300, "Start" + chr(0x202E) + "hilfe" + chr(10) + "x", "verwalter")
    for _ in range(3):
        abrechnung.buchen(dbs, g, "gutschein", 10)
    dbs.commit()
    assert client.get(f"{API}/credits", headers=world["gm"]).json()[0]["status"] == "good"
    b = client.get(f"{API}/credits/{gid}/bookings?limit=2", headers=world["gm"]).json()
    assert len(b) == 2 and all(x["kind"] == "voucher" and x["reason"] is None for x in b)
    rest = client.get(f"{API}/credits/{gid}/bookings?before={b[-1]['id']}", headers=world["gm"]).json()
    assert [x["kind"] for x in rest] == ["voucher", "credit"]
    assert rest[-1]["reason"] == "Start hilfe x" and rest[-1]["amountCents"] == 300  # G17: bereinigt
    assert {x["id"] for x in b}.isdisjoint({x["id"] for x in rest})
    # Fremde und Spieler: 404 ohne Anzahl
    for h in (world["pl"], world["out"]):
        assert client.get(f"{API}/credits/{gid}/bookings", headers=h).status_code == 404
        assert client.get(f"{API}/credits/{gid}/waiting", headers=h).status_code == 404
        assert client.patch(f"{API}/credits/{gid}/settings", headers=h, json={}).status_code == 404
    andere = dbs.scalar(select(Buchung).where(Buchung.guthaben_id != gid).limit(1))
    assert andere is None or client.get(f"{API}/credits/{gid}/bookings?before={andere.id}",
                                        headers=world["gm"]).status_code == 404


def test_einstellungen_des_guthabens_begrenzt(client, world, dbs):
    _modus(dbs)
    gid = client.get(f"{API}/credits", headers=world["gm"]).json()[0]["id"]
    r = client.patch(f"{API}/credits/{gid}/settings", headers=world["gm"],
                     json={"approvalThresholdCents": 999999, "sheetsPerPlayer": 2})
    assert r.status_code == 200, r.text
    assert (r.json()["approvalThresholdCents"], r.json()["sheetsPerPlayer"], r.json()["sheetMaxPages"]) == \
        (100000, 2, 20)
    k = client.get(f"{API}/campaigns/{world['cid']}/settings", headers=world["pl"]).json()
    assert k["characterSheetLimits"] == {"maxSheets": 2, "maxPages": 20}


def test_wartend_verarbeiten_und_creditwait(client, world, dbs):
    from app.models import Job

    d, job = _teure_unterlage(client, world, dbs)
    gid = client.get(f"{API}/credits", headers=world["gm"]).json()[0]["id"]
    doc = client.get(f"{API}/documents/{d['id']}", headers=world["gm"]).json()
    assert doc["creditWait"] == {"reason": "approval", "estimateCents": 500, "creditId": gid, "jobId": job.id}
    assert "/guthaben" not in (doc["message"] or "") and "„Verarbeiten“" in doc["message"]
    w = client.get(f"{API}/credits/{gid}/waiting", headers=world["gm"]).json()
    assert len(w) == 1 and w[0]["jobKind"] == "document" and w[0]["documentTitle"] == "Der Graf ist der Verräter"
    assert w[0]["reason"] == "approval" and w[0]["estimateCents"] == 500
    assert client.get(f"{API}/credits", headers=world["gm"]).json()[0]["waitingCount"] == 1
    r = client.post(f"{API}/credits/{gid}/waiting/{job.id}/approve", headers=world["gm"])
    assert r.status_code == 204
    dbs.expire_all()
    assert dbs.get(Job, job.id).kosten_freigegeben_at is not None
    # jetzt wartet er auf Guthaben – „Verarbeiten“ geht nicht noch einmal
    doc = client.get(f"{API}/documents/{d['id']}", headers=world["gm"]).json()
    assert doc["creditWait"]["reason"] == "empty"
    assert client.post(f"{API}/credits/{gid}/waiting/{job.id}/approve", headers=world["gm"]).status_code == 409
    assert client.post(f"{API}/credits/{gid}/waiting/unbekannt/approve", headers=world["gm"]).status_code == 404


def test_mit_sl_ohne_zahlung_sieht_grund_ohne_betrag(client, world, dbs, make_user, login):
    from app.models import Member

    d, _job = _teure_unterlage(client, world, dbs)
    make_user("dora")
    dbs.add(Member(campaign_id=world["cid"], user_id=_user(dbs, "dora").id, role="gm"))
    dbs.commit()
    doc = client.get(f"{API}/documents/{d['id']}", headers=login("dora")).json()
    assert doc["creditWait"] == {"reason": "approval", "estimateCents": None, "creditId": None, "jobId": None}


def test_gutschein_in_der_app(client, world, dbs, admin):  # noqa: F811
    from app import abrechnung

    _modus(dbs)
    codes = abrechnung.gutscheine_erzeugen(dbs, 2, 500, "Herbst", None, "test")
    dbs.commit()
    gid = client.get(f"{API}/credits", headers=world["gm"]).json()[0]["id"]
    url = f"{API}/credits/{gid}/vouchers"
    r = client.post(url, headers=world["gm"], json={"code": codes[0].lower().replace("-", " ")})
    assert r.status_code == 200, r.text
    assert r.json()["amountCents"] == 500 and r.json()["credit"]["balanceCents"] == 500
    saetze = set()
    for code in (codes[0], codes[1], "FALSCH-FALSCH"):  # schon eingelöst, gleiche Aktion, gibt es nicht
        r = client.post(url, headers=world["gm"], json={"code": code})
        assert r.status_code == 400 and r.json()["code"] == "voucher_invalid"
        saetze.add(r.json()["message"])
    assert len(saetze) == 1  # verrät nicht, ob es den Code gibt
    for _ in range(10):
        r = client.post(url, headers=world["gm"], json={"code": "NOCH-EINMAL"})
    assert r.status_code == 429


# ---------------------------------------------------------------- Topf (Bedingung 4) und G14
def test_topf_nur_lesen_und_titel_nur_fuer_die_sl(client, world, dbs):
    from app import abrechnung
    from app.models import Campaign

    d, job = _teure_unterlage(client, world, dbs)
    _modus(dbs, "topf")
    _topf_verwalterin(dbs, world)
    liste = client.get(f"{API}/credits", headers=world["out"]).json()
    topf = next(x for x in liste if x["kind"] == "organization")
    assert topf["canManage"] is False and topf["organizationName"]
    tid = topf["id"]
    # Titel, Runde und Kennungen nur für die heutige SL der Kampagne
    # Die SL (keine Verwalterin) sieht nur „pot“, ohne Betrag
    doc = client.get(f"{API}/documents/{d['id']}", headers=world["gm"]).json()
    assert doc["creditWait"] == {"reason": "pot", "estimateCents": None, "creditId": None, "jobId": None}
    assert client.get(f"{API}/credits/{tid}/waiting", headers=world["gm"]).status_code == 404
    w = client.get(f"{API}/credits/{tid}/waiting", headers=world["out"]).json()
    assert w[0]["campaignTitle"] == "Rabenfels" and w[0]["documentTitle"] is None and w[0]["documentId"] is None
    g = abrechnung.zahler(dbs, dbs.get(Campaign, world["cid"]), anlegen=True)
    abrechnung.buchen(dbs, g, "verbrauch", -120, job_id=job.id)
    dbs.commit()
    from app.models import KostenAuftrag

    dbs.add(KostenAuftrag(job_id=job.id, guthaben_id=g.id, weg="worker", art="document", zustand="abgerechnet",
                          campaign_id=world["cid"], document_id=d["id"]))
    dbs.commit()
    b = client.get(f"{API}/credits/{tid}/bookings", headers=world["out"]).json()
    assert b[0]["usage"] == {"jobKind": "document", "campaignId": world["cid"], "campaignTitle": "Rabenfels",
                             "sessionNumber": None, "documentTitle": None}
    # Ändern, Verarbeiten, Gutschein: nur auf der Seite des Servers
    for r in (client.patch(f"{API}/credits/{tid}/settings", headers=world["out"], json={"sheetsPerPlayer": 1}),
              client.post(f"{API}/credits/{tid}/waiting/{job.id}/approve", headers=world["out"]),
              client.post(f"{API}/credits/{tid}/vouchers", headers=world["out"], json={"code": "X"})):
        assert r.status_code == 403 and r.json()["code"] == "second_factor_required"
    # Billing der Kampagne: Organisation; Zahlung abgeben geht im Topf nicht
    k = client.get(f"{API}/campaigns/{world['cid']}", headers=world["gm"]).json()
    assert k["billing"] == {"paidBy": "organization", "payerMemberId": None, "isMe": False,
                            "offeredToMemberId": None}
    assert client.post(f"{API}/campaigns/{world['cid']}/payer/accept", headers=world["gm"]).status_code == 404


def test_g14_seite_guthaben_ohne_titel_fuer_topf_verwalter(client, world, dbs):
    _teure_unterlage(client, world, dbs)
    _modus(dbs, "topf")
    _topf_verwalterin(dbs, world)
    _csrf, seite = _sl_anmelden(client, "cleo")
    assert "Guthaben der Organisation" in seite and "Rabenfels" in seite
    assert "Der Graf ist der Verräter" not in seite and "SL-Unterlage" in seite


def test_g17_hinweis_beim_grund(client, dbs, admin):  # noqa: F811
    _modus(dbs)
    seite = client.get("/verwaltung/abrechnung").text
    assert 'id="grund-hilfe">Sieht die SL in der App.' in seite


# ---------------------------------------------------------------- Zahlende SL
def test_zahlung_abgeben_und_uebernehmen_ueber_die_app(client, world, dbs, make_user, login):
    from app.models import Member

    _modus(dbs)
    make_user("dora")
    dora = Member(campaign_id=world["cid"], user_id=_user(dbs, "dora").id, role="gm")
    dbs.add(dora)
    dbs.commit()
    hd = login("dora")
    k = client.get(f"{API}/campaigns/{world['cid']}", headers=world["gm"]).json()
    assert k["billing"] == {"paidBy": "gm", "payerMemberId": world["gm_member"], "isMe": True,
                            "offeredToMemberId": None}
    assert client.get(f"{API}/campaigns/{world['cid']}", headers=world["pl"]).json()["billing"] is None
    url = f"{API}/campaigns/{world['cid']}/payer"
    assert client.post(url + "/offer", headers=hd, json={"memberId": dora.id}).status_code == 404  # zahlt nicht
    assert client.post(url + "/offer", headers=world["pl"], json={"memberId": dora.id}).status_code == 404
    r = client.post(url + "/offer", headers=world["gm"], json={"memberId": world["pl_member"]})
    assert r.status_code == 400 and r.json()["code"] == "invalid_member"  # Spieler zahlen nie
    assert client.post(url + "/accept", headers=hd).status_code == 409  # ohne Angebot nicht
    r = client.post(url + "/offer", headers=world["gm"], json={"memberId": dora.id})
    assert r.json()["offeredToMemberId"] == dora.id
    assert client.post(url + "/offer", headers=world["gm"], json={"memberId": None}).json()["offeredToMemberId"] \
        is None
    client.post(url + "/offer", headers=world["gm"], json={"memberId": dora.id})
    r = client.post(url + "/accept", headers=hd)
    assert r.status_code == 200 and r.json() == {"paidBy": "gm", "payerMemberId": dora.id, "isMe": True,
                                                 "offeredToMemberId": None}
    assert client.post(url + "/accept", headers=world["pl"]).status_code == 404


# ---------------------------------------------------------------- Kampagnen-Einstellungen (G16)
def test_einstellungen_lesen_aendern_konflikt_protokoll(client, world, dbs):
    url = f"{API}/campaigns/{world['cid']}/settings"
    s = client.get(url, headers=world["pl"]).json()
    assert s["version"] == 1 and s["updatedByMemberId"] is None and s["allowCharacterAnalysis"] is False
    assert {(x["field"], x["value"], x["reason"]) for x in s["locks"]} == {
        ("allowCloudSummary", False, "unavailable"), ("allowExternalTranscription", False, "unavailable")}
    assert client.get(url, headers=world["out"]).status_code == 404
    assert client.patch(url, headers=world["pl"], json={"version": 1, "allowCharacterAnalysis": True}).status_code \
        == 404
    assert client.get(url + "/log", headers=world["pl"]).status_code == 404
    r = client.patch(url, headers=world["gm"], json={"version": 1, "allowCharacterAnalysis": True})
    assert r.status_code == 200 and r.json()["version"] == 2 and r.json()["updatedByMemberId"] == world["gm_member"]
    # alte Fassung → 409 mit dem aktuellen Stand, nichts geändert
    r = client.patch(url, headers=world["gm"], json={"version": 1, "allowCharacterAnalysis": False})
    assert r.status_code == 409 and r.json()["code"] == "settings_conflict"
    assert r.json()["details"]["current"]["version"] == 2 and r.json()["details"]["current"]["allowCharacterAnalysis"]
    # gesperrt: anderer Wert → 400; unverändert zurückgeschickt ist keine Änderung
    r = client.patch(url, headers=world["gm"], json={"version": 2, "allowCloudSummary": True})
    assert r.status_code == 400 and r.json()["code"] == "setting_locked" and r.json()["details"] == {
        "field": "allowCloudSummary"}
    assert client.patch(url, headers=world["gm"], json={"version": 2, "allowCloudSummary": False}).json()["version"] \
        == 2
    # Ältere Apps über PATCH /campaigns: derselbe Weg, Fassung zählt hoch
    k = _schalten(client, world, an=False).json()
    assert k["settingsVersion"] == 3 and k["allowCharacterAnalysis"] is False
    log = client.get(url + "/log", headers=world["gm"]).json()
    assert [(x["field"], x["oldValue"], x["newValue"], x["version"]) for x in log] == [
        ("allowCharacterAnalysis", True, False, 3), ("allowCharacterAnalysis", False, True, 2)]
    assert log[0]["byMemberId"] == world["gm_member"] and log[0]["byOperator"] is False


def test_ausschalten_lehnt_offene_auswertungen_ab(client, world, dbs):
    _schalten(client, world)
    a_id = _bogen(client, world, world["pl"]).json()["analysisId"]
    v = client.get(f"{API}/campaigns/{world['cid']}/settings", headers=world["gm"]).json()["version"]
    r = client.patch(f"{API}/campaigns/{world['cid']}/settings", headers=world["gm"],
                     json={"version": v, "allowCharacterAnalysis": False})
    assert r.status_code == 200
    a = _auswertung(client, world, world["gm"], a_id)
    assert a["state"] == "rejected"


def test_einschalten_nur_mit_nutzungsbedingungen(client, world, dbs):
    ich = _anna(dbs)
    ich.terms_accepted_version = None
    dbs.commit()
    url = f"{API}/campaigns/{world['cid']}/settings"
    r = client.patch(url, headers=world["gm"], json={"version": 1, "allowCharacterAnalysis": True})
    assert r.status_code == 403 and r.json()["code"] == "terms_required"


def test_anlegen_mit_einstellungen(client, world, dbs):
    r = client.post(f"{API}/campaigns", headers=world["gm"],
                    json={"title": "Nebelmoor", "settings": {"allowCharacterAnalysis": True,
                                                             "allowCloudSummary": False}})
    assert r.status_code == 201, r.text
    assert r.json()["allowCharacterAnalysis"] is True and r.json()["settingsVersion"] == 1
    log = client.get(f"{API}/campaigns/{r.json()['id']}/settings/log", headers=world["gm"]).json()
    assert log == []
    r = client.post(f"{API}/campaigns", headers=world["gm"],
                    json={"title": "Gesperrt", "settings": {"allowExternalTranscription": True}})
    assert r.status_code == 400 and r.json()["code"] == "setting_locked"
    from app.models import Campaign

    assert dbs.scalar(select(Campaign).where(Campaign.title == "Gesperrt")) is None  # nichts angelegt


# ---------------------------------------------------------------- Auswertungen: Schätzung und Zustimmung als Freigabe
def test_auswertung_schaetzung_und_zustimmung_gibt_frei(client, world, dbs):
    from app.models import Job

    _schalten(client, world)
    _modus(dbs, worker_unterlage_seite=500)
    a_id = _bogen(client, world, world["pl"]).json()["analysisId"]
    sl = _auswertung(client, world, world["gm"], a_id)
    sp = _auswertung(client, world, world["pl"], a_id)
    assert sl["costEstimateCents"] == 500 and sp["costEstimateCents"] is None and sp["creditWait"] is None
    r = _entscheiden(client, a_id, world["gm"])
    assert r.status_code == 200
    job = dbs.scalar(select(Job).where(Job.analysis_id == a_id))
    assert job.kosten_freigegeben_at is not None
    assert r.json()["creditWait"]["reason"] == "empty"  # freigegeben, wartet nur noch auf Guthaben
    _gutschreiben(dbs, _anna(dbs), 1000)
    assert _auswertung(client, world, world["gm"], a_id)["creditWait"] is None


def test_status_der_runde_ohne_adresse_und_nur_sl_mit_creditwait(_kleine_teile, client, world, dbs, tmp_path):  # noqa: F811
    from tests.test_abrechnung_0485 import _wartende_runde
    from tests.test_step2a import status

    _modus(dbs)
    s = _wartende_runde(client, world, dbs, tmp_path)
    sl = status(client, world["gm"], s["id"])
    assert sl["creditWait"]["reason"] == "empty" and sl["creditWait"]["creditId"]
    assert not re.search(r"https?://|/guthaben", sl["message"])
