"""Server 0.4.148: Schnittstelle 0.4.30 – Kapitel teilen als verschlüsselte Datei (S45).

Prüft den gemeinsamen Testvektor (tests/vektoren/kapitel-teilen-1.json, die App prüft denselben), dass die Datei nichts
enthält, was ein gerade beigetretenes Mitglied nicht sieht, und dass nur aktive Mitglieder sie öffnen können."""
import json
from pathlib import Path

import pytest

from tests.test_step2a import API, neue_session

VEKTOR = Path(__file__).parent / "vektoren" / "kapitel-teilen-1.json"


# ---------------------------------------------------------------- Testvektor
def test_gemeinsamer_testvektor():
    from cryptography.hazmat.primitives import serialization

    from app import teilen as t

    v = json.loads(VEKTOR.read_text(encoding="utf-8"))
    spki = t.b64u_lesen(v["spki"])
    for r in v["recipients"]:
        assert t.oeffnen(v["file"], spki, r["keyId"], t.b64u_lesen(r["key"])) == v["content"]
    with pytest.raises(ValueError):
        t.oeffnen(v["file"], spki, v["fremderSchluessel"]["keyId"], t.b64u_lesen(v["fremderSchluessel"]["key"]))
    # Signatur: DER ↔ r‖s, und sie gilt über genau die Bytes von „signed“
    p1363, der = t.b64u_lesen(v["p1363Signature"]), t.b64u_lesen(v["derSignature"])
    assert len(p1363) == 64 and t.p1363_zu_der(p1363) == der and t.der_zu_p1363(der) == p1363
    datei = json.loads(v["file"])
    assert t.b64u_lesen(datei["signed"]).decode() == v["signed"]
    assert v["header"] == t.kopf(v["serverId"], v["campaignId"], v["shareId"], v["createdAt"]).decode()
    assert v["header"] == ('{"format":"taleward-kapitel/1","serverId":"chronik-vektor","campaignId":"c-0001",'
                           '"shareId":"vektor-1","createdAt":"2026-11-01T09:30:00Z"}')
    signiert = json.loads(v["signed"])
    assert list(signiert) == ["serverId", "campaignId", "shareId", "createdAt", "signKeyId", "recipients", "nonce",
                              "ciphertext"]
    assert signiert["signKeyId"] == v["signKeyId"] and signiert["nonce"] == v["contentNonce"]
    # Nachbauen mit denselben Zufallswerten ergibt denselben signierten Teil
    privat = serialization.load_der_private_key(t.b64u_lesen(v["privateKeyPkcs8"]), password=None)
    folge = [t.b64u_lesen(v["contentKey"]), t.b64u_lesen(v["contentNonce"])] + [
        t.b64u_lesen(r["nonce"]) for r in v["recipients"]]

    class Fest(t.Zufall):
        def bytes(self, n):
            return folge.pop(0)

        def share_id(self):
            return v["shareId"]

    sign = t.Signierer(v["signKeyId"], privat)
    neu = t.bauen(sign, v["serverId"], v["campaignId"], v["createdAt"], v["content"],
                  [(r["keyId"], t.b64u_lesen(r["key"])) for r in v["recipients"]], Fest())
    assert json.loads(neu)["signed"] == datei["signed"]


def test_veraenderte_datei_wird_abgelehnt():
    from app import teilen as t

    v = json.loads(VEKTOR.read_text(encoding="utf-8"))
    spki, r = t.b64u_lesen(v["spki"]), v["recipients"][1]
    schluessel = t.b64u_lesen(r["key"])
    datei = json.loads(v["file"])
    signiert = json.loads(v["signed"])
    for feld, wert in (("campaignId", "c-0002"), ("createdAt", "2027-01-01T00:00:00Z"), ("ciphertext", "AAAA")):
        falsch = dict(datei, signed=t.b64u(json.dumps(dict(signiert, **{feld: wert}), separators=(",", ":")).encode()))
        with pytest.raises(ValueError, match="signatur"):
            t.oeffnen(json.dumps(falsch), spki, r["keyId"], schluessel)
    sig = bytearray(t.b64u_lesen(datei["signature"]))
    sig[5] ^= 1
    with pytest.raises(ValueError, match="signatur"):
        t.oeffnen(json.dumps(dict(datei, signature=t.b64u(bytes(sig)))), spki, r["keyId"], schluessel)
    with pytest.raises(ValueError, match="format"):
        t.oeffnen(json.dumps(dict(datei, format="taleward-kapitel/2")), spki, r["keyId"], schluessel)
    # Fremder Server-Schlüssel: dieselbe Datei gilt nicht
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import ec

    fremd = ec.generate_private_key(ec.SECP256R1()).public_key().public_bytes(
        serialization.Encoding.DER, serialization.PublicFormat.SubjectPublicKeyInfo)
    with pytest.raises(ValueError, match="signatur"):
        t.oeffnen(v["file"], fremd, r["keyId"], schluessel)


# ---------------------------------------------------------------- Schlüssel
def _schluessel(client, w, h):
    return client.get(f"{API}/campaigns/{w['cid']}", headers=h).json().get("shareKey")


def test_server_schluessel_in_info_und_geschuetzt(client, dbs):
    from app import dpapi, teilen

    a = client.get(f"{API}/info").json()
    assert a["apiVersion"] == "0.4.35" and len(a["shareKeys"]) == 1
    assert client.get(f"{API}/info").json()["shareKeys"] == a["shareKeys"]  # bleibt gleich
    assert dpapi.geheim(teilen.META_SCHLUESSEL)
    assert teilen.signierer(dbs).id == a["shareKeys"][0]["id"]


def test_mitgliedsschluessel_nur_fuer_sich(client, world, dbs):
    from app import umzug
    from app.models import Campaign

    w = world
    mein = _schluessel(client, w, w["pl"])
    assert mein and len(mein["key"]) == 43 and _schluessel(client, w, w["pl"]) == mein  # fest bis zum Austritt
    sl = _schluessel(client, w, w["gm"])
    assert sl["id"] != mein["id"] and sl["key"] != mein["key"]
    # nirgends sonst: Liste, Mitglieder, Datenauskunft, Umzug
    for antwort in (client.get(f"{API}/campaigns", headers=w["pl"]).text,
                    client.get(f"{API}/campaigns/{w['cid']}", headers=w["gm"]).text,
                    client.get(f"{API}/me/export", headers=w["pl"]).text,
                    client.get(f"{API}/me/export", headers=w["gm"]).text):
        assert mein["key"] not in antwort and mein["id"] not in antwort
    dbs.expire_all()
    paket, _d = umzug.packliste(dbs, dbs.get(Campaign, w["cid"]), {w["pl_member"]})
    text = json.dumps(paket, ensure_ascii=False, default=str)
    assert mein["key"] not in text and sl["key"] not in text
    assert client.get(f"{API}/campaigns/{w['cid']}", headers=w["out"]).status_code == 404


# ---------------------------------------------------------------- Teilen
def _veroeffentlichen(dbs, sid, text="Die Gruppe erreicht Rabenfels.\n\nAm Tor wartet niemand."):
    from app.db import utcnow
    from app.models import GameSession, Recap

    s = dbs.get(GameSession, sid)
    s.state, s.published_at = "published", utcnow()
    dbs.add(Recap(session_id=sid, title="Ankunft", text=text, open_threads=json.dumps(["Wer schloss das Tor?"]),
                  review=json.dumps({"MARKER": "PRUEFTEIL"})))
    dbs.commit()


def _teilen(client, w, sid, h=None):
    return client.post(f"{API}/campaigns/{w['cid']}/shares", headers=h or w["gm"], json={"sessionId": sid})


def _oeffnen(client, antwort, schluessel):
    from app import teilen as t

    spki = t.b64u_lesen(client.get(f"{API}/info").json()["shareKeys"][0]["spki"])
    return t.oeffnen(antwort["file"], spki, schluessel["id"], t.b64u_lesen(schluessel["key"]))


def test_nur_sl_nur_veroeffentlicht(client, world, dbs, make_user, login):
    w = world
    s = neue_session(client, w)
    for h in (w["pl"], w["out"]):
        assert _teilen(client, w, s["id"], h).status_code == 404
    assert _teilen(client, w, s["id"]).status_code == 409
    assert _teilen(client, w, s["id"]).json()["code"] == "not_published"
    assert _teilen(client, w, "gibt-es-nicht").status_code == 404
    r = client.post(f"{API}/campaigns/{w['cid']}/shares", headers=w["gm"], json={"sessionId": s["id"], "x": 1})
    assert r.status_code == 400
    assert client.post(f"{API}/campaigns/{w['cid']}/shares", headers=w["gm"],
                       json={"sessionId": "a/b"}).status_code == 400
    # Runde einer anderen Kampagne derselben SL
    c2 = client.post(f"{API}/campaigns", json={"title": "Andere"}, headers=w["gm"]).json()
    r = client.post(f"{API}/campaigns/{c2['id']}/sessions", headers=w["gm"],
                    json={"playedAt": "2026-10-01T18:00:00Z",
                          "attendees": [{"memberId": c2["members"][0]["id"], "consent": True,
                                         "consentSource": "on_site"}]})
    assert r.status_code == 201, r.text
    _veroeffentlichen(dbs, r.json()["id"])
    assert _teilen(client, w, r.json()["id"]).status_code == 404


def test_inhalt_wie_ein_neues_mitglied(client, world, dbs, make_user, login):
    w = world
    eintrag = lambda **f: client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"],  # noqa: E731
                                      json={"type": "npc", "visibility": "public", "summary": "Bekannt."} | f).json()
    oeff = eintrag(name="Wirt Bodo", gmNotes="MARKER-SL-NOTIZ",
                   links=[{"id": "0b8f2c6e-1a2b-4c3d-8e9f-0a1b2c3d4e5f", "label": "Karte",
                          "url": "https://example.org/k", "shared": False}])
    eintrag(name="MARKER-VERBORGEN", hiddenFromMemberIds=[w["pl_member"]])
    eintrag(name="MARKER-GEHEIM", visibility="gm_only")
    s = neue_session(client, w)
    client.put(f"{API}/sessions/{s['id']}/gm-note", headers=w["gm"], json={"text": "MARKER-RUNDENNOTIZ"})
    client.post(f"{API}/campaigns/{w['cid']}/player-proposals", headers=w["pl"],
                json={"entryType": "npc", "title": "MARKER-VORSCHLAG", "detail": "x"})
    _veroeffentlichen(dbs, s["id"])
    client.post(f"{API}/sessions/{s['id']}/comments", headers=w["pl"], json={"text": "MARKER-KOMMENTAR"})
    mein, sl = _schluessel(client, w, w["pl"]), _schluessel(client, w, w["gm"])

    r = _teilen(client, w, s["id"])
    assert r.status_code == 200, r.text
    a = r.json()
    assert a["fileName"] == "taleward-kapitel-1.taleward" and a["missingKeys"] == []
    assert "Rabenfels" not in a["fileName"] and "Rabenfels" not in a["file"] and "Bodo" not in a["file"]
    inhalt = _oeffnen(client, a, mein)
    assert inhalt == _oeffnen(client, a, sl)  # für alle gleich
    text = json.dumps(inhalt, ensure_ascii=False)
    for marker in ("MARKER", "PRUEFTEIL", "example.org", "attendees", "unreadComments", "gmWarnings", "review"):
        assert marker not in text, marker
    assert inhalt["campaignTitle"] == "Rabenfels" and len(inhalt["sessions"]) == 1
    assert set(inhalt["sessions"][0]["session"]) == {"id", "number", "title", "playedAt", "state"}

    # Vergleich mit einer gerade beigetretenen Spielerin
    make_user("dora")
    h = login("dora")
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    dora = {m["displayName"]: m["id"] for m in client.post(
        f"{API}/campaigns/join", json={"code": code, "characterName": "Tilda"}, headers=h).json()["members"]}["Dora"]
    recap = client.get(f"{API}/sessions/{s['id']}/recap", headers=h).json()
    assert inhalt["sessions"][0]["recap"] == recap
    sieht = {e["id"]: e for e in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=h).json()}
    for e in inhalt["entries"]:
        assert sieht[e["id"]] == e
    assert [e["id"] for e in inhalt["entries"]] == [oeff["id"]]

    # Dora hat noch keinen Schlüssel → steht in missingKeys; nach dem Laden ist sie Empfängerin
    assert _teilen(client, w, s["id"]).json()["missingKeys"] == [dora]
    doras = _schluessel(client, w, h)
    b = _teilen(client, w, s["id"]).json()
    assert b["missingKeys"] == [] and _oeffnen(client, b, doras)["campaignTitle"] == "Rabenfels"
    assert json.loads(b["file"])["signed"] != json.loads(a["file"])["signed"]  # jedes Mal eine neue Datei


def test_ehemalige_und_wiedereintritt(client, world, dbs):
    w = world
    s = neue_session(client, w)
    _veroeffentlichen(dbs, s["id"])
    alt = _schluessel(client, w, w["pl"])
    _schluessel(client, w, w["gm"])
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["pl"]).status_code == 204
    a = _teilen(client, w, s["id"]).json()
    assert a["missingKeys"] == []  # Ehemalige sind keine Empfänger und fehlen auch nicht
    with pytest.raises(ValueError):
        _oeffnen(client, a, alt)
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    assert client.post(f"{API}/campaigns/join", json={"code": code}, headers=w["pl"]).status_code in (200, 201)
    neu = _schluessel(client, w, w["pl"])
    assert neu["id"] != alt["id"] and neu["key"] != alt["key"]
    b = _teilen(client, w, s["id"]).json()
    assert _oeffnen(client, b, neu)["sessions"]
    with pytest.raises(ValueError):
        _oeffnen(client, b, alt)


def test_schluessel_gehoert_der_person_nicht_dem_platz(client, world, dbs):
    """Wer den Platz übernimmt (anderes Konto, Platz freigeben, Konto gelöscht), bekommt nie den alten Schlüssel."""
    from app import teilen
    from app.models import Member, ShareKey

    w = world
    alt = _schluessel(client, w, w["pl"])
    m = dbs.get(Member, w["pl_member"])
    m.user_id = None  # wie nach Platz freigeben bzw. Kontolöschung
    dbs.commit()
    assert teilen.gueltiger(dbs, m) is None and dbs.get(ShareKey, m.id) is None
    from app.models import User

    m.user_id = dbs.query(User).filter_by(username="cleo").one().id
    dbs.commit()
    assert teilen.fuer_mich(dbs, m)["key"] != alt["key"]


def test_hoechstens_30_je_stunde(client, world, dbs, monkeypatch):
    from app import teilen

    monkeypatch.setattr(teilen, "JE_STUNDE", 2)
    w = world
    s = neue_session(client, w)
    _veroeffentlichen(dbs, s["id"])
    assert [_teilen(client, w, s["id"]).status_code for _ in range(3)] == [200, 200, 429]
