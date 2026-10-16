"""Schnittstelle 0.4.21: „Auch genannt“ (aliases), Dublettenprüfung (Proposal.match) und „Dort ergänzen“
(mergeIntoEntryId), Geheimes im Kapitel (Recap.gmWarnings), Stimmproben (Speaker.samples, sample?index) und
Anrede-Hinweis (Speaker.hint). Die Prüfkriterien (a)–(e) der Sicherheitsprüfung stehen bei den Tests."""
from fastapi.testclient import TestClient

from tests.test_step2a import API, audio_abschnitte, hochladen, neue_session, worker_token
from tests.test_step2b import transkribiert, zusammenfassen
from tests.test_step4 import IntroMotor, _kleine_teile  # noqa: F401 (Fixture)
from tests.test_step5 import api, einstellen, mit_geheimnissen  # noqa: F401 (Fixture)


def _eintrag(client, w, **felder):
    daten = {"type": "npc", "visibility": "public", "summary": "Ein Mann."} | felder
    r = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"], json=daten)
    assert r.status_code == 201, r.text
    return r.json()


def _namen(client, w, h, q=None):
    r = client.get(f"{API}/campaigns/{w['cid']}/entries", headers=h, params={"q": q} if q else None)
    return {e["name"]: e for e in r.json()}


# ---------------------------------------------------------------- aliases
def test_aliases_sichtbar_wie_der_name(client, world):
    w = world
    e = _eintrag(client, w, name="Tarvok", aliases=[" Tharvok ", "tarvok", "Tharvok", "der Alte", "", "x" * 60])
    assert e["aliases"] == ["Tharvok", "der Alte", "x" * 40]  # bereinigt: Leerraum, Doppelte, der Name selbst, Länge
    # Spieler sehen Aliasse öffentlicher Einträge und finden sie über die Suche – (c) nur, was sie sehen dürfen
    assert _namen(client, w, w["pl"])["Tarvok"]["aliases"] == e["aliases"]
    assert set(_namen(client, w, w["pl"], q="tharvok")) == {"Tarvok"}
    geheim = _eintrag(client, w, name="Die Schwarze Hand", visibility="gm_only", aliases=["Varek"])
    verborgen = _eintrag(client, w, name="Großmutter Ilse", aliases=["Ilse"], hiddenFromMemberIds=[w["pl_member"]])
    assert set(_namen(client, w, w["pl"], q="varek")) == set() and set(_namen(client, w, w["pl"], q="ilse")) == set()
    assert client.get(f"{API}/entries/{geheim['id']}", headers=w["pl"]).status_code == 404
    assert client.get(f"{API}/entries/{verborgen['id']}", headers=w["pl"]).status_code == 404
    assert set(_namen(client, w, w["gm"], q="varek")) == {"Die Schwarze Hand"}
    # Umbenennen auf einen Alias nimmt ihn aus der Liste; Liste ganz ersetzen
    r = client.patch(f"{API}/entries/{e['id']}", headers=w["gm"], json={"name": "Tharvok"})
    assert r.json()["aliases"] == ["der Alte", "x" * 40]
    r = client.patch(f"{API}/entries/{e['id']}", headers=w["gm"], json={"aliases": []})
    assert r.json()["aliases"] == []
    # Namenshilfe: Aliasse direkt nach dem Namen
    hotwords = client.get(f"{API}/campaigns/{w['cid']}", headers=w["gm"]).json()["hotwords"]
    assert hotwords.index("Die Schwarze Hand") + 1 == hotwords.index("Varek")
    assert "hotwords" not in client.get(f"{API}/campaigns/{w['cid']}", headers=w["pl"]).json()


def test_aliases_mehr_als_zehn_abgelehnt(client, world):
    w = world
    r = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"],
                    json={"type": "npc", "name": "Viele", "aliases": [f"Name{i}" for i in range(11)]})
    assert r.status_code == 400


# ---------------------------------------------------------------- match und mergeIntoEntryId
def test_dublettenpruefung_ohne_sprachmodell():
    from app import dubletten

    class E:
        def __init__(s, id, type, name, aliases=()):
            s.id, s.type, s.name, s.alle_namen = id, type, name, [name, *aliases]

    eintraege = [E("1", "npc", "Tarvok"), E("2", "npc", "Hauptmann Varek"), E("3", "location", "Rabenfels"),
                 E("4", "pc", "Mira"), E("5", "npc", "Lysander"), E("6", "npc", "die Alte", ["Großmutter Ilse"])]
    allgemein = frozenset({"alte", "mühle", "bauer"})
    treffer = {t: dubletten.finden(t, eintraege, allgemein) for t in
               ("Tharvok", "Darvok", "Varek", "Rabenfeld", "Mira", "Lysandra", "Ilse", "Alte Mühle", "Bauer", "Kuno")}
    assert (treffer["Tharvok"]["kind"], treffer["Tharvok"]["entryId"]) == ("spelling", "1")
    assert (treffer["Darvok"]["kind"], treffer["Darvok"]["matchedName"]) == ("sound", "Tarvok")
    assert (treffer["Varek"]["kind"], treffer["Varek"]["entryId"]) == ("part_name", "2")
    assert treffer["Rabenfeld"]["kind"] == "spelling" and treffer["Lysandra"]["entryId"] == "5"
    assert treffer["Ilse"]["matchedName"] == "Großmutter Ilse"
    assert treffer["Mira"] is None  # Spielercharaktere sind nie Ziel
    assert treffer["Alte Mühle"] is None and treffer["Bauer"] is None and treffer["Kuno"] is None  # keine Fehltreffer


def test_match_und_dort_ergaenzen(client, world, dbs, tmp_path, api, make_user, login):  # noqa: F811
    w = world
    s, oeff, geheim, teilweise = mit_geheimnissen(client, w, dbs, tmp_path)
    tarvok = _eintrag(client, w, name="Tarvok", summary="Ein Söldner.")
    api.vorschlaege = [
        {"entryType": "npc", "action": "create", "title": "Tharvok", "detail": "Trägt eine Narbe.",
         "suggestedVisibility": "public", "confidence": 0.9, "evidence": [{"start": "0:40", "quote": "Wir reiten"}]},
        {"entryType": "npc", "action": "create", "title": "Der Graue Fürst", "detail": "Will den Thron.",
         "suggestedVisibility": "public", "confidence": 0.9},
        {"entryType": "npc", "action": "create", "title": "Kuno", "detail": "Ein Bauer.", "suggestedVisibility": "public"},
        {"entryType": "location", "action": "update", "targetEntryId": oeff["id"], "title": "Rabenfels",
         "detail": "Markt am Sonntag.", "suggestedVisibility": "public"},
    ]
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234", weg="abschrift")
    assert zusammenfassen(dbs)
    vorschlaege = {p["title"]: p for p in client.get(f"{API}/sessions/{s['id']}/proposals", headers=w["gm"]).json()}
    assert vorschlaege["Tharvok"]["match"] == {"entryId": tarvok["id"], "name": "Tarvok", "matchedName": "Tarvok",
                                               "entryType": "npc", "kind": "spelling"}
    assert vorschlaege["Der Graue Fürst"]["match"]["entryId"] == geheim["id"]  # auch gm_only – nur die SL sieht es
    assert vorschlaege["Kuno"]["match"] is None and vorschlaege["Rabenfels"]["match"] is None
    # (b) fremde Kampagne wie unbekannt; nur action create
    make_user("dora")
    fremd = client.post(f"{API}/campaigns", json={"title": "Woanders"}, headers=login("dora")).json()
    fremder_eintrag = client.post(f"{API}/campaigns/{fremd['id']}/entries", headers=login("dora"),
                                  json={"type": "npc", "name": "Tarvok"}).json()
    for ziel, pid in ((fremder_eintrag["id"], vorschlaege["Tharvok"]["id"]), ("gibtsnicht", vorschlaege["Tharvok"]["id"]),
                      (oeff["id"], vorschlaege["Rabenfels"]["id"])):
        r = client.patch(f"{API}/proposals/{pid}", headers=w["gm"], json={"mergeIntoEntryId": ziel})
        assert r.status_code == 400 and r.json()["code"] == "invalid_input", r.text
    # Ergänzen: öffentlich → öffentlich
    r = client.patch(f"{API}/proposals/{vorschlaege['Tharvok']['id']}", headers=w["gm"],
                     json={"mergeIntoEntryId": tarvok["id"], "decision": "accepted"})
    assert r.status_code == 200 and r.json()["mergeIntoEntryId"] == tarvok["id"]
    # (a) ein gm_only-Vorschlag in einen öffentlichen Eintrag: Text und Titel landen nur in den SL-Notizen
    r = client.patch(f"{API}/proposals/{vorschlaege['Kuno']['id']}", headers=w["gm"],
                     json={"mergeIntoEntryId": oeff["id"], "visibility": "gm_only", "decision": "accepted"})
    assert r.status_code == 200
    client.patch(f"{API}/proposals/{vorschlaege['Der Graue Fürst']['id']}", headers=w["gm"],
                 json={"decision": "rejected"})
    client.patch(f"{API}/proposals/{vorschlaege['Rabenfels']['id']}", headers=w["gm"], json={"decision": "rejected"})
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    namen_sl = _namen(client, w, w["gm"])
    assert "Tharvok" not in namen_sl and "Kuno" not in namen_sl  # kein neuer Eintrag
    t = namen_sl["Tarvok"]
    assert t["aliases"] == ["Tharvok"] and "Trägt eine Narbe." in t["summary"]
    rabenfels = namen_sl["Rabenfels"]
    assert "Kuno" in rabenfels["gmNotes"] and "Ein Bauer." in rabenfels["gmNotes"]
    assert "Kuno" not in rabenfels["summary"] and rabenfels["aliases"] == []
    spieler = _namen(client, w, w["pl"])
    assert "Kuno" not in spieler["Rabenfels"]["summary"] and spieler["Rabenfels"]["aliases"] == []
    assert spieler["Tarvok"]["aliases"] == ["Tharvok"]


# ---------------------------------------------------------------- gmWarnings
def test_gm_warnings_nur_fuer_die_sl(client, world, dbs, tmp_path, api):  # noqa: F811
    from sqlalchemy import update

    from app.models import Recap

    w = world
    s, oeff, geheim, teilweise = mit_geheimnissen(client, w, dbs, tmp_path)
    client.patch(f"{API}/entries/{geheim['id']}", headers=w["gm"],
                 json={"aliases": ["Grauer"], "gmNotes": "Er plant den Sturz des Königs im Herbst des nächsten Jahres."})
    api.vorschlaege = [{"entryType": "npc", "action": "reveal", "targetEntryId": geheim["id"], "title": "Der Graue Fürst",
                        "detail": "Ein Adliger in Grau.", "confidence": 0.8}]
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234", weg="abschrift")
    assert zusammenfassen(dbs)
    recap = client.get(f"{API}/sessions/{s['id']}/recap", headers=w["gm"]).json()
    assert recap["gmWarnings"] == []
    # Name des geheimen Eintrags, ein Alias, der Name des teilweise verborgenen Eintrags und Wörtliches aus gmNotes
    text = ("Die Gruppe ritt nach Rabenfels und traf den Grauen Fürst.\n\nDer Grauer sprach mit den Verschwörern.\n\n"
            "Er plant den Sturz des Königs im Herbst des nächsten Jahres, sagte der Wirt.")
    dbs.execute(update(Recap).where(Recap.session_id == s["id"]).values(text=text))
    dbs.commit()
    recap = client.get(f"{API}/sessions/{s['id']}/recap", headers=w["gm"]).json()
    hinweise = {(h["code"], h["entryIds"][0]): h for h in recap["gmWarnings"]}
    assert hinweise[("secret_entry_name", geheim["id"])]["paragraphs"] == [0, 1]
    assert set(hinweise[("secret_entry_name", geheim["id"])]["names"]) == {"Der Graue Fürst", "Grauer"}
    assert hinweise[("secret_entry_name", teilweise["id"])]["paragraphs"] == [1]
    assert hinweise[("secret_wording", geheim["id"])]["paragraphs"] == [2]
    assert ("secret_entry_name", oeff["id"]) not in hinweise  # öffentlich und allgemein bekannt
    # Ein angenommener reveal-Vorschlag deckt den Eintrag auf → kein Hinweis mehr auf den Namen
    r = client.get(f"{API}/sessions/{s['id']}/proposals", headers=w["gm"]).json()
    reveal = next(p for p in r if p["action"] == "reveal")
    client.patch(f"{API}/proposals/{reveal['id']}", headers=w["gm"], json={"decision": "accepted"})
    hinweise = {(h["code"], h["entryIds"][0]) for h in
                client.get(f"{API}/sessions/{s['id']}/recap", headers=w["gm"]).json()["gmWarnings"]}
    assert ("secret_entry_name", geheim["id"]) not in hinweise and ("secret_wording", geheim["id"]) in hinweise
    # (d) Spieler bekommen das Feld nie – auch nicht leer, auch nicht im veröffentlichten Kapitel
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    spieler = client.get(f"{API}/sessions/{s['id']}/recap", headers=w["pl"]).json()
    assert "gmWarnings" not in spieler and "review" not in spieler
    assert "gmWarnings" in client.get(f"{API}/sessions/{s['id']}/recap", headers=w["gm"]).json()


# ---------------------------------------------------------------- Stimmproben und Hinweis
class ProbenMotor(IntroMotor):
    """Drei Abschnitte je Stimme über die Runde verteilt, einer nennt die Figur."""

    def transkribieren(self, dauer, sprache, hotwords, fortschritt):
        return [{"start": 1.0, "end": 5.0, "text": " Ich bin Anna, ich leite heute."},
                {"start": 6.0, "end": 10.0, "text": " Hallo, ich spiele Mira."},
                {"start": 11.0, "end": 15.0, "text": " Ihr seht eine dunkle Taverne vor euch."},
                {"start": 16.0, "end": 20.0, "text": " Mira schaut sich erst einmal um."},
                {"start": 21.0, "end": 25.0, "text": " Der Wirt kommt auf euch zu."},
                {"start": 26.0, "end": 30.0, "text": " Ich bestelle einen Met."}]

    def sprecher_trennen(self, dauer, segmente, min_n, max_n, fortschritt):
        for s, sp in zip(segmente, ["SPEAKER_00", "SPEAKER_01"] * 3):
            s["speaker"] = sp
        return segmente, {"SPEAKER_00": [0.1], "SPEAKER_01": [0.2]}


def test_stimmproben_und_hoerproben(client, world, dbs, tmp_path):
    from app.transkription import verarbeiter
    from app.worker_prozess import WorkerProzess

    w = world
    s = neue_session(client, w)
    hochladen(client, w["gm"], s["id"], audio_abschnitte(tmp_path, (31,)))
    knecht = WorkerProzess("http://testserver", worker_token(dbs), tmp_path / "k", verarbeiter(ProbenMotor()),
                           client=TestClient(client.app), claim_wait=0)
    assert knecht.einen_auftrag()
    stimmen = client.get(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"]).json()
    assert len(stimmen) == 2
    for sp in stimmen:
        assert len(sp["samples"]) == 3 and sp["samples"][0]["text"] == sp["sampleText"]
        assert [p["start"] for p in sp["samples"]] == sorted(p["start"] for p in sp["samples"])
        assert sp["hint"] is None
        basis = f"{API}/sessions/{s['id']}/speakers/{sp['id']}/sample"
        for index in (None, 0, 1, 2):
            r = client.get(basis, headers=w["gm"], params={"index": index} if index is not None else None)
            assert r.status_code == 200 and r.headers["content-type"].startswith("audio/ogg"), index
        # (e) außerhalb der Liste bzw. ungültig → 404 (laut YAML); Spieler 404
        for falsch in (3, -1, "x", "01x", "1.0"):
            assert client.get(basis, headers=w["gm"], params={"index": falsch}).status_code == 404, falsch
        assert client.get(basis, headers=w["pl"], params={"index": 1}).status_code == 404
    # Nach der Bestätigung sind die Hörproben weg (410), die Textproben bleiben
    client.put(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"], json=[])
    sp = client.get(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"]).json()[0]
    assert len(sp["samples"]) == 3
    assert client.get(f"{API}/sessions/{s['id']}/speakers/{sp['id']}/sample", headers=w["gm"],
                      params={"index": 1}).status_code == 410


def test_stimmproben_waehlen_namen_zuerst():
    from app.transkription import proben_waehlen

    segs = [{"start": float(i * 10), "end": float(i * 10 + 5), "text": t} for i, t in enumerate(
        ["Guten Abend zusammen.", "Lysander zieht sein Schwert.", "Wir gehen weiter.", "Noch ein Satz hier.",
         "Mira lacht laut.", "Ende der Runde."])]
    proben = proben_waehlen(segs, ("Lysander", "Mira"))
    assert [p["text"] for p in proben] == ["Lysander zieht sein Schwert.", "Noch ein Satz hier.", "Mira lacht laut."]
    assert proben_waehlen([], ()) == [] and len(proben_waehlen(segs[:1], ())) == 1


def test_anrede_hinweis(client, world, dbs, tmp_path):
    from sqlalchemy import select

    from app.models import Speaker, TranscriptSegment

    w = world
    s = transkribiert(client, w, dbs, tmp_path)
    stimmen = list(dbs.scalars(select(Speaker).where(Speaker.session_id == s["id"]).order_by(Speaker.position)))
    assert len(stimmen) >= 2
    sl, spieler = stimmen[0], stimmen[1]
    sl.suggested_member_id, spieler.suggested_member_id = w["pl_member"], w["gm_member"]  # vertauscht
    zeilen = [(sl.id, 100.0, "Mira, was machst du jetzt?"), (spieler.id, 101.0, "Ich sehe mich um."),
              (sl.id, 110.0, "Und du, Mira?"), (spieler.id, 111.0, "Ich warte."),
              (spieler.id, 120.0, "Mein Name ist Mira.")]
    for i, (sp_id, start, text) in enumerate(zeilen):
        dbs.add(TranscriptSegment(session_id=s["id"], position=50 + i, start=start, end=start + 0.8,
                                  speaker_id=sp_id, text=text))
    dbs.commit()
    aus = {x["id"]: x for x in client.get(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"]).json()}
    assert aus[spieler.id]["hint"] == {"code": "addressed_as", "name": "Mira", "memberId": w["pl_member"], "count": 2}
    assert aus[sl.id]["hint"] is None  # die SL-Stimme wird nicht angesprochen; kein Widerspruch zu melden
    # Zuordnung passt → kein Hinweis
    sl.suggested_member_id, spieler.suggested_member_id = w["gm_member"], w["pl_member"]
    dbs.commit()
    aus = client.get(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"]).json()
    assert all(x["hint"] is None for x in aus)
