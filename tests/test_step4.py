"""Schritt 4: Stimmen aus der Vorstellungsrunde vorschlagen."""
from types import SimpleNamespace as NS

import pytest
from fastapi.testclient import TestClient

from app.zuordnung import FENSTER_SEKUNDEN, Hinweis, verteilen, vorstellungsrunde
from tests.test_step2a import API, audio_abschnitte, hochladen, neue_session, worker_token


def mitglied(id_, name, figur=None, rolle="player", benutzer=None):
    return NS(id=id_, role=rolle, character_name=figur,
              user=NS(display_name=name, username=benutzer or name.lower()))


RUNDE = [mitglied("sl", "Zeitiger", rolle="gm"), mitglied("lilio", "Lilio", "Jemma Reed"),
         mitglied("saroman", "Saroman", "Litha Flamel"), mitglied("phybe", "Phybe", "Tubo")]


def seg(sp, start, text, dauer=4.0):
    return NS(speaker_id=sp, start=start, end=start + dauer, text=text)


def vorschlag(segmente, runde=RUNDE):
    return {k: h.member_id for k, h in verteilen(vorstellungsrunde(segmente, runde)).items()}


def test_typische_vorstellungsrunde():
    segmente = [
        seg("A", 5, "Hallo zusammen, ich leite heute wieder, willkommen zu Household."),
        seg("B", 20, "Ich bin Lilio und spiele Jemma Reed."),
        seg("C", 30, "Hi, ich bin der Saroman, ich spiele Litha Flamel."),
        seg("D", 40, "Ich bin", dauer=1.0),
        seg("D", 41.5, "Phybe, und ich spiel den Tubo."),  # Vorstellung über zwei Zeilen
    ]
    assert vorschlag(segmente) == {"A": "sl", "B": "lilio", "C": "saroman", "D": "phybe"}


def test_whisper_schreibweisen_und_nur_figur():
    # Whisper schreibt „Gemma“ statt „Jemma“ und „Litta“ statt „Litha“
    segmente = [seg("B", 10, "Ich spiele die Gemma."), seg("C", 20, "Und ich bin heute Litta Flamel.")]
    ergebnis = verteilen(vorstellungsrunde(segmente, RUNDE))
    assert {k: h.member_id for k, h in ergebnis.items()} == {"B": "lilio", "C": "saroman"}
    assert all(0.5 <= h.sicherheit < 0.9 for h in ergebnis.values())  # unsicherer als ein exakter Treffer


def test_keine_falschen_treffer():
    segmente = [
        seg("A", 60, "Ich bin Graf Borbarad und ihr werdet mir dienen!"),  # SL spricht einen NSC
        seg("B", 70, "Ich bin müde, lasst uns anfangen."),
        seg("C", 80, "Ich spiele heute mal vorsichtig."),
        seg("D", FENSTER_SEKUNDEN + 10, "Ich bin Phybe."),  # zu spät, keine Vorstellungsrunde mehr
    ]
    assert vorschlag(segmente) == {}


def test_nur_anwesende_mit_konto():
    nur_zwei = RUNDE[:2]
    assert vorschlag([seg("C", 10, "Ich bin Saroman.")], nur_zwei) == {}


def test_widerspruch_jede_person_nur_einmal():
    segmente = [seg("B", 10, "Ich bin Lilio und spiele Jemma Reed."), seg("X", 200, "Ich bin Lilio!")]
    ergebnis = vorschlag(segmente)
    assert ergebnis == {"B": "lilio"}  # die stärkere Stimme bekommt den Vorschlag, die andere keinen


def test_englisch():
    runde = [mitglied("anna", "Anna", "Mira"), mitglied("gm", "Ben", rolle="gm")]
    segmente = [seg("A", 5, "Hi everyone, I'm your GM tonight."), seg("B", 12, "Hey, I'm Anna and I play Mira.")]
    assert vorschlag(segmente, runde) == {"A": "gm", "B": "anna"}


def test_stimmprofil_hinweise_docken_an():
    """Schritt 6 liefert Hinweise mit Quelle voice_match – sie werden mit der Vorstellung verrechnet."""
    hinweise = vorstellungsrunde([seg("B", 10, "Ich spiele die Gemma.")], RUNDE) + [
        Hinweis("B", "lilio", 0.7, "voice_match"), Hinweis("C", "saroman", 0.8, "voice_match")]
    ergebnis = verteilen(hinweise)
    assert ergebnis["B"].member_id == "lilio" and ergebnis["B"].quelle == "voice_match"
    assert ergebnis["B"].sicherheit > 0.9  # zwei Quellen zusammen sind sicherer
    assert ergebnis["C"].member_id == "saroman"


# ---------------------------------------------------------------- über den Server
class IntroMotor:
    """Wie WhisperX, liefert aber eine Vorstellungsrunde."""

    modell = "test"

    def audio_laden(self, wav):
        from app import audio
        return audio.dauer(wav)

    def transkribieren(self, dauer, sprache, hotwords, fortschritt):
        return [{"start": 1.0, "end": 5.0, "text": " Ich bin Anna, ich leite heute."},
                {"start": 6.0, "end": 10.0, "text": " Hallo, ich spiele Mira."},
                {"start": 12.0, "end": 20.0, "text": " Dann legen wir los mit dem Abenteuer."},
                {"start": 21.0, "end": 29.0, "text": " Wir betreten die Taverne."}]

    def ausrichten(self, segmente, dauer, sprache, fortschritt):
        return segmente

    def sprecher_trennen(self, dauer, segmente, min_n, max_n, fortschritt):
        for s, sp in zip(segmente, ["SPEAKER_00", "SPEAKER_01", "SPEAKER_00", "SPEAKER_01"]):
            s["speaker"] = sp
        return segmente, {"SPEAKER_00": [0.1], "SPEAKER_01": [0.2]}


@pytest.fixture(autouse=True)
def _kleine_teile(monkeypatch):
    monkeypatch.setenv("CHUNK_SIZE_BYTES", str(64 * 1024))


def test_vorschlaege_in_der_app(client, world, dbs, tmp_path, caplog):
    import logging

    from app.worker_prozess import WorkerProzess
    from app.transkription import verarbeiter

    w = world  # anna = SL, ben = Spieler mit Figur „Mira“
    s = neue_session(client, w)
    hochladen(client, w["gm"], s["id"], audio_abschnitte(tmp_path, (30,)))
    caplog.set_level(logging.INFO, logger="zuordnung")
    knecht = WorkerProzess("http://testserver", worker_token(dbs), tmp_path / "k", verarbeiter(IntroMotor()),
                          client=TestClient(client.app), claim_wait=0)
    assert knecht.einen_auftrag()
    sprecher = {sp["sampleText"][:10]: sp for sp in
                client.get(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"]).json()}
    assert len(sprecher) == 2
    for sp in sprecher.values():
        assert sp["source"] == "intro_round" and sp["confidence"] >= 0.5
    assert {sp["suggestedMemberId"] for sp in sprecher.values()} == {w["gm_member"], w["pl_member"]}
    zeile = next(r.getMessage() for r in caplog.records if r.name == "zuordnung")  # eine Zeile je Runde, ohne Namen
    assert "2 Stimmen" in zeile and "vorgeschlagen 2" in zeile and "Anna" not in zeile and "Mira" not in zeile
    # Bestätigen ohne Änderung übernimmt die Vorschläge
    client.put(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"], json=[])
    zeilen = client.get(f"{API}/sessions/{s['id']}/transcript", headers=w["gm"]).json()
    assert zeilen[0]["memberId"] == w["gm_member"] and zeilen[1]["memberId"] == w["pl_member"]


# ---------------------------------------------------------------- Gäste, Selbstnennung im Spiel, mehrere SL-Stimmen
def test_gaeste_werden_erkannt_aber_nicht_vorgeschlagen():
    """Gäste (oft mit Figurennamen eingetragen) sind Kandidaten: Ihre Stimme bekommt kein Mitglied zugeschlagen."""
    runde = [mitglied("sl", "Tinker", rolle="gm")]
    segmente = [
        seg("A", 30, "Das heißt, ich als Erzähler habe eine Geschichte vorbereitet."),
        seg("B", 118, "Mein Name ist Alekin Desch."),  # Whisper-Schreibweise
        seg("C", 226, "Hi, ich bin der Rondra-geweihte Praetor Orasilas Albio."),
    ]
    ergebnis = verteilen(vorstellungsrunde(segmente, runde, ["Arlekin Dehsch", "Orasilas Albio"]))
    assert {k: h.member_id for k, h in ergebnis.items()} == {
        "A": "sl", "B": "gast:Arlekin Dehsch", "C": "gast:Orasilas Albio"}


def test_sl_als_erzaehler_und_englisch():
    for satz in ("Ich als Erzähler kenne die Geschichte.", "Ich bin euer Spielleiter heute.",
                 "As your GM I prepared a one-shot."):
        assert vorschlag([seg("A", 5, satz)]) == {"A": "sl"}, satz


def test_im_spiel_nur_mit_klarer_mehrheit():
    spaet = FENSTER_SEKUNDEN + 60
    einmal = [seg("B", spaet, "Mein Name ist Jemma Reed.")]
    assert vorschlag(einmal) == {}  # ein Satz im Spiel reicht nie
    zweimal = einmal + [seg("B", spaet + 900, "Ich bin Jemma, lasst mich rein.")]
    ergebnis = verteilen(vorstellungsrunde(zweimal, RUNDE))
    assert {k: h.member_id for k, h in ergebnis.items()} == {"B": "lilio"} and ergebnis["B"].sicherheit < 0.8
    strittig = zweimal + [seg("B", spaet + 1800, "Ich bin Litha Flamel!"), seg("B", spaet + 2000, "Ich spiele Litha.")]
    assert vorschlag(strittig) == {}  # keine klare Mehrheit
    # „Ich bin sofort hinterher, Jemma“ ist keine Vorstellung (nur die ersten zwei Wörter zählen)
    assert vorschlag([seg("C", spaet, "Ich bin sofort hinterher, Jemma auch."),
                      seg("C", spaet + 500, "Ich bin gleich wieder da, Jemma.")]) == {}
    # Figuren, nicht Personen: Im Spiel nennt niemand seinen echten Namen
    assert vorschlag([seg("C", spaet, "Ich bin Saroman."), seg("C", spaet + 500, "Ich bin Saroman!")]) == {}


def test_sl_bekommt_uebrige_stimmen_nur_wenn_alle_anderen_vergeben_sind():
    from app.zuordnung import sl_weitere_stimmen

    auswahl = {"A": Hinweis("A", "sl", 0.8, "intro_round"), "B": Hinweis("B", "lilio", 0.9, "intro_round")}
    redezeit = {"A": 3000.0, "B": 1500.0, "C": 600.0, "D": 20.0}
    weitere = sl_weitere_stimmen(auswahl, redezeit, ["sl", "lilio"], "sl")
    assert set(weitere) == {"C"} and weitere["C"].member_id == "sl" and weitere["C"].sicherheit < 0.8  # D: zu wenig
    assert sl_weitere_stimmen(auswahl, redezeit, ["sl", "lilio", "saroman"], "sl") == {}  # Saroman fehlt noch
    assert sl_weitere_stimmen({"B": auswahl["B"]}, redezeit, ["sl", "lilio"], "sl") == {}  # SL noch ohne Stimme
    assert sl_weitere_stimmen(auswahl, redezeit, ["sl", "lilio"], None) == {}
