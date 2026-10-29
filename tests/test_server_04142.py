"""Server 0.4.142: Kapitel je Szene (Testoption „kapitelweg“) – Szenen deterministisch schneiden, je Szene ein Absatz
ohne zweite Modellstufe („szenen“) oder Szenenabsätze als Entwurf für den Standardweg („vorbereitet“)."""
import json
import re

import httpx
import pytest

from app import sprachmodell as sm
from tests.test_step2b import _kleine_teile, transkribiert  # noqa: F401
from tests.test_verwaltung import admin  # noqa: F401


def _notizen(minuten: int, luecke_bei: tuple[int, ...] = ()) -> str:
    """Eine Notiz je Minute; an den Stellen in luecke_bei fehlen zwei Minuten (dort ist die Szenengrenze)."""
    return "\n".join(f"[{m // 60}:{m % 60:02d}:00] Notiz {m}." for m in range(0, minuten)
                     if not any(lu <= m < lu + 2 for lu in luecke_bei))


def test_szenen_schneiden_an_der_groessten_luecke():
    sz = sm.szenen_schneiden(_notizen(74, luecke_bei=(10, 26, 38, 49, 61)), 74 * 60)
    assert len(sz) == 6 and sz[0]["von"] == 0.0 and sz[-1]["bis"] == 74 * 60.0
    # Zielgrenzen bei 12:20, 24:40, 37:00, 49:20, 61:40 – die Grenze springt in die Lücke davor bzw. dahinter
    assert [s["bis"] // 60 for s in sz[:-1]] == [12, 28, 40, 51, 63]
    for a, b in zip(sz, sz[1:]):
        assert a["bis"] == b["von"]
    # ohne Lücken: Grenzen an den Zielwerten; kurze Runde: keine Szenen; keine Notizen: keine Szenen
    glatt = sm.szenen_schneiden(_notizen(60), 60 * 60)
    assert len(glatt) == 5 and all(abs((s["bis"] - s["von"]) - 720) < 61 for s in glatt)
    assert sm.szenen_schneiden(_notizen(15), 15 * 60) == []
    assert sm.szenen_schneiden("", 7200) == []
    # eine Szene ohne eigene Notiz geht in die davor
    duenn = "[0:00:00] a.\n[0:01:00] b.\n[0:02:00] c.\n[0:03:00] d.\n[0:40:00] e."
    sz = sm.szenen_schneiden(duenn, 45 * 60)
    assert sz[0]["von"] == 0.0 and sz[-1]["bis"] == 45 * 60.0 and all(
        any(s["von"] <= sm._zeit_vorn(z) < s["bis"] for z in duenn.splitlines()) for s in sz)


def test_szene_grenzen_teilen_den_platz_ohne_teil_minimum():
    dauer = 74 * 60
    sz = {"von": 0.0, "bis": 12 * 60.0}
    unten, oben = sm.szene_grenzen(sz, dauer, True)  # 74 min, lang: 400–900 → ein Sechstel
    assert 60 <= unten <= 80 and oben >= unten + 60 and oben <= 160
    assert sm.teil_grenzen(sz, dauer, True)[0] == sm.TEIL_MIN_WOERTER  # zum Vergleich: Teile haben das Minimum 300


_NAMEN = ["Ana", "Bo", "Cem", "Dov", "Eli", "Fay", "Gus", "Hal", "Ida", "Jon", "Kai", "Lu"]
_TUN = ["kaufte", "suchte", "verlor", "fand", "tauschte", "versteckte", "öffnete", "trug", "zeigte", "zählte", "prüfte", "band"]
_DING = ["den Schlüssel", "die Karte", "das Seil", "den Brief", "die Laterne", "das Siegel", "den Ring", "die Münze",
         "den Dolch", "das Buch", "den Stein", "die Fackel"]


def _satz(k: int, i: int) -> str:
    return f"{_NAMEN[i]} {_TUN[(i + k) % 12]} {_DING[(2 * i + k) % 12]} am {['Hafen', 'Markt', 'Tor'][(i + k) % 3]}."


class Anbieter:
    """Antwortet je Aufgabe; merkt sich die Recap-Aufrufe (Szenen und Kapitel)."""

    def __init__(self):
        self.recaps: list[tuple[str, str]] = []

    def antwort(self, system: str, nutzer: str) -> dict:
        if "Schreibe Szenennotizen" in system:
            eigen = nutzer.split("des Transkripts:\n")[1]
            zeiten = re.findall(r"^\[(\d+:\d{2}(?::\d{2})?)\]", eigen, re.M)
            return {"notizen": [f"[{z}] Ana tut etwas um {z}." for z in zeiten], "stand": ["Ana: wach (seit [0:00])"]}
        if system.startswith("Du bereitest das Kapitel"):
            zeiten = re.findall(r"^\[(\d+:\d{2}(?::\d{2})?)\]", nutzer, re.M)
            return {"ereignisse": [{"zeit": z, "ereignis": f"Ana handelt um {z}.", "ausgang": "gut", "rang": "kritisch"}
                                   for z in zeiten[::6]]}
        if system.startswith("Du schreibst den Recap"):
            self.recaps.append((system, nutzer))
            m = re.search(r"Szene (\d+) von (\d+)", nutzer)
            if m:
                k = m.group(1)
                return {"title": f"Kapitel 1: Szene {k}", "openThreads": [f"Faden {k}"],
                        "text": f"In Szene {k} geschah es: " + " ".join(_satz(int(k), i) for i in range(12))}
            return {"title": "Kapitel 1: Das Ganze", "openThreads": ["Faden ganz"],
                    "text": "\n\n".join(" ".join(_satz(j + 10, i) for i in range(12)) for j in range(6))}
        if system.startswith("Du vergleichst das Kapitel"):
            return {"punkte": []}
        if "Kampagnen-Bibel" in system:
            return {"proposals": []}
        return {}

    def __call__(self, request: httpx.Request) -> httpx.Response:
        body = json.loads(request.content)
        system, nutzer = body["messages"][0]["content"], body["messages"][1]["content"]
        return httpx.Response(200, json={"choices": [{"message": {"content": json.dumps(self.antwort(system, nutzer),
                                                                                         ensure_ascii=False)}}],
                                         "usage": {"prompt_tokens": 1000, "completion_tokens": 100}})


def _eingabe():
    zeilen = [{"start": float(m * 60), "sprecher": "Ana" if m % 7 else "Bo", "text": f"Satz bei Minute {m}."}
              for m in range(0, 74)]
    return {"sprache": "de", "kampagne": "K", "session_nummer": 1, "session_titel": None, "welt": "", "bibel": [],
            "personen": [], "transkript": zeilen}


def _ablauf(anbieter, kapitelweg):
    klient = sm.OpenAIKlient("https://llm.example/v1", "sk", "m", httpx.Client(transport=httpx.MockTransport(anbieter)))
    return sm.Ablauf(klient, schritt=lambda _n: None, nachbesserung=False, notizen_zuerst=True, notiz_stueck=200000,
                     zweiter_blick_an=False, kapitelweg=kapitelweg)


def test_kapitel_je_szene_zusammengesetzt():
    an = Anbieter()
    a = _ablauf(an, "szenen")
    d = a.ausfuehren(_eingabe(), dict(_eingabe(), geheim=[]), gegenpruefen=True)
    n = len(a.letzte_szenen)
    assert n == 6 and len(an.recaps) == n  # ein Aufruf je Szene, keine zweite Stufe
    assert "parts" not in d and a.letzte_teile == []
    assert d["title"] == "Kapitel 1: Szene 1" and d["openThreads"] == [f"Faden {n}"]
    assert all(f"In Szene {k} geschah" in d["text"] for k in range(1, n + 1))
    assert [s["text"][:10] for s in a.letzte_szenen] == [f"In Szene {k}" for k in range(1, 7)]
    # Szene 1 ohne Vorgriff, Szene 2 als Fortsetzung mit dem Absatz davor; je Szene nur ihre Notizen und Pflichtpunkte
    assert "Szene 1 von 6 eines Kapitels" in an.recaps[0][0] and "Kein Schlusswort" in an.recaps[0][0]
    assert "setzt die Szene davor nahtlos fort" in an.recaps[1][0] and "So endet der Teil davor" in an.recaps[1][1]
    assert "[0:00] Ana" in an.recaps[0][1] and "um 29:00" not in an.recaps[0][1] and "um 29:00" in an.recaps[2][1]
    assert "Ein Absatz, höchstens zwei" in an.recaps[0][0]
    assert "Diese Ereignisse müssen im Kapitel vorkommen" in an.recaps[0][1]
    assert "Ana handelt um 0:00" in an.recaps[0][1] and "Ana handelt um 0:00" not in an.recaps[1][1]


def test_kapitel_mit_szenenentwurf_vorbereitet():
    an = Anbieter()
    a = _ablauf(an, "vorbereitet")
    d = a.ausfuehren(_eingabe(), dict(_eingabe(), geheim=[]), gegenpruefen=True)
    assert len(a.letzte_szenen) == 6 and len(an.recaps) == 7  # sechs Szenen, dann das Kapitel
    assert d["title"] == "Kapitel 1: Das Ganze" and "Ana prüfte den Stein" in d["text"] and "In Szene" not in d["text"]
    kapitel = an.recaps[-1][1]
    assert "Entwurf je Szene" in kapitel and "In Szene 3 geschah" in kapitel and "kein Ereignis daraus weglassen" in kapitel
    assert "[0:00] Ana tut etwas" in kapitel  # die Notizen bleiben daneben
    assert "Diese Ereignisse müssen im Kapitel vorkommen" in kapitel
    assert "400 bis 900" in an.recaps[-1][0]  # das Kapitel bekommt seine normale Länge, nicht die Summe der Szenen
    assert "Absatz, höchstens zwei" in an.recaps[0][0] and "Absatz, höchstens zwei" not in an.recaps[-1][0]


def test_standardweg_unveraendert_und_nur_mit_notizen():
    an = Anbieter()
    a = _ablauf(an, "standard")
    a.ausfuehren(_eingabe(), dict(_eingabe(), geheim=[]), gegenpruefen=True)
    assert len(an.recaps) == 1 and a.letzte_szenen == []
    # ohne den Weg „Notizen zuerst“ greift die Option nicht
    an = Anbieter()
    klient = sm.OpenAIKlient("https://llm.example/v1", "sk", "m", httpx.Client(transport=httpx.MockTransport(an)))
    b = sm.Ablauf(klient, schritt=lambda _n: None, nachbesserung=False, kapitelweg="szenen", zweiter_blick_an=False)
    b.ausfuehren(_eingabe(), dict(_eingabe(), geheim=[]), gegenpruefen=False)
    assert b.letzte_szenen == []


def test_modellvergleich_und_probelauf_kennen_die_option():
    from app import kapitelprobe
    from app.modellvergleich import Einstellungen, szenen_text

    e = Einstellungen(weg="notizen", kapitel="szenen")
    assert "Kapitel szenen" in e.beschreibung() and "Kapitel" not in Einstellungen().beschreibung()
    assert szenen_text([{"von": 0.0, "bis": 720.0, "text": "A."}]).startswith("– Szene 1 (0:00–12:00) –\n\nA.")
    assert kapitelprobe.KAPITELWEGE == sm.KAPITELWEGE == ("standard", "szenen", "vorbereitet")
    assert "szenen.txt" in kapitelprobe.DATEIEN


@pytest.mark.usefixtures("_kleine_teile")
def test_probelauf_formular_und_cli(client, world, dbs, tmp_path, admin):  # noqa: F811
    import time

    from app import kapitelprobe as probelauf
    from app.models import Campaign, Member, User
    from tests.test_step5 import einstellen

    s = transkribiert(client, world, dbs, tmp_path)
    einstellen(dbs, art="attrappe")
    chef = dbs.query(User).filter_by(username="chef").one()
    dbs.add(Member(campaign_id=world["cid"], user_id=chef.id, role="gm"))
    dbs.get(Campaign, world["cid"]).allow_cloud_summary = True
    dbs.commit()
    seite = client.get("/verwaltung/zusammenfassung").text
    for wert in ("standard", "szenen", "vorbereitet"):
        assert f'name="kapitelweg" value="{wert}"' in seite
    # Option nur zusammen mit dem Weg über Notizen; sonst bleibt es beim Standard
    r = client.post("/verwaltung/probelauf", data={"csrf": admin, "session": s["id"], "weg": "abschrift",
                                                   "kapitelweg": "szenen"}, follow_redirects=False)
    p = probelauf.lesen(r.headers["location"].rsplit("/", 1)[1])
    assert p.kapitelweg == "standard"
    r = client.post("/verwaltung/probelauf", data={"csrf": admin, "session": s["id"], "weg": "notizen",
                                                   "kapitelweg": "vorbereitet"}, follow_redirects=False)
    pid = r.headers["location"].rsplit("/", 1)[1]
    assert probelauf.lesen(pid).kapitelweg == "vorbereitet"
    for _ in range(200):
        if probelauf.lesen(pid).zustand != "läuft":
            break
        time.sleep(0.05)
    assert probelauf.lesen(pid).zustand == "fertig"
    assert "Szenen als Entwurf" in client.get(f"/verwaltung/probelauf/{pid}").text
    # Befehlszeile: die Option verlangt den Weg über Notizen
    from typer.testing import CliRunner

    from app.cli import app

    r = CliRunner().invoke(app, ["modellvergleich", "--server", "http://x", "--benutzer", "u", "--passwort", "p",
                                 "--kapitel", "szenen"])
    assert r.exit_code != 0 and "nur mit --weg notizen" in (r.output + str(r.exception or ""))
