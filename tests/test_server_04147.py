"""Server 0.4.147 (Issues aus dem Testdurchlauf 03.11.): Vorschläge aus der Weltbeschreibung bzw. mit Namen, die in der
Abschrift nie fallen, gelten nicht als belegt; Kapiteltitel ohne „Kapitel 2:“; leere Runden ohne Aufnahme verschwinden
nach einer Woche; Eigennamen aus Kampagnentitel und Welt für die Schreibhilfe."""
import json
from datetime import timedelta

from tests.test_step2a import API, neue_session
from tests.test_step2b import zusammenfassen
from tests.test_step4 import _kleine_teile  # noqa: F401 (Fixture)
from tests.test_step5 import api, einstellen, mit_geheimnissen  # noqa: F401 (Fixture)


def test_belege_name_und_herkunft():
    from app import belege

    t = belege.Transkript([(0.0, "Wir reiten nach Nostrid und sehen die Abtei am Fluss")])
    assert belege.name_vorkommt(t, "Nostria")  # Erkennungsfehler gilt als derselbe Name
    assert belege.name_vorkommt(t, "Alte Abtei") and belege.name_vorkommt(t, "Der Ox")  # zu kurz: nicht prüfbar
    assert not belege.name_vorkommt(t, "Kaltenfurt")
    welt = "Die Gruppe erreicht Kaltenfurt. Eine verschwundene Fähre blockiert den Weg zur alten Abtei."
    assert belege.aus_text("Die Gruppe erreicht Kaltenfurt.", welt)
    assert belege.aus_text("Am Ufer. Eine verschwundene Fähre blockiert den Weg zur alten Abtei!", welt)
    assert not belege.aus_text("Die Gruppe erreicht den Fluss.", welt) and not belege.aus_text("Kaltenfurt", welt)


def test_vorschlag_aus_der_welt_gilt_nicht_als_belegt(client, world, dbs, tmp_path, api):  # noqa: F811
    from app.models import Campaign, Proposal, Recap

    w = world
    c = dbs.get(Campaign, w["cid"])
    c.world_info = "Die Gruppe erreicht Kaltenfurt. Eine verschwundene Fähre blockiert den Weg zur alten Abtei."
    dbs.commit()
    s, _oeff, _geheim, _t = mit_geheimnissen(client, w, dbs, tmp_path)
    echt = [{"start": "0:40", "quote": "Wir reiten nach Rabenfels"}]  # echter Satz, aber nicht über Kaltenfurt
    api.vorschlaege = [
        {"entryType": "location", "action": "create", "title": "Kaltenfurt", "detail": "Die Gruppe erreicht Kaltenfurt.",
         "suggestedVisibility": "public", "confidence": 0.9, "evidence": echt},
        {"entryType": "npc", "action": "create", "title": "Der Graue Fürst", "detail": "Wartet in Rabenfels.",
         "suggestedVisibility": "public", "confidence": 0.9, "evidence": echt},
        {"entryType": "location", "action": "create", "title": "Fährhaus", "detail": "Eine verschwundene Fähre "
         "blockiert den Weg zur alten Abtei.", "suggestedVisibility": "public", "confidence": 0.9, "evidence": echt},
    ]
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234", weg="abschrift")
    assert zusammenfassen(dbs)
    v = {p.title: p for p in dbs.query(Proposal).filter_by(session_id=s["id"])}
    for titel in ("Kaltenfurt", "Fährhaus"):
        flags = json.loads(v[titel].flags)
        assert "evidence_not_found" in flags and "low_confidence" in flags, titel
        assert v[titel].confidence <= 0.3 and json.loads(v[titel].evidence) == []
    assert "evidence_not_found" not in json.loads(v["Der Graue Fürst"].flags)
    assert json.loads(v["Der Graue Fürst"].evidence)
    # Titel ohne Nummer – die App setzt „Kapitel 1“ selbst davor
    assert dbs.get(Recap, s["id"]).title == "Der Ritt"


def test_titel_ohne_nummer():
    from app.zusammenfassung import titel_ohne_nummer as t

    assert t("Kapitel 2: Der Krater und die Grünkappen") == "Der Krater und die Grünkappen"
    assert t("Chapter 12 – Fog over the Moor") == "Fog over the Moor"
    assert t("Kapitel 3") == "Kapitel 3" and t("Der Weg nach Kapitel 2") == "Der Weg nach Kapitel 2"


def test_leere_runden_verschwinden_nach_einer_woche(client, world, dbs):
    from app import aufbewahrung
    from app.db import utcnow
    from app.models import GameSession

    w = world
    leer = neue_session(client, w)
    mit_notiz = neue_session(client, w)
    client.put(f"{API}/sessions/{mit_notiz['id']}/gm-note", headers=w["gm"], json={"text": "Vorbereitung"})
    danach = neue_session(client, w)
    assert aufbewahrung.leere_runden_entfernen(dbs) == 0  # zu frisch
    for sid in (leer["id"], mit_notiz["id"], danach["id"]):
        dbs.get(GameSession, sid).created_at = utcnow() - timedelta(hours=7 * 24 + 1)
    dbs.commit()
    from app.models import Upload

    dbs.add(Upload(session_id=danach["id"], source="table", chunk_size=1, state="aborted"))
    dbs.commit()
    assert aufbewahrung.leere_runden_entfernen(dbs) == 1
    dbs.commit()
    dbs.expire_all()
    assert dbs.get(GameSession, leer["id"]) is None
    assert dbs.get(GameSession, mit_notiz["id"]).number == 1 and dbs.get(GameSession, danach["id"]).number == 2


def test_eigennamen_aus_titel_und_welt(client, world, dbs, monkeypatch):
    from app import namenshilfe, woerterbuch
    from app.models import Campaign

    c = dbs.get(Campaign, world["cid"])
    c.title = "Nostria erhebt sich"
    c.world_info = "Die Gruppe erreicht Kaltenfurt. Eine verschwundene Fähre blockiert den Weg zur alten Abtei."
    dbs.commit()
    monkeypatch.setattr(woerterbuch, "liste", lambda sprache: frozenset())
    assert namenshilfe.eigennamen(c) == []  # ohne Wortliste lieber nichts
    allgemein = frozenset({"die", "gruppe", "eine", "fähre", "weg", "abtei", "erhebt", "sich", "den", "zur", "alten"})
    monkeypatch.setattr(woerterbuch, "liste", lambda sprache: allgemein)
    assert namenshilfe.eigennamen(c) == ["Nostria", "Kaltenfurt"]
    assert "Kaltenfurt" in namenshilfe.fuer_kampagne(dbs, c)
