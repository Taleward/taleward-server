"""Server 0.4.144 (Schnittstelle 0.4.27 + 0.4.28): Gerätekennung bei der App-Anmeldung, Vorschläge von Spielenden,
Merken, Pronomen, Zähler – mit den Prüfkriterien des Sicherheits-Chats (S56 und Bedingungen 1–23)."""
import json
from datetime import datetime, timedelta, timezone

from tests.test_step2a import API

RTL = chr(0x202E)  # Richtungszeichen, das nie gespeichert werden darf


def _z(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


def _zweite_spielerin(client, w, make_user, login):
    make_user("dora")
    h = login("dora")
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    j = client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Tilda"}, headers=h).json()
    return h, {m["displayName"]: m["id"] for m in j["members"]}["Dora"]


def _vorschlagen(client, w, h=None, **felder):
    daten = {"entryType": "npc", "title": "Der Graf", "detail": "Trägt nie einen Spiegel bei sich."} | felder
    return client.post(f"{API}/campaigns/{w['cid']}/player-proposals", headers=h or w["pl"], json=daten)


def _eintrag(client, w, **felder):
    daten = {"type": "npc", "visibility": "public", "summary": "Ein Mann."} | felder
    r = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"], json=daten)
    assert r.status_code == 201, r.text
    return r.json()


def _eigene(client, w, h=None):
    return {p["id"]: p for p in client.get(f"{API}/campaigns/{w['cid']}/player-proposals", headers=h or w["pl"]).json()}


# ---------------------------------------------------------------- Vorschläge: S56
def test_s56_urheberin_sieht_abgelehnt_und_verborgen_gleich(client, world):
    w = world
    geheim = _eintrag(client, w, name="Graf Orlok", visibility="gm_only")
    oeff = _eintrag(client, w, name="Burg Rabenstein", summary="Alte Burg.")
    ids = {}
    for name in ("abgelehnt", "verborgen", "nur_notiz", "sichtbar", "in_geheimes"):
        r = _vorschlagen(client, w, title=f"Vorschlag {name}", detail=f"Text {name}")
        assert r.status_code == 201, r.text
        assert r.json()["state"] == "open" and r.json()["authorMemberId"] == w["pl_member"]
        ids[name] = r.json()["id"]

    def entscheiden(name, **felder):
        r = client.post(f"{API}/player-proposals/{ids[name]}/decision", headers=w["gm"], json=felder)
        assert r.status_code == 200, r.text
        return r.json()

    assert entscheiden("abgelehnt", decision="reject")["state"] == "rejected"
    sl = entscheiden("verborgen", decision="accept", visibility="gm_only")
    assert sl["state"] == "accepted" and sl["resultEntryId"]  # die SL sieht den echten Zustand
    entscheiden("nur_notiz", decision="accept", mergeIntoEntryId=oeff["id"], detail="", gmNotes="Text nur_notiz")
    sichtbar = entscheiden("sichtbar", decision="accept")
    entscheiden("in_geheimes", decision="accept", mergeIntoEntryId=geheim["id"])

    eigene = _eigene(client, w)
    for name in ("abgelehnt", "verborgen", "nur_notiz", "in_geheimes"):
        p = eigene[ids[name]]
        assert (p["state"], p["resultEntryId"]) == ("closed", None), name
        assert p["decidedAt"]
    assert eigene[ids["sichtbar"]]["state"] == "accepted"
    assert eigene[ids["sichtbar"]]["resultEntryId"] == sichtbar["resultEntryId"]
    # Geheimes nie im sichtbaren Teil
    e = client.get(f"{API}/entries/{oeff['id']}", headers=w["gm"]).json()
    assert "Text nur_notiz" in e["gmNotes"] and "nur_notiz" not in e["summary"]
    assert client.get(f"{API}/entries/{oeff['id']}", headers=w["pl"]).json().get("gmNotes") is None
    # wird der Eintrag aufgedeckt, darf die Urheberin „übernommen“ sehen
    client.patch(f"{API}/entries/{sl['resultEntryId']}", headers=w["gm"], json={"visibility": "public"})
    assert _eigene(client, w)[ids["verborgen"]]["state"] == "accepted"


def test_ergaenzen_in_sichtbares_haengt_an_und_halter_nur_spielende(client, world):
    w = world
    karte = _eintrag(client, w, type="item", name="Karte", summary="Zeigt den Pfad.")
    p = _vorschlagen(client, w, entryType="item", title="Karte", detail="Mira hat sie.",
                     targetEntryId=karte["id"]).json()
    r = client.post(f"{API}/player-proposals/{p['id']}/decision", headers=w["gm"],
                    json={"decision": "accept", "holderMemberId": w["gm_member"]})
    assert r.status_code == 400 and r.json()["code"] == "holder_unknown"
    r = client.post(f"{API}/player-proposals/{p['id']}/decision", headers=w["gm"],
                    json={"decision": "accept", "holderMemberId": w["pl_member"]})
    assert r.status_code == 200
    e = client.get(f"{API}/entries/{karte['id']}", headers=w["pl"]).json()
    assert e["summary"] == "Zeigt den Pfad.\n\nMira hat sie." and e["holderMemberId"] == w["pl_member"]
    assert _eigene(client, w)[p["id"]]["state"] == "accepted"
    r = client.post(f"{API}/player-proposals/{p['id']}/decision", headers=w["gm"], json={"decision": "reject"})
    assert r.status_code == 409


def test_nur_eigene_keine_anzahl_und_gleiche_404(client, world, make_user, login):
    w = world
    dora, dora_id = _zweite_spielerin(client, w, make_user, login)
    verborgen = _eintrag(client, w, name="Geheimtür", hiddenFromMemberIds=[w["pl_member"]])
    unbekannt = _vorschlagen(client, w, targetEntryId="gibt-es-nicht")
    versteckt = _vorschlagen(client, w, targetEntryId=verborgen["id"])
    assert unbekannt.status_code == versteckt.status_code == 404
    assert unbekannt.json() == versteckt.json()
    assert _vorschlagen(client, w, dora, targetEntryId=verborgen["id"]).status_code == 201  # Dora sieht ihn
    p = _vorschlagen(client, w).json()
    assert list(_eigene(client, w, dora)) != [] and p["id"] not in _eigene(client, w, dora)
    assert client.delete(f"{API}/player-proposals/{p['id']}", headers=dora).status_code == 404
    assert client.post(f"{API}/player-proposals/{p['id']}/decision", headers=dora,
                       json={"decision": "reject"}).status_code == 404
    assert client.post(f"{API}/player-proposals/{p['id']}/decision", headers=w["pl"],
                       json={"decision": "reject"}).status_code == 404
    assert client.get(f"{API}/campaigns/{w['cid']}/player-proposals", headers=w["out"]).status_code == 404
    c_sp = client.get(f"{API}/campaigns/{w['cid']}", headers=w["pl"]).json()
    assert "openPlayerProposals" not in c_sp
    assert client.get(f"{API}/campaigns/{w['cid']}", headers=w["gm"]).json()["openPlayerProposals"] == 2
    assert len(client.get(f"{API}/campaigns/{w['cid']}/player-proposals", headers=w["gm"],
                          params={"state": "open"}).json()) == 2
    assert _vorschlagen(client, w, w["gm"]).status_code == 403  # die SL schlägt nicht vor
    # zurückziehen: einmal, danach 409
    assert client.delete(f"{API}/player-proposals/{p['id']}", headers=w["pl"]).status_code == 204
    assert client.delete(f"{API}/player-proposals/{p['id']}", headers=w["pl"]).status_code == 409
    assert _eigene(client, w)[p["id"]]["state"] == "withdrawn"
    assert dora_id


def test_grenzen_schalter_und_bereinigung(client, world, dbs):
    from app.models import PlayerProposal

    w = world
    r = _vorschlagen(client, w, title="Der" + RTL + " Graf" + chr(7), detail="Zeile 1\r\n\r\n\r\n" + chr(0x2066) + "Zeile 2")
    assert r.status_code == 201
    assert r.json()["title"] == "Der Graf" and r.json()["detail"] == "Zeile 1\n\nZeile 2"
    assert _vorschlagen(client, w, title="x" * 121).status_code == 400
    assert _vorschlagen(client, w, detail="x" * 2001).status_code == 400
    for i in range(19):
        assert _vorschlagen(client, w, title=f"V{i}").status_code == 201
    r = _vorschlagen(client, w, title="zu viel")
    assert r.status_code == 409 and r.json()["code"] == "player_proposal_limit"
    # 30 je Stunde: ältere zählen nicht mehr
    for p in dbs.query(PlayerProposal).all():
        p.created_at = p.created_at - timedelta(hours=2)
    dbs.commit()
    eine = dbs.query(PlayerProposal).first().id
    client.delete(f"{API}/player-proposals/{eine}", headers=w["pl"])
    assert _vorschlagen(client, w, title="wieder Platz").status_code == 201
    # Schalter über die Einstellungen (mit Fassung und Protokoll)
    v = client.get(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"]).json()
    assert v["playerProposals"] is True and v["counterKinds"] == []
    r = client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"],
                     json={"version": v["version"], "playerProposals": False})
    assert r.status_code == 200 and r.json()["version"] == v["version"] + 1
    log = client.get(f"{API}/campaigns/{w['cid']}/settings/log", headers=w["gm"]).json()
    assert log[0]["field"] == "playerProposals" and log[0]["newValue"] is False
    r = _vorschlagen(client, w)
    assert r.status_code == 403 and r.json()["code"] == "player_proposals_off"
    assert _eigene(client, w) == {}
    assert len(client.get(f"{API}/campaigns/{w['cid']}/player-proposals", headers=w["gm"]).json()) >= 20


def test_aufbewahrung_austritt_und_auskunft(client, world, dbs):
    from app import spielende
    from app.db import utcnow
    from app.models import Bookmark, PlayerProposal

    w = world
    alt = _vorschlagen(client, w, title="Alt").json()
    client.post(f"{API}/player-proposals/{alt['id']}/decision", headers=w["gm"], json={"decision": "reject"})
    neu = _vorschlagen(client, w, title="Neu").json()
    auskunft = client.get(f"{API}/me/export", headers=w["pl"]).json()
    zustaende = {p["title"]: p["state"] for p in auskunft["playerProposals"]}
    assert zustaende == {"Alt": "closed", "Neu": "open"}  # wie die Person ihn sieht (S56)
    p = dbs.get(PlayerProposal, alt["id"])
    p.decided_at = utcnow() - timedelta(days=91)
    dbs.commit()
    assert spielende.aufraeumen(dbs) == 1
    dbs.commit()
    dbs.expire_all()
    assert dbs.get(PlayerProposal, alt["id"]) is None and dbs.get(PlayerProposal, neu["id"]) is not None
    entschieden = _vorschlagen(client, w, title="Bleibt").json()
    client.post(f"{API}/player-proposals/{entschieden['id']}/decision", headers=w["gm"], json={"decision": "reject"})
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["pl"]).status_code == 204
    dbs.expire_all()
    assert dbs.get(PlayerProposal, neu["id"]) is None  # offene weg
    assert dbs.get(PlayerProposal, entschieden["id"]) is not None  # entschiedene bleiben
    assert dbs.query(Bookmark).count() == 0


# ---------------------------------------------------------------- Merken
def _laufende_runde(client, w, mit_ben=True):
    att = [{"memberId": w["gm_member"], "consent": True, "consentSource": "on_site"}]
    if mit_ben:
        att.append({"memberId": w["pl_member"], "consent": True, "consentSource": "on_site"})
    r = client.post(f"{API}/campaigns/{w['cid']}/sessions", headers=w["gm"],
                    json={"playedAt": _z(datetime.now(timezone.utc) - timedelta(hours=1)), "attendees": att})
    assert r.status_code == 201, r.text
    return r.json()


def test_merken_nur_fuer_die_person(client, world, make_user, login):
    w = world
    dora, _dora_id = _zweite_spielerin(client, w, make_user, login)
    s = _laufende_runde(client, w)
    assert client.get(f"{API}/campaigns/{w['cid']}", headers=w["pl"]).json()["liveSessionId"] == s["id"]
    assert client.get(f"{API}/campaigns/{w['cid']}", headers=w["gm"]).json()["liveSessionId"] == s["id"]
    assert client.get(f"{API}/campaigns/{w['cid']}", headers=dora).json()["liveSessionId"] is None
    # liveSessionId gibt keinen Zugang zur Runde
    assert client.get(f"{API}/sessions/{s['id']}", headers=w["pl"]).status_code == 404
    assert client.get(f"{API}/sessions/{s['id']}/bookmarks", headers=dora).status_code == 404
    assert client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=dora, json={}).status_code == 404
    r = client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={"note": "Graf" + RTL + " lügt"})
    assert r.status_code == 201, r.text
    b = r.json()
    assert b["note"] == "Graf lügt" and b["offsetMs"] is None and b["recapParagraph"] is None
    assert client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"]).status_code == 201  # ohne Inhalt
    zu_frueh = _z(datetime.now(timezone.utc) - timedelta(hours=14))
    zu_spaet = _z(datetime.now(timezone.utc) + timedelta(minutes=10))
    for at in (zu_frueh, zu_spaet):
        r = client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={"at": at})
        assert r.status_code == 400 and r.json()["code"] == "bookmark_time_invalid"
    assert len(client.get(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"]).json()) == 2
    assert client.get(f"{API}/sessions/{s['id']}/bookmarks", headers=w["gm"]).json() == []  # auch die SL nicht
    assert client.delete(f"{API}/bookmarks/{b['id']}", headers=w["gm"]).status_code == 404
    assert client.delete(f"{API}/bookmarks/{b['id']}", headers=dora).status_code == 404
    assert client.delete(f"{API}/bookmarks/{b['id']}", headers=w["pl"]).status_code == 204
    assert client.get(f"{API}/me/export", headers=w["pl"]).json()["bookmarks"][0]["sessionId"] == s["id"]


def test_merken_grenzen(client, world, dbs):
    from app import spielende
    from app.begrenzung import ZAEHLER

    w = world
    s = _laufende_runde(client, w)
    for _ in range(spielende.MERKEN_JE_MINUTE):
        assert client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={}).status_code == 201
    assert client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={}).status_code == 429
    ZAEHLER.vergessen()
    from app.models import Bookmark

    for _ in range(spielende.MERKEN_JE_RUNDE - spielende.MERKEN_JE_MINUTE):
        dbs.add(Bookmark(session_id=s["id"], member_id=w["pl_member"], at=datetime.now(timezone.utc)))
    dbs.commit()
    r = client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={})
    assert r.status_code == 409 and r.json()["code"] == "bookmark_limit"


def test_spannen_und_zeit_in_der_aufnahme():
    from app import spielende

    gespielt = datetime.now(timezone.utc).replace(microsecond=0) - timedelta(hours=4)

    def um(minuten):
        return gespielt + timedelta(minutes=minuten)

    spannen = [{"startedAt": _z(um(0)), "endedAt": _z(um(60))}, {"startedAt": _z(um(90)), "endedAt": _z(um(120))}]
    roh = spielende.spannen_pruefen(spannen, gespielt)
    achse = [tuple(x) for x in json.loads(roh)]
    assert spielende.versatz_ms(achse, um(100)) == 70 * 60 * 1000  # 60 min + 10 min nach der Pause
    assert spielende.versatz_ms(achse, um(70)) == 3600 * 1000  # in der Pause: Ende der Spanne davor
    assert spielende.versatz_ms(achse, um(-60)) is None and spielende.versatz_ms(achse, um(130)) is None
    # still verworfen: rückwärts, leer, zu früh, in der Zukunft, kaputt
    assert spielende.spannen_pruefen(list(reversed(spannen)), gespielt) is None
    assert spielende.spannen_pruefen([{"startedAt": _z(um(0)), "endedAt": _z(um(0))}], gespielt) is None
    assert spielende.spannen_pruefen([{"startedAt": _z(um(-800)), "endedAt": _z(um(-790))}], gespielt) is None
    assert spielende.spannen_pruefen([{"startedAt": _z(um(0)), "endedAt": _z(um(600))}], gespielt) is None
    assert spielende.spannen_pruefen([{"startedAt": "kaputt"}], gespielt) is None
    assert spielende.spannen_pruefen("kaputt", gespielt) is None
    assert spielende.absatz_finden([(0, 10.0), (1, 900.0), (2, 1900.0)], 1000.0) == 1
    assert spielende.absatz_finden([(0, 10.0)], 5000.0) is None


def test_spannen_beim_hochladen_nie_ein_fehler(client, world, dbs):
    from app.models import Upload
    w = world
    s = _laufende_runde(client, w)
    files = [{"fileName": "a.m4a", "sizeBytes": 1000, "mimeType": "audio/mp4"}]
    r = client.post(f"{API}/sessions/{s['id']}/uploads", headers=w["gm"],
                    json={"source": "table", "files": files, "spans": [{"startedAt": 3}, "x"]})
    assert r.status_code == 201, r.text
    assert dbs.get(Upload, r.json()["uploadId"]).spans is None


# ---------------------------------------------------------------- Pronomen
def test_pronomen_sichtbar_wie_name_und_recap(client, world, dbs):
    from app.models import GameSession
    from app.zusammenfassung import eingabe_bauen, recap_eingabe

    w = world
    oeff = _eintrag(client, w, name="Wirtin Hella", pronouns=" sie" + RTL + " ")
    assert oeff["pronouns"] == "sie"
    teil = _eintrag(client, w, name="Spion Rax", pronouns="er", hiddenFromMemberIds=[w["pl_member"]])
    r = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"],
                    json={"type": "npc", "name": "Zu lang", "pronouns": "y" * 33})
    assert r.status_code == 400
    assert client.get(f"{API}/entries/{teil['id']}", headers=w["pl"]).status_code == 404
    ch = {"id": "6f1c1f9e-2d1b-4c55-9a51-0d6a1d2f3b4c", "version": 1, "name": "Mira", "status": "active",
          "pronouns": "sie/ihr"}
    assert client.put(f"{API}/campaigns/{w['cid']}/members/me/character", headers=w["pl"], json=ch).status_code == 200
    mitglieder = {m["id"]: m for m in client.get(f"{API}/campaigns/{w['cid']}", headers=w["pl"]).json()["members"]}
    assert mitglieder[w["pl_member"]]["characterPronouns"] == "sie/ihr"
    # Recap-Eingabe: Pronomen nur bei allgemein bekannten Einträgen; nie Vorschläge, Merkstellen, Zähler
    _vorschlagen(client, w, title="MARKER-VORSCHLAG", detail="MARKER-VORSCHLAGSTEXT")
    s = _laufende_runde(client, w)
    client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={"note": "MARKER-MERKEN"})
    v = client.get(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"]).json()
    client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"],
                 json={"version": v["version"], "counterKinds": [{"id": "lep", "label": "MARKER-ZAEHLER"}]})
    client.put(f"{API}/campaigns/{w['cid']}/members/me/counters", headers=w["pl"],
               json={"values": [{"kindId": "lep", "value": 31337}]})
    ein = recap_eingabe(eingabe_bauen(dbs, dbs.get(GameSession, s["id"])))
    text = json.dumps(ein, ensure_ascii=False)
    for marker in ("MARKER-VORSCHLAG", "MARKER-MERKEN", "MARKER-ZAEHLER", "31337", "Spion Rax"):
        assert marker not in text
    hella = next(e for e in ein["bibel"] if e["name"] == "Wirtin Hella")
    assert hella["pronomen"] == "sie"
    mira = next(p for p in ein["personen"] if p["member_id"] == w["pl_member"])
    assert mira["pronomen"] == "sie/ihr"
    from app.sprachmodell import _kopf, _namenszeile

    assert "Mira (Pronomen: sie/ihr)" in _kopf(ein) and _namenszeile(hella) == "Wirtin Hella (Pronomen: sie)"


# ---------------------------------------------------------------- Zähler
def test_zaehler_nur_person_und_sl(client, world, make_user, login):
    w = world
    dora, dora_id = _zweite_spielerin(client, w, make_user, login)
    pfad = f"{API}/campaigns/{w['cid']}/members/me/counters"
    r = client.put(pfad, headers=w["pl"], json={"values": [{"kindId": "lep", "value": 3}]})
    assert r.status_code == 403 and r.json()["code"] == "counters_off"
    v = client.get(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"]).json()
    r = client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"], json={"version": v["version"],
                     "counterKinds": [{"id": "lep", "label": "LeP" + RTL, "hint": "Lebenspunkte"},
                                      {"id": "asp", "label": "AsP"}]})
    assert r.status_code == 200 and r.json()["counterKinds"][0] == {"id": "lep", "label": "LeP", "hint": "Lebenspunkte"}
    r = client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"], json={
        "version": r.json()["version"], "counterKinds": [{"id": "a", "label": "A"}, {"id": "a", "label": "B"}]})
    assert r.status_code == 400
    r = client.put(pfad, headers=w["pl"], json={"values": [{"kindId": "lep", "value": 28, "max": 32},
                                                           {"kindId": "asp", "value": 5}]})
    assert r.status_code == 200 and r.json()[0] == {"kindId": "lep", "value": 28, "max": 32}
    r = client.put(pfad, headers=w["pl"], json={"values": [{"kindId": "kap", "value": 1}]})
    assert r.status_code == 400 and r.json()["code"] == "counter_unknown"
    assert client.put(pfad, headers=w["pl"], json={"values": [{"kindId": "lep", "value": 100000}]}).status_code == 400
    assert client.put(pfad, headers=w["gm"], json={"values": []}).status_code == 403
    client.put(pfad, headers=dora, json={"values": [{"kindId": "lep", "value": 9}]})

    def mitglieder(h):
        return {m["id"]: m for m in client.get(f"{API}/campaigns/{w['cid']}", headers=h).json()["members"]}

    assert "counters" not in mitglieder(dora)[w["pl_member"]]  # Feld fehlt, nicht []
    assert mitglieder(dora)[dora_id]["counters"] == [{"kindId": "lep", "value": 9, "max": None}]
    assert len(mitglieder(w["pl"])[w["pl_member"]]["counters"]) == 2
    assert len(mitglieder(w["gm"])[w["pl_member"]]["counters"]) == 2
    assert "counters" not in mitglieder(w["gm"])[w["gm_member"]] or mitglieder(w["gm"])[w["gm_member"]]["counters"] == []
    # Art entfernen verwirft ihre Stände
    v = client.get(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"]).json()
    client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"],
                 json={"version": v["version"], "counterKinds": [{"id": "asp", "label": "AsP"}]})
    assert mitglieder(w["pl"])[w["pl_member"]]["counters"] == [{"kindId": "asp", "value": 5, "max": None}]
    log = client.get(f"{API}/campaigns/{w['cid']}/settings/log", headers=w["gm"]).json()
    assert log[0]["field"] == "counterKinds" and (log[0]["oldValue"], log[0]["newValue"]) == (2, 1)
    v = client.get(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"]).json()
    client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"],
                 json={"version": v["version"], "counterKinds": []})
    assert "counters" not in mitglieder(w["pl"])[w["pl_member"]]  # aus: Feld fehlt


# ---------------------------------------------------------------- 0.4.27: Gerätekennung
def test_geraetekennung_bei_der_anmeldung(client, world, dbs):
    from sqlalchemy import select

    from app.models import BekanntesGeraet, User

    ben = dbs.scalar(select(User).where(User.username == "ben"))
    vorher = dbs.query(BekanntesGeraet).filter_by(user_id=ben.id).count()
    for kennung in ("A" * 20, "A" * 20, "kurz", None, 12345):
        r = client.post(f"{API}/auth/login", json={"username": "ben", "password": "geheim123", "deviceId": kennung})
        assert r.status_code == 200, r.text
    dbs.expire_all()
    app_geraete = dbs.query(BekanntesGeraet).filter_by(user_id=ben.id, ort="app").count()
    assert app_geraete == 1 and dbs.query(BekanntesGeraet).filter_by(user_id=ben.id).count() >= vorher + 1


def test_umzug_nimmt_nichts_davon_mit(client, world, dbs):
    from app import umzug
    from app.models import Campaign

    w = world
    _vorschlagen(client, w, title="MARKER-VORSCHLAG")
    s = _laufende_runde(client, w)
    client.post(f"{API}/sessions/{s['id']}/bookmarks", headers=w["pl"], json={"note": "MARKER-MERKEN"})
    v = client.get(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"]).json()
    client.patch(f"{API}/campaigns/{w['cid']}/settings", headers=w["gm"],
                 json={"version": v["version"], "counterKinds": [{"id": "lep", "label": "MARKER-ZAEHLER"}]})
    client.put(f"{API}/campaigns/{w['cid']}/members/me/counters", headers=w["pl"],
               json={"values": [{"kindId": "lep", "value": 31337}]})
    _eintrag(client, w, name="Wirtin Hella", pronouns="sie")
    dbs.expire_all()
    paket, _dateien = umzug.packliste(dbs, dbs.get(Campaign, w["cid"]), {w["pl_member"]})
    text = json.dumps(paket, ensure_ascii=False, default=str)
    for marker in ("MARKER-VORSCHLAG", "MARKER-MERKEN", "MARKER-ZAEHLER", "31337"):
        assert marker not in text
    assert '"pronouns": "sie"' in text  # Pronomen gehören zum Eintrag und ziehen mit
