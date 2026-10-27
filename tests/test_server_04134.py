"""Server 0.4.134: Lokales Sprachmodell – Abtastung je Aufgabenart, JSON-Grammatik nur für Urteile, Antwortlänge nach
Bedarf (Teil 1) und der Weg „Erst Notizen“ auch für den Worker (Teil 2). Standard des Wegs bleibt „abschrift“, bis die
Messung entschieden hat."""
import httpx
import pytest

from app import sprachmodell as sm
from tests.test_notizen_zuerst import Anbieter, _eingabe
from tests.test_step2b import _kleine_teile, transkribiert  # noqa: F401
from tests.test_verwaltung import admin  # noqa: F401

pytestmark = pytest.mark.usefixtures("_kleine_teile")


class Ollama(sm.OllamaKlient):
    """OllamaKlient ohne Netz: merkt sich jeden Body und antwortet nach Vorgabe."""

    def __init__(self, antworten=None, kontext=24576):
        super().__init__("http://ollama.invalid", "testmodell", kontext)
        self.bodies: list[dict] = []
        self.antworten = list(antworten or [])
        self._denkt = False

    def denkmodus(self) -> bool:
        return False

    def _streamen(self, body: dict):
        self.bodies.append(body)
        if self.antworten:
            a = self.antworten.pop(0)
            if isinstance(a, tuple):
                return a
            return 200, "", {"inhalt": a, "eval_count": 10, "done_reason": "stop"}
        return 200, "", {"inhalt": '{"ok": true}', "eval_count": 10, "done_reason": "stop"}


# ---------------------------------------------------------------- Teil 1: Abtastung
def test_profile_je_aufgabenart():
    k = Ollama()
    z = sm.Zaehler()
    z.aufruf(k, "s", "n", profil="prosa", antwort_hoechstens=7000)
    o = k.bodies[-1]["options"]
    assert "format" not in k.bodies[-1]
    assert (o["temperature"], o["top_k"], o["top_p"], o["repeat_penalty"], o["num_predict"]) == (0.7, 64, 0.95, 1.0, 7000)
    z.aufruf(k, "s", "n", profil="urteil")
    assert k.bodies[-1]["format"] == "json" and k.bodies[-1]["options"]["temperature"] == 0.1
    assert k.bodies[-1]["options"]["num_predict"] == sm.ANTWORT_HOECHSTENS
    z.aufruf(k, "s", "n", profil="sachlich")
    assert "format" not in k.bodies[-1] and k.bodies[-1]["options"]["temperature"] == 0.3
    # nach dem Aufruf wieder wie vorher
    assert (k.profil, k.antwort_hoechstens, k.grammatik) == ("prosa", sm.ANTWORT_HOECHSTENS, None)


def test_leiter_nach_wiederholungsschleife():
    k = Ollama([(500, "token repeat limit reached", {}), (500, "token repeat limit reached", {}), '{"a": 1}'])
    k.profil = "urteil"
    assert sm._json(k.chat("s", "n")) == {"a": 1}
    erster, zweiter, dritter = (b["options"] for b in k.bodies)
    assert erster["repeat_last_n"] == 256 and zweiter["repeat_last_n"] == 2048 and dritter["repeat_last_n"] == 4096
    assert zweiter["repeat_penalty"] == 1.05 and zweiter["frequency_penalty"] == 0.2
    assert k.bodies[0]["format"] == "json" and "format" not in k.bodies[2]  # der letzte ohne Grammatik
    assert dritter["temperature"] == 0.5


def test_profil_alt_wie_bis_0_4_133():
    k = Ollama()
    k.profile = sm.PROFILE_ALT
    k.profil, k.antwort_hoechstens = "prosa", 9000
    k.chat("s", "n")
    assert k.bodies[-1]["format"] == "json"
    assert k.bodies[-1]["options"] == {"num_ctx": 24576, "temperature": 0.3, "repeat_penalty": 1.1,
                                       "repeat_last_n": 256, "num_predict": 4096}


def test_kein_json_zweiter_versuch_mit_grammatik_und_abgeschnitten_gezaehlt():
    k = Ollama([(200, "", {"inhalt": "Hier ist das Kapitel: …", "eval_count": 4096, "done_reason": "length"}),
                '{"text": "gut"}'])
    z = sm.Zaehler()
    assert z.aufruf(k, "s", "n", profil="prosa") == {"text": "gut"}
    assert "format" not in k.bodies[0] and k.bodies[1]["format"] == "json"
    assert z.nachgefordert == 1 and z.abgeschnitten == 1 and k.grammatik is None


def test_andere_klienten_bleiben_unveraendert():
    class Fremd:
        modell = "x"

        def chat(self, system, nutzer):
            return sm.Antwort('{"ok": 1}')

    assert sm.Zaehler().aufruf(Fremd(), "s", "n", profil="urteil", antwort_hoechstens=9000) == {"ok": 1}


def test_antwortlaenge_nach_bedarf_und_kontext():
    a = sm.Ablauf(Ollama(kontext=24576))
    assert a._antwortlaenge("s", "n", 2400) == int(2400 * sm.TOKEN_JE_WORT) + 500  # mehr als die alten 4096
    assert a._antwortlaenge("s", "n", 300) == sm.ANTWORT_HOECHSTENS  # nie weniger als bisher
    eng = sm.Ablauf(Ollama(kontext=8192))
    gross = "x " * 20_000
    assert eng._antwortlaenge("s", gross, 2400) == 1024  # Kontext voll: höchstens, was passt
    assert sm.Ablauf(sm.OpenAIKlient("https://x.example/v1", "k", "m"))._antwortlaenge("s", "n", 2400) is None


def test_recap_bekommt_platz_fuer_lange_kapitel():
    text = '{"title": "Kapitel 1: Nebel", "text": "' + "Die Gruppe zog weiter. " * 50 + '", "openThreads": []}'
    k = Ollama([text, text])
    a = sm.Ablauf(k)
    ein = {"bibel": [], "personen": [], "kampagne": "K", "system": None, "welt": "", "session_nummer": 1,
           "session_titel": "", "sprache": "de", "transkript": [{"start": 7 * 3600.0, "sprecher": "A", "text": "x"}]}
    a.recap(ein, "Transkript", "[0:00] A: Hallo")
    oben = sm.obergrenze(ein)
    assert k.bodies[0]["options"]["num_predict"] >= oben * sm.TOKEN_JE_WORT and "format" not in k.bodies[0]


# ---------------------------------------------------------------- Teil 2: „Erst Notizen“ lokal
def test_notiz_stueck_fuer_kontext():
    assert [sm.notiz_stueck_fuer(k) for k in (12288, 20480, 24576, 32768)] == [3392, 11584, 15000, 15000]


def test_teile_nach_platz():
    notizen = "\n".join(f"[{m // 60}:{m % 60:02d}:00] Ereignis {m} mit einigen Worten mehr für den Platz." for m in
                        range(0, 180, 2))
    teile = sm.teile_nach_platz(notizen, 0.0, 180 * 60.0, sm.tokens(notizen) // 2 + 10)
    assert len(teile) == 2 and teile[0]["von"] == 0.0 and teile[-1]["bis"] == 180 * 60.0
    assert teile[0]["bis"] == teile[1]["von"] and teile[1]["titel"] == "Teil 2"
    assert any(f"[{int(teile[1]['von'] // 3600)}:" in z for z in notizen.splitlines())  # Grenze auf einer Notiz
    assert len(sm.teile_nach_platz(notizen, 0.0, 180 * 60.0, 10**6, "en")) == 1
    assert sm.teile_nach_platz(notizen, 0.0, 180 * 60.0, sm.tokens(notizen) // 3 + 10, "en")[2]["titel"] == "Part 3"


def test_notizen_zuerst_ohne_verlauf_in_teilen(client, world, dbs, tmp_path):
    recap_ein, vorschlag_ein = _eingabe(client, world, dbs, tmp_path)
    anbieter = Anbieter()
    klient = sm.OpenAIKlient("https://llm.example/v1", "sk", "m", httpx.Client(transport=httpx.MockTransport(anbieter)))
    ablauf = sm.Ablauf(klient, schritt=lambda _n: None, nachbesserung=False, notizen_zuerst=True, notiz_stueck=2000,
                       max_transkript_tokens=sm.PLATZ_RESERVE + 120)
    ablauf.ausfuehren(recap_ein, vorschlag_ein)
    systeme = [a["system"] for a in anbieter.aufrufe]
    assert not any(s.startswith(sm.SYSTEM_TEIL[:40]) for s in systeme)  # keine Prosa aus Prosa
    recaps = [a for a in anbieter.aufrufe if a["system"].startswith("Du schreibst den Recap")]
    assert len(recaps) >= 2 and "Szenennotizen von Teil 1 von" in recaps[0]["nutzer"]
    assert any(w["schritt"] == "teile" for w in ablauf.warnungen)


def test_auftrag_traegt_den_weg_und_worker_baut_danach(client, world, dbs, tmp_path):
    from app.einstellungen import meta_schreiben
    from app.models import Job
    from app.routers.worker import _zusammenfassungs_auftrag
    from app.worker_prozess import lokaler_ablauf

    s = transkribiert(client, world, dbs, tmp_path)
    client.put(f"/api/v1/sessions/{s['id']}/speakers", headers=world["gm"], json=[])
    job = dbs.query(Job).filter_by(session_id=s["id"], type="summarize").one()
    z = _zusammenfassungs_auftrag(dbs, job)["summarize"]
    assert (z["path"], z["secondLook"], z["reviewView"]) == ("abschrift", True, "pflicht")
    meta_schreiben(dbs, "llm.weg_lokal", "notizen")
    dbs.commit()
    z = _zusammenfassungs_auftrag(dbs, job)["summarize"]
    assert z["path"] == "notizen"
    a = lokaler_ablauf(Ollama(kontext=20480), z)
    assert a.notizen_zuerst and a.notiz_stueck == 11584 and a.nachbesserung is False
    alt = lokaler_ablauf(Ollama(kontext=20480), {})  # älterer Server ohne Feld
    assert not alt.notizen_zuerst and alt.nachbesserung is True


def test_verwaltung_speichert_den_lokalen_weg(client, dbs, admin):  # noqa: F811
    import re

    from app.einstellungen import llm_konfig

    seite = client.get("/verwaltung/zusammenfassung").text
    assert 'name="weg_lokal" value="abschrift" checked' in seite
    form = seite.split('action="/verwaltung/zusammenfassung"', 1)[1].split("</form>", 1)[0]
    daten = {"csrf": admin}
    for name, wert in re.findall(r'<input type="hidden" name="(\w+)" value="([^"]*)"', form):
        daten[name] = wert
    for m in re.finditer(r'<input[^>]*name="(\w+)"[^>]*>', form):
        tag = m.group(0)
        if 'type="radio"' in tag and "checked" in tag:
            daten[m.group(1)] = re.search(r'value="([^"]*)"', tag).group(1)
        elif 'type="text"' in tag or 'type="number"' in tag:
            v = re.search(r'value="([^"]*)"', tag)
            daten.setdefault(m.group(1), v.group(1) if v else "")
    for m in re.finditer(r'<select name="(\w+)".*?</select>', form, re.S):
        gewaehlt = re.search(r'<option value="([^"]*)"[^>]*selected', m.group(0)) or re.search(
            r'<option value="([^"]*)"', m.group(0))
        daten[m.group(1)] = gewaehlt.group(1) if gewaehlt else ""
    daten["weg_lokal"] = "notizen"
    r = client.post("/verwaltung/zusammenfassung", data=daten, follow_redirects=False)
    assert r.status_code == 303, r.text[:2000]
    dbs.expire_all()
    assert llm_konfig(dbs).weg_lokal == "notizen"
