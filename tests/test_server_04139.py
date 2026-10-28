"""Server 0.4.139 (Schnittstelle 0.4.26): Wer hat den Gegenstand? (Proposal.holderMemberId/holderCleared) und private
Kommentare an mehrere Personen (recipientMemberIds)."""
from tests.test_schnittstelle_0421 import _eintrag, _namen
from tests.test_step2a import API
from tests.test_step2b import zusammenfassen
from tests.test_step4 import _kleine_teile  # noqa: F401 (Fixture)
from tests.test_step5 import api, einstellen, mit_geheimnissen  # noqa: F401 (Fixture)


def _vorschlaege(client, w, s):
    return {p["title"]: p for p in client.get(f"{API}/sessions/{s['id']}/proposals", headers=w["gm"]).json()}


def _beute(client, w, dbs, tmp_path, api):  # noqa: F811
    s, oeff, _geheim, _t = mit_geheimnissen(client, w, dbs, tmp_path)
    karte = _eintrag(client, w, type="item", name="Karte des Moores", summary="Zeigt den Pfad.")
    fackel = _eintrag(client, w, type="item", name="Silberfackel", summary="Brennt kalt.",
                      holderMemberId=w["pl_member"])
    api.vorschlaege = [
        {"entryType": "item", "action": "create", "title": "Runenschlüssel", "detail": "Öffnet die Gruft.",
         "suggestedVisibility": "public", "holder": "mira", "holderGone": True},  # holderGone bei create: weg
        {"entryType": "item", "action": "create", "title": "Kupferring", "detail": "Ein Ring.",
         "suggestedVisibility": "public", "holder": "Kalgor der Fremde"},  # keine Spielerfigur: kein Halter
        {"entryType": "item", "action": "update", "targetEntryId": karte["id"], "title": "Karte des Moores",
         "detail": "Mira steckt sie ein.", "suggestedVisibility": "public", "holder": "Mira"},
        {"entryType": "item", "action": "update", "targetEntryId": fackel["id"], "title": "Silberfackel",
         "detail": "Im Moor versunken.", "suggestedVisibility": "public", "holderGone": True, "holder": "Mira"},
        {"entryType": "npc", "action": "create", "title": "Kuno", "detail": "Ein Bauer.",
         "suggestedVisibility": "public", "holder": "Mira"},
    ]
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234", weg="abschrift")
    assert zusammenfassen(dbs)
    return s, karte, fackel, oeff


def test_halter_aus_der_runde_aufgeloest_und_veroeffentlicht(client, world, dbs, tmp_path, api):  # noqa: F811
    w = world
    s, karte, fackel, _oeff = _beute(client, w, dbs, tmp_path, api)
    v = _vorschlaege(client, w, s)
    assert v["Runenschlüssel"]["holderMemberId"] == w["pl_member"] and v["Runenschlüssel"]["holderCleared"] is False
    assert v["Kupferring"]["holderMemberId"] is None  # nie geraten
    assert v["Karte des Moores"]["holderMemberId"] == w["pl_member"]
    assert v["Silberfackel"]["holderCleared"] is True and v["Silberfackel"]["holderMemberId"] is None
    assert v["Kuno"]["holderMemberId"] is None and v["Kuno"]["holderCleared"] is False
    for t in v:
        client.patch(f"{API}/proposals/{v[t]['id']}", headers=w["gm"], json={"decision": "accepted"})
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    e = _namen(client, w, w["gm"])
    assert e["Runenschlüssel"]["holderMemberId"] == w["pl_member"]
    assert e["Karte des Moores"]["holderMemberId"] == w["pl_member"]
    assert e["Silberfackel"]["holderMemberId"] is None
    assert e["Kupferring"]["holderMemberId"] is None and e["Kuno"]["holderMemberId"] is None


def test_halter_auflösen_nur_eindeutig(world, dbs):
    from app.beute import Halter
    from app.models import Member

    m = dbs.get(Member, world["pl_member"])
    m.character_name, m.character_nickname = "Mira Dornhecke", "Dorn"
    dbs.commit()
    h = Halter(dbs, world["cid"])
    assert h.aufloesen("Mira Dornhecke") == h.aufloesen("mira") == h.aufloesen("DORN") == m.id
    assert h.aufloesen("Anna") is None  # die SL ist nie Halterin
    assert h.aufloesen("Mi") is None and h.aufloesen("") is None and h.aufloesen("Mira Sonnenschein") is None


def test_sl_aendert_den_halter(client, world, dbs, tmp_path, api, make_user, login):  # noqa: F811
    w = world
    s, _karte, _fackel, _oeff = _beute(client, w, dbs, tmp_path, api)
    v = _vorschlaege(client, w, s)
    pid = v["Runenschlüssel"]["id"]
    for falsch in (w["gm_member"], "gibtsnicht"):  # SL bzw. Unbekannte: holder_unknown
        r = client.patch(f"{API}/proposals/{pid}", headers=w["gm"], json={"holderMemberId": falsch})
        assert r.status_code == 400 and r.json()["code"] == "holder_unknown"
    r = client.patch(f"{API}/proposals/{pid}", headers=w["gm"], json={"holderCleared": True})  # create
    assert r.status_code == 400 and r.json()["code"] == "invalid_input"
    r = client.patch(f"{API}/proposals/{v['Kuno']['id']}", headers=w["gm"], json={"holderMemberId": w["pl_member"]})
    assert r.status_code == 400 and r.json()["code"] == "invalid_input"  # kein Gegenstand
    r = client.patch(f"{API}/proposals/{pid}", headers=w["gm"], json={"holderMemberId": None})
    assert r.status_code == 200 and r.json()["holderMemberId"] is None
    karte = v["Karte des Moores"]["id"]
    r = client.patch(f"{API}/proposals/{karte}", headers=w["gm"], json={"holderCleared": True})
    assert r.status_code == 200 and r.json()["holderCleared"] is True and r.json()["holderMemberId"] is None
    r = client.patch(f"{API}/proposals/{karte}", headers=w["gm"], json={"holderMemberId": w["pl_member"]})
    assert r.json()["holderCleared"] is False and r.json()["holderMemberId"] == w["pl_member"]
    # mit „Dort ergänzen“ darf ein neuer Gegenstand den Halter verlieren; ohne Ziel fällt das zurück
    ring = v["Kupferring"]["id"]
    r = client.patch(f"{API}/proposals/{ring}", headers=w["gm"],
                     json={"mergeIntoEntryId": _vorschlaege(client, w, s)["Silberfackel"]["targetEntryId"],
                           "holderCleared": True})
    assert r.status_code == 200 and r.json()["holderCleared"] is True
    r = client.patch(f"{API}/proposals/{ring}", headers=w["gm"], json={"mergeIntoEntryId": None})
    assert r.json()["holderCleared"] is False
    # Spielende sehen Vorschläge nie
    assert client.patch(f"{API}/proposals/{pid}", headers=w["pl"], json={"holderMemberId": w["pl_member"]}
                        ).status_code == 404


def test_umleitung_in_gmnotes_aendert_den_halter_nicht(client, world, dbs, tmp_path, api):  # noqa: F811
    w = world
    s, karte, fackel, _oeff = _beute(client, w, dbs, tmp_path, api)
    v = _vorschlaege(client, w, s)
    for t in v:
        if t != "Silberfackel":
            client.patch(f"{API}/proposals/{v[t]['id']}", headers=w["gm"], json={"decision": "rejected"})
    # geheim gemacht: Text nur in den SL-Notizen, und Spieler sollen auch über den Halter nichts merken
    r = client.patch(f"{API}/proposals/{v['Silberfackel']['id']}", headers=w["gm"],
                     json={"visibility": "gm_only", "decision": "accepted"})
    assert r.status_code == 200
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    f = _namen(client, w, w["pl"])["Silberfackel"]
    assert f["holderMemberId"] == w["pl_member"] and "versunken" not in f["summary"]


# ---------------------------------------------------------------- Kommentare an mehrere
def _dritte(client, w, make_user, login):
    make_user("dora")
    h = login("dora")
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    j = client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Ilsabet"}, headers=h).json()
    return h, {m["displayName"]: m["id"] for m in j["members"]}["Dora"]


def _kapitel(client, w, dbs, tmp_path):
    from tests.test_step2b import transkribiert

    return transkribiert(client, w, dbs, tmp_path)


def test_privat_an_mehrere(client, world, dbs, tmp_path, make_user, login):
    w = world
    dora, dora_id = _dritte(client, w, make_user, login)
    make_user("emil")
    emil = login("emil")
    code = client.post(f"{API}/campaigns/{w['cid']}/invites", headers=w["gm"]).json()["code"]
    client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Ruk"}, headers=emil)
    s = _kapitel(client, w, dbs, tmp_path)
    url = f"{API}/sessions/{s['id']}/comments"
    r = client.post(url, headers=w["gm"], json={"text": "Nur für euch zwei.",
                                                 "recipientMemberIds": [w["pl_member"], dora_id],
                                                 "recipientMemberId": None})
    assert r.status_code == 201, r.text
    k = r.json()
    assert k["recipientMemberIds"] == [w["pl_member"], dora_id] and k["recipientMemberId"] == w["pl_member"]
    for h, sieht in ((w["gm"], True), (w["pl"], True), (dora, True), (emil, False)):
        liste = client.get(url, headers=h)
        if liste.status_code == 404:  # Spieler sehen unveröffentlichte Kapitel nicht – für die SL reicht das hier
            continue
        ids = [x["id"] for x in liste.json()]
        assert (k["id"] in ids) == sieht
    # öffentlich: leere Liste und alter Weg mit recipientMemberId
    assert client.post(url, headers=w["gm"], json={"text": "Alle.", "recipientMemberIds": []}).json()[
        "recipientMemberIds"] == []
    alt = client.post(url, headers=w["gm"], json={"text": "Wie früher.", "recipientMemberId": dora_id}).json()
    assert alt["recipientMemberIds"] == [dora_id] and alt["recipientMemberId"] == dora_id
    # Fehler: doppelt, an sich selbst, fremd, mehr als 20
    for falsch in ([dora_id, dora_id], [w["gm_member"]], ["gibtsnicht"], [dora_id] * 21):
        r = client.post(url, headers=w["gm"], json={"text": "x", "recipientMemberIds": falsch})
        assert r.status_code == 400 and r.json()["code"] == "recipient_unknown", falsch


def test_spieler_nur_an_die_sl_und_sichtbarkeit_im_veroeffentlichten_kapitel(client, world, dbs, tmp_path, make_user,
                                                                             login):
    from app.models import Comment, CommentRecipient, GameSession

    w = world
    dora, dora_id = _dritte(client, w, make_user, login)
    s = _kapitel(client, w, dbs, tmp_path)
    g = dbs.get(GameSession, s["id"])
    g.state = "published"
    dbs.commit()
    url = f"{API}/sessions/{s['id']}/comments"
    r = client.post(url, headers=w["pl"], json={"text": "x", "recipientMemberIds": [w["gm_member"], dora_id]})
    assert r.status_code == 403
    k = client.post(url, headers=w["gm"], json={"text": "Für Ben und Dora.",
                                                 "recipientMemberIds": [dora_id, w["pl_member"]]}).json()
    assert [x["id"] for x in client.get(url, headers=w["pl"]).json()] == [k["id"]]
    assert client.get(url, headers=dora).json()[0]["recipientMemberIds"] == [dora_id, w["pl_member"]]
    assert client.patch(f"{API}/comments/{k['id']}", headers=w["pl"], json={"text": "y"}).status_code == 403
    assert client.delete(f"{API}/comments/{k['id']}", headers=w["pl"]).status_code == 403  # sichtbar, aber nicht eigen
    assert dbs.query(CommentRecipient).filter_by(comment_id=k["id"]).count() == 2
    assert client.delete(f"{API}/comments/{k['id']}", headers=w["gm"]).status_code == 204
    dbs.expire_all()
    assert dbs.get(Comment, k["id"]) is None and dbs.query(CommentRecipient).filter_by(comment_id=k["id"]).count() == 0


def test_umzugsdatei_kennt_mehrere_empfaenger():
    from app.umzug import _Kommentar

    neu = _Kommentar.model_validate({"author": "p1", "recipients": ["p2", "p3", "p2"], "text": "x",
                                     "createdAt": "2026-10-28T10:00:00Z"})
    alt = _Kommentar.model_validate({"author": "p1", "recipient": "p2", "text": "x", "createdAt": "2026-10-28T10:00:00Z"})
    assert neu.alle_empfaenger == ["p2", "p3"] and alt.alle_empfaenger == ["p2"]


def test_api_version():
    from app.routers.auth import API_VERSION

    assert API_VERSION == "0.4.26"
