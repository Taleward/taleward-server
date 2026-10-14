"""Server 0.4.91: S31 beim Einnehmen eines offenen Platzes, Marke nur bei echter Änderung, M2 (immer dekodieren, wenn
die Länge Geld kostet; Upload während der Messung gesperrt) und Schnittstelle 0.4.19 (Beziehung bei der mitgebrachten
Welt, Grenzen für Charakterbögen)."""
import uuid

from sqlalchemy import select

from tests.test_abrechnung_0485 import _modus
from tests.test_server_0489 import _auswerten_und_annehmen, _namen
from tests.test_unterlagen import api  # noqa: F401 (Fixture)

API = "/api/v1"


def _platz(dbs, w, name="Lirael"):
    from app.models import Member

    m = Member(campaign_id=w["cid"], user_id=None, role="player", character_name=name, open_seat=True)
    dbs.add(m)
    dbs.commit()
    return m.id


def _platz_einnehmen(client, w, platz, h):
    code = client.post(f"{API}/campaigns/{w['cid']}/members/{platz}/invite", headers=w["gm"]).json()["code"]
    r = client.post(f"{API}/campaigns/join", json={"code": code}, headers=h)
    assert r.status_code == 200, r.text
    return r.json()


# ---------------------------------------------------------------- S31: offener Platz
def test_s31_offener_platz_sieht_geheimes_aus_auswertung_nicht(client, world, dbs, api, make_user, login):  # noqa: F811
    from app.zusammenfassung import eingabe_bauen  # noqa: F401 (Recap-Eingang prüft test_server_0489)

    w = world
    platz = _platz(dbs, w)
    _auswerten_und_annehmen(client, w, dbs, api)
    [v] = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    assert v["hiddenFromMemberIds"] == [platz]  # offene Plätze zählen mit
    assert client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"}).status_code == 200
    make_user("dora")
    dora = login("dora")
    _platz_einnehmen(client, w, platz, dora)
    assert "Orden der Asche" not in _namen(client, w, dora)
    assert "Orden der Asche" in _namen(client, w, w["pl"])  # die Urheberin weiterhin


def test_s31_platz_entsteht_nach_der_annahme(client, world, dbs, api, make_user, login):  # noqa: F811
    """Marke statt Liste: auch ein Platz, den es beim Annehmen noch nicht gab, sieht den Eintrag nicht."""
    from app.models import Entry, GmNotice, Proposal

    w = world
    _auswerten_und_annehmen(client, w, dbs, api)
    [v] = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"})
    # zweite Auswertung bleibt offen
    a2 = client.post(f"{API}/campaigns/{w['cid']}/members/me/backstory-analysis", headers=w["pl"])
    platz = _platz(dbs, w)
    make_user("dora")
    dora = login("dora")
    _platz_einnehmen(client, w, platz, dora)
    assert "Orden der Asche" not in _namen(client, w, dora)
    dbs.expire_all()
    e = dbs.scalar(select(Entry).where(Entry.name == "Orden der Asche"))
    assert platz in e.hidden_member_ids
    assert dbs.scalar(select(GmNotice).where(GmNotice.code == "hidden_entries_for_newcomer",
                                            GmNotice.member_id == platz)) is not None  # die SL kann freigeben
    import json

    for p in dbs.scalars(select(Proposal).where(Proposal.decision == "open", Proposal.analysis_id.is_not(None))):
        assert platz in json.loads(p.hidden_member_ids_json)
    assert a2.status_code in (200, 201, 409)


def test_s31_platz_bekommt_nur_auswertungen_dazu(client, world, dbs, make_user, login):
    """Andere Verborgen-Listen bringt der Platz aus dem Umzug mit – sie werden beim Einnehmen nicht erweitert."""
    w = world
    platz = _platz(dbs, w)
    r = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"], json={
        "type": "npc", "name": "Hella", "summary": "Kräuterfrau", "visibility": "public",
        "hiddenFromMemberIds": [w["pl_member"]]})
    assert r.status_code == 201, r.text
    make_user("dora")
    dora = login("dora")
    _platz_einnehmen(client, w, platz, dora)
    assert "Hella" in _namen(client, w, dora)


def test_marke_bleibt_beim_speichern_ohne_aenderung(client, world, dbs, api, make_user, login):  # noqa: F811
    from app.models import Entry

    w = world
    _auswerten_und_annehmen(client, w, dbs, api)
    [v] = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"})
    e = next(x for x in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"]).json()
             if x["name"] == "Orden der Asche")
    # die App schickt das ganze Formular – gleiche Sichtbarkeit, gleiche Liste, nur der Text ist neu
    r = client.patch(f"{API}/entries/{e['id']}", headers=w["gm"], json={
        "summary": "Jagt Tharvok seit Jahren, meist nachts.", "visibility": e["visibility"],
        "hiddenFromMemberIds": e["hiddenFromMemberIds"]})
    assert r.status_code == 200
    dbs.expire_all()
    assert dbs.get(Entry, e["id"]).origin_analysis_id
    make_user("dora")
    dora = login("dora")
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Dora"}, headers=dora)
    assert "Orden der Asche" not in _namen(client, w, dora)


# ---------------------------------------------------------------- M2
def test_m2_mit_abrechnung_immer_dekodiert(tmp_path, monkeypatch):
    from app import audio

    datei = tmp_path / "a.mka"
    datei.write_bytes(b"\0" * 3_600_000)  # 1 h Opus mit 8 kbit/s, Kopf sagt 60 s (60 kB/s: unauffällig)
    aufrufe = []
    monkeypatch.setattr(audio.shutil, "which", lambda name: "/usr/bin/" + name)
    import subprocess

    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"60.0\n", b""))
    monkeypatch.setattr(audio, "dauer_dekodiert", lambda d: aufrufe.append(d) or 3600.0)
    assert audio.dauer_datei(datei) == 60.0  # ohne Geld: Kopf genügt
    assert audio.dauer_datei(datei, gruendlich=True) == 3600.0 and aufrufe
    monkeypatch.setattr(audio, "dauer_dekodiert", lambda d: None)
    assert audio.dauer_datei(datei, gruendlich=True) is None  # nicht dekodierbar → nie die Kopf-Länge
    monkeypatch.setattr(subprocess, "run", lambda *a, **k: subprocess.CompletedProcess(a, 0, b"1.0\n", b""))
    assert audio.dauer_datei(datei) is None  # unplausibler Kopf und nicht dekodierbar → nicht messbar


def _hochladen_bis_vor_abschluss(client, w, groesse=1000):
    from tests.test_step2a import neue_session

    s = neue_session(client, w)
    r = client.post(f"{API}/sessions/{s['id']}/uploads", headers=w["gm"], json={
        "source": "table", "files": [{"fileName": "a.m4a", "sizeBytes": groesse, "mimeType": "audio/mp4"}]})
    assert r.status_code == 201, r.text
    up = r.json()
    f = up["files"][0]
    assert client.put(f"{API}/uploads/{up['uploadId']}/files/{f['fileId']}/chunks/0", headers=w["gm"],
                      content=b"\1" * groesse).status_code == 204
    return s, up, f


def test_m2_upload_waehrend_der_messung_gesperrt(client, world, dbs, monkeypatch):
    from app import abrechnung
    from app.models import Upload
    w = world
    _modus(dbs)
    s, up, f = _hochladen_bis_vor_abschluss(client, w)
    zwischendurch = {}

    def messen(u):
        # während der Messung: ein weiterer Abschluss ist harmlos, Teile lassen sich nicht mehr tauschen
        zwischendurch["teil"] = client.put(f"{API}/uploads/{up['uploadId']}/files/{f['fileId']}/chunks/0",
                                           headers=w["gm"], content=b"\2" * 1000).status_code
        zwischendurch["zweiter"] = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=w["gm"]).status_code
        return 1800.0

    monkeypatch.setattr(abrechnung, "aufnahme_messen", messen)
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=w["gm"])
    assert r.status_code == 202, r.text
    assert zwischendurch == {"teil": 409, "zweiter": 202}
    dbs.expire_all()
    u = dbs.get(Upload, up["uploadId"])
    assert u.state == "completed" and u.dauer_sekunden == 1800.0


def test_m2_messung_nach_neustart_wiederholt(client, world, dbs, monkeypatch):
    from app import abrechnung
    from app.models import Upload
    w = world
    _modus(dbs)
    s, up, f = _hochladen_bis_vor_abschluss(client, w)
    u = dbs.get(Upload, up["uploadId"])
    u.state = "measuring"  # Server mitten in der Messung neu gestartet
    dbs.commit()
    monkeypatch.setattr(abrechnung, "aufnahme_messen", lambda u: 600.0)
    assert client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=w["gm"]).status_code == 202
    dbs.expire_all()
    assert dbs.get(Upload, up["uploadId"]).state == "completed"


# ---------------------------------------------------------------- Schnittstelle 0.4.19
def _welt(client, w, eintraege):
    return client.post(f"{API}/campaigns/{w['cid']}/members/me/world", headers=w["pl"], json={"entries": eintraege})


def test_beziehung_bei_der_mitgebrachten_welt(client, world, dbs):
    from tests.test_charakterauswertung_0418 import _charakter

    w = world
    _charakter(client, w, w["pl"])
    kennung = str(uuid.uuid4())
    eintrag = {"id": kennung, "version": 1, "type": "npc", "name": "Litha", "summary": "Ältere Schwester",
               "secret": False, "relation": "  Schwester von Mira  "}
    r = _welt(client, w, [eintrag])
    assert r.status_code == 200, r.text
    vs = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    [v] = [x for x in vs if x["originEntryId"] == kennung]
    assert v["relation"] == "Schwester von Mira"
    # nur Anzeige: geht beim Annehmen nicht in den Eintrag
    client.patch(f"{API}/proposals/{v['id']}", headers=w["gm"], json={"decision": "accepted"})
    e = next(x for x in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"]).json()
             if x["name"] == "Litha")
    assert "Schwester von Mira" not in str(e)
    # leer = null, zu lang = 400
    zweiter = {**eintrag, "id": str(uuid.uuid4()), "name": "Hella", "relation": "   "}
    _welt(client, w, [zweiter])
    vs = client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json()
    assert next(x for x in vs if x["originEntryId"] == zweiter["id"])["relation"] is None
    assert _welt(client, w, [{**eintrag, "version": 2, "relation": "x" * 121}]).status_code == 400
    # ältere Apps schicken das Feld nicht
    ohne = {k: v for k, v in eintrag.items() if k != "relation"}
    assert _welt(client, w, [{**ohne, "id": str(uuid.uuid4()), "name": "Bram"}]).status_code == 200


def test_grenzen_fuer_charakterboegen(client, world, dbs):
    from app import abrechnung
    from app.models import Campaign

    w = world
    for h in (w["gm"], w["pl"]):
        c = client.get(f"{API}/campaigns/{w['cid']}", headers=h).json()
        assert c["characterSheetLimits"] == {"maxSheets": 3, "maxPages": 20}
    _modus(dbs)
    g = abrechnung.zahler(dbs, dbs.get(Campaign, w["cid"]), anlegen=True)
    abrechnung.einstellungen_aendern(dbs, g, "test", g.freigabe_ab_cent, 5, 1)
    dbs.commit()
    c = client.get(f"{API}/campaigns/{w['cid']}", headers=w["pl"]).json()
    assert c["characterSheetLimits"] == {"maxSheets": 1, "maxPages": 5}
