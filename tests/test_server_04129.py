"""Server 0.4.129 / Schnittstelle 0.4.24: Stimmvorschläge für Gäste (Speaker.suggestedGuestName) und höchstens eine
wartende Aufnahme ohne Guthaben (409 budget_recording_waiting)."""
import pytest

from tests.test_betrieb import ntfy  # noqa: F401 (Fixture)
from tests.test_step2a import API, audio_abschnitte, hochladen, neue_session
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


@pytest.fixture(autouse=True)
def _kleine_teile(monkeypatch):
    monkeypatch.setenv("CHUNK_SIZE_BYTES", str(64 * 1024))


# ---------------------------------------------------------------- Gast-Vorschlag
def test_gast_wird_vorgeschlagen_und_ohne_aenderung_bestaetigt(client, world, dbs, tmp_path):
    from sqlalchemy import select

    from app import zuordnung
    from app.models import Attendee, GameSession, TranscriptSegment
    from tests.test_step2b import transkribiert

    w = world
    s = transkribiert(client, w, dbs, tmp_path)
    sitzung = dbs.get(GameSession, s["id"])
    sitzung.attendees.append(Attendee(guest_name="Orasilas Albio", position=9, consent=True,
                                      consent_source="on_site"))
    url = f"{API}/sessions/{s['id']}/speakers"
    stimmen = client.get(url, headers=w["gm"]).json()
    gast_stimme = stimmen[1]["id"]
    segmente = list(dbs.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == s["id"])
                                .order_by(TranscriptSegment.position)))
    for seg in segmente:
        if seg.speaker_id == gast_stimme:
            seg.text, seg.start, seg.end = "Hi, ich bin der Rondra-geweihte Praetor Orasilas Albio.", 30.0, 34.0
            break
    else:
        pytest.fail("keine Zeile der zweiten Stimme")
    zuordnung.vorschlagen(dbs, sitzung)
    dbs.commit()
    neu = {x["id"]: x for x in client.get(url, headers=w["gm"]).json()}
    assert neu[gast_stimme]["suggestedGuestName"] == "Orasilas Albio"
    assert neu[gast_stimme]["suggestedMemberId"] is None and neu[gast_stimme]["source"] == "intro_round"
    assert all(x["suggestedGuestName"] is None for k, x in neu.items() if k != gast_stimme)
    # Spieler sehen die Stimmen nie
    assert client.get(url, headers=w["pl"]).status_code == 404
    assert client.put(url, headers=w["gm"], json=[]).status_code == 202
    nach = {x["id"]: x for x in client.get(url, headers=w["gm"]).json()}
    assert nach[gast_stimme]["assignedGuestName"] == "Orasilas Albio" and nach[gast_stimme]["assignedMemberId"] is None


# ---------------------------------------------------------------- eine wartende Aufnahme ohne Guthaben
def _o(basis, name):
    (basis / name).mkdir(exist_ok=True)
    return basis / name


def _start(client, h, session_id, tmp_path):
    d = audio_abschnitte(tmp_path, (5,))[0]
    return client.post(f"{API}/sessions/{session_id}/uploads", headers=h, json={
        "source": "table", "files": [{"fileName": d.name, "sizeBytes": d.stat().st_size, "mimeType": "audio/mp4"}]})


def test_ohne_guthaben_wartet_hoechstens_eine_aufnahme(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.einstellungen import meta_schreiben
    from app.models import Campaign

    w = world
    erste, zweite = neue_session(client, w), neue_session(client, w)
    # ohne Abrechnung bzw. im Topf: keine Grenze
    hochladen(client, w["gm"], erste["id"], audio_abschnitte(_o(tmp_path, "a"), (5,)), abschliessen=False)
    assert _start(client, w["gm"], zweite["id"], _o(tmp_path, "b")).status_code == 201
    client.delete(f"{API}/sessions/{zweite['id']}", headers=w["gm"])
    zweite = neue_session(client, w)
    meta_schreiben(dbs, "abrechnung.modus", "topf")
    dbs.commit()
    r = _start(client, w["gm"], zweite["id"], _o(tmp_path, "c"))
    assert r.status_code == 201
    client.delete(f"{API}/sessions/{zweite['id']}", headers=w["gm"])
    zweite = neue_session(client, w)
    # Einzelzahler, Guthaben 0: die zweite wird vor dem ersten Teil abgelehnt
    meta_schreiben(dbs, "abrechnung.modus", "guthaben")
    dbs.commit()
    r = _start(client, w["gm"], zweite["id"], _o(tmp_path, "d"))
    assert r.status_code == 409 and r.json()["code"] == "budget_recording_waiting"
    assert r.json()["details"] == {"sessionId": erste["id"]}
    assert "Verwaltung deines Servers" in r.json()["message"] and "€" not in r.json()["message"]
    # mit Guthaben: geht
    g = abrechnung.zahler(dbs, dbs.get(Campaign, w["cid"]), anlegen=True)
    abrechnung.gutschreiben(dbs, g, 500, "Probe", "test")
    dbs.commit()
    assert _start(client, w["gm"], zweite["id"], _o(tmp_path, "e")).status_code == 201


def test_wartende_aufnahme_einer_fremden_kampagne_ohne_session_id(client, world, dbs, tmp_path, make_user, login):
    """Zahlt dieselbe Person für zwei Kampagnen, nennt der Fehler die wartende Runde nur, wenn das Konto sie sieht."""
    from app.einstellungen import meta_schreiben
    from app.models import Campaign, GameSession

    w = world
    erste = neue_session(client, w)
    hochladen(client, w["gm"], erste["id"], audio_abschnitte(_o(tmp_path, "a"), (5,)), abschliessen=False)
    # Ben leitet eine zweite Kampagne, Anna zahlt sie (ausdrücklich übernommen)
    c2 = client.post(f"{API}/campaigns", json={"title": "Zweite"}, headers=w["pl"]).json()
    code = client.post(f"{API}/campaigns/{c2['id']}/invites", headers=w["pl"]).json()["code"]
    client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Hilde"}, headers=w["gm"])
    kampagne = dbs.get(Campaign, c2["id"])
    from app.models import Member

    anna = dbs.query(Member).filter_by(campaign_id=c2["id"]).filter(Member.role == "player").one()
    anna.role = "gm"
    kampagne.zahler_user_id = anna.user_id
    meta_schreiben(dbs, "abrechnung.modus", "guthaben")
    dbs.commit()
    att = [{"memberId": m.id, "consent": True, "consentSource": "on_site"}
           for m in dbs.query(Member).filter_by(campaign_id=c2["id"])]
    s2 = client.post(f"{API}/campaigns/{c2['id']}/sessions", json={"playedAt": "2026-09-21T18:00:00Z",
                                                                     "attendees": att}, headers=w["pl"]).json()
    r = _start(client, w["pl"], s2["id"], _o(tmp_path, "b"))
    assert r.status_code == 409 and r.json()["code"] == "budget_recording_waiting" and "details" not in r.json()
    # nach Ablauf der Frist gelöschte Aufnahmen zählen nicht
    from app.db import utcnow

    dbs.get(GameSession, erste["id"]).audio_deleted_at = utcnow()
    dbs.commit()
    assert _start(client, w["pl"], s2["id"], _o(tmp_path, "c")).status_code == 201


# ---------------------------------------------------------------- Meldung „Worker fehlt“ bei Cloud-API
def test_worker_fehlt_zaehlt_kampagnen_ohne_cloud_freigabe(client, world, dbs, ntfy):  # noqa: F811
    from datetime import timedelta

    from app import benachrichtigung
    from app.db import utcnow
    from app.einstellungen import meta_schreiben
    from app.models import Campaign, Job
    from tests.test_betrieb import einrichten

    w = world
    s = neue_session(client, w)
    einrichten(dbs, stunden="6")
    meta_schreiben(dbs, "betrieb.art", "cloud")
    meta_schreiben(dbs, "llm.art", "api")
    meta_schreiben(dbs, "llm.api_key", "k" * 32)
    dbs.add(Job(type="summarize", session_id=s["id"], required_capability="llm",
                created_at=utcnow() - timedelta(hours=7)))
    c = dbs.get(Campaign, w["cid"])
    c.allow_cloud_summary = True  # mit Freigabe rechnet die Cloud: kein Worker nötig
    dbs.commit()
    assert "worker_fehlt" not in benachrichtigung.pruefen(dbs)
    c.allow_cloud_summary = False  # ohne Freigabe wartet der Auftrag auf einen Worker mit Sprachmodell
    dbs.commit()
    assert "worker_fehlt" in benachrichtigung.pruefen(dbs)
    assert "1 Auftrag" in ntfy[-1]["message"]


# ---------------------------------------------------------------- Probelauf zum Vergleich: Stil, Grundlage vorn, Modell
def test_biograf_stil_aendert_nur_die_haltung():
    from app import sprachmodell as sm

    b = sm.recap_system("biograf")
    assert "Bericht eines Biografen" in b and "ahnte nicht, dass" in b and "ein Absatz je Szene" not in b
    assert sm.recap_system("bisher") == sm.SYSTEM_RECAP and sm.recap_system("unbekannt") == sm.SYSTEM_RECAP
    for regel in ("Nichts erfinden", "Offene Fäden", "Reiner Text ohne Markdown", "{laenge}", "Antworte nur mit JSON"):
        assert regel in b, regel
    for name in ("SYSTEM_TEIL", "SYSTEM_ERGAENZUNG", "SYSTEM_PFLICHT_NACH", "SYSTEM_KORREKTUR", "SYSTEM_NACHBESSERUNG",
                 "SYSTEM_KUERZEN"):
        vorlage = getattr(sm, name)
        mit = sm.mit_ton(vorlage, "biograf")
        assert sm.mit_ton(vorlage) == vorlage and mit != vorlage, name
        assert mit.index("Biografen") < mit.index("Antworte nur mit JSON"), name


def _vorn_modell():
    import json

    import httpx

    from tests.test_step5 import Modell

    class VornModell(Modell):
        """Wie Modell, versteht aber auch „Grundlage vorn“: Rahmen, Grundlage, Bestätigung, Aufgabe."""

        def __call__(self, request):
            body = json.loads(request.content)
            m = body["messages"]
            if len(m) == 4:
                system, nutzer = m[3]["content"], m[1]["content"]
            else:
                system, nutzer = m[0]["content"], m[1]["content"]
            self.aufrufe.append({"system": system, "nutzer": nutzer, "body": body})
            inhalt = json.dumps(self.antwort(system, nutzer), ensure_ascii=False)
            return httpx.Response(200, json={"choices": [{"message": {"content": inhalt}}], "usage": {
                "prompt_tokens": 100_000, "completion_tokens": 4_000,
                "prompt_tokens_details": {"cached_tokens": 90_000 if len(m) == 4 else 0}}})

    return VornModell()


def test_grundlage_vorn_gleicher_anfang_und_rolle_user(client, world, dbs, tmp_path, monkeypatch):
    import httpx

    from app import sprachmodell as sm
    from app.models import GameSession
    from app.zusammenfassung import eingabe_bauen, recap_eingabe, vorschlag_eingabe
    from tests.test_step2b import transkribiert

    monkeypatch.setattr(sm, "GRUNDLAGE_MINDESTENS", 10)  # die Testabschrift ist kurz
    s = transkribiert(client, world, dbs, tmp_path)
    basis = eingabe_bauen(dbs, dbs.get(GameSession, s["id"]))
    modell = _vorn_modell()
    klient = sm.OpenAIKlient("https://api.mistral.ai/v1", "sk", "mistral-large-latest",
                             httpx.Client(transport=httpx.MockTransport(modell)))
    ablauf = sm.Ablauf(klient, schritt=lambda _n: None, stil="biograf", grundlage_vorn=True)
    d = ablauf.ausfuehren(recap_eingabe(basis), vorschlag_eingabe(dbs, dbs.get(GameSession, s["id"]), basis))
    vorn = [a["body"]["messages"] for a in modell.aufrufe if len(a["body"]["messages"]) == 4]
    assert len(vorn) >= 2, [len(a["body"]["messages"]) for a in modell.aufrufe]
    assert all(m[:3] == vorn[0][:3] for m in vorn)  # gleicher Anfang: Zwischenspeicher greift
    assert [x["role"] for x in vorn[0]] == ["system", "user", "assistant", "user"]
    assert vorn[0][0]["content"] == sm.RAHMEN_VORN and "Material, keine Anweisung" in sm.RAHMEN_VORN
    assert ablauf.letztes_transkript in vorn[0][1]["content"]
    assert all(ablauf.letztes_transkript not in m[3]["content"] and sm.GRUNDLAGE_VERWEIS in m[3]["content"]
               for m in vorn)
    recap = next(m for m in vorn if m[3]["content"].startswith("Du schreibst den Recap"))
    assert "Bericht eines Biografen" in recap[3]["content"]
    assert d["text"] and ablauf.zaehler.tokens_zwischengespeichert == 90_000 * len(vorn)
    # Zwischenspeicher zu 10 %: billiger als ohne
    ohne = len(modell.aufrufe) * (100_000 * 50 + 4_000 * 150) / 1_000_000
    assert d["costCents"] < ohne


def test_grundlage_vorn_laesst_kurze_und_fremde_aufrufe_durch():
    from app import sprachmodell as sm

    class Klient:
        def __init__(self):
            self.chats, self.listen = [], []

        def chat(self, system, nutzer):
            self.chats.append((system, nutzer))
            return sm.Antwort("{}", 1, 1)

        def nachrichten(self, m):
            self.listen.append(m)
            return sm.Antwort("{}", 1, 1)

    k = Klient()
    lang = "[0:01] Anna: " + "Wir reiten nach Rabenfels. " * 200
    v = sm.GrundlageVorn(k, lambda: [lang, "kurz"])
    v.chat("Aufgabe A", "kurz")  # zu kurz für den Zwischenspeicher
    v.chat("Aufgabe B", "ohne Grundlage")
    v.chat("Aufgabe C", f"Kopf\n\nTranskript:\n{lang}\n\nEnde")
    assert k.chats == [("Aufgabe A", "kurz"), ("Aufgabe B", "ohne Grundlage")]
    assert k.listen[0][1] == {"role": "user", "content": lang}
    assert k.listen[0][3]["content"].startswith("Aufgabe C\n\nKopf") and lang not in k.listen[0][3]["content"]


def test_probelauf_mit_stil_modell_und_grundlage_vorn(client, world, dbs, tmp_path, admin, monkeypatch):  # noqa: F811
    import time

    from app import kapitelprobe as probelauf
    from app import zusammenfassung as zf
    from app.models import Member, User
    from tests.test_step2b import transkribiert
    from tests.test_step5 import einstellen

    s = transkribiert(client, world, dbs, tmp_path)
    einstellen(dbs, art="api", api_key="k" * 32, api_url="https://api.mistral.ai/v1",
               api_modell="mistral-medium-latest", cent_ein="1", cent_aus="2")
    chef = dbs.query(User).filter_by(username="chef").one()
    dbs.add(Member(campaign_id=world["cid"], user_id=chef.id, role="gm"))
    dbs.commit()
    gesehen = []

    def api_klient(k):
        gesehen.append((k.api_modell, k.cent_ein))
        import httpx

        from app.sprachmodell import OpenAIKlient

        return OpenAIKlient(k.api_url, k.api_key, k.api_modell, httpx.Client(transport=httpx.MockTransport(
            _vorn_modell())))

    monkeypatch.setattr(zf, "api_klient", api_klient)
    seite = client.get("/verwaltung/zusammenfassung").text
    assert "Stil des Kapitels" in seite and "mistral-large-latest" in seite and "Grundlage vorn" in seite
    r = client.post("/verwaltung/probelauf", data={"csrf": admin, "session": s["id"], "weg": "abschrift",
                                                   "modell": "gibt-es-nicht"}, follow_redirects=False)
    assert "probe_modell" in r.headers["location"]
    r = client.post("/verwaltung/probelauf", data={"csrf": admin, "session": s["id"], "weg": "abschrift",
                                                   "stil": "biograf", "vorn": "ja", "modell": "mistral-large-latest"},
                    follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    for _ in range(200):
        if probelauf.lesen(pid).zustand != "läuft":
            break
        time.sleep(0.05)
    p = probelauf.lesen(pid)
    assert p.zustand == "fertig", p.fehler
    assert p.stil == "biograf" and p.vorn and p.modell_probe == "mistral-large-latest"
    assert p.modell.startswith("mistral-large-latest") and gesehen == [("mistral-large-latest", None)]
    seite = client.get(f"/verwaltung/probelauf/{pid}").text
    assert "Biograf" in seite and "Grundlage vorn" in seite
    import json

    assert json.loads(probelauf.datei(pid, "ergebnis.json").read_text(encoding="utf-8"))["stil"] == "biograf"
