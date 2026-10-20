"""Server 0.4.114: Messwerte je Auftrag (Laufzeit je Schritt, Token/s, Grafikkarte), Sprachmodell eine Stufe
kleiner, wenn es nicht ganz auf die Grafikkarte passt, Zwischenspeicher des Cloud-Anbieters in den Kosten."""
import json

import httpx
import pytest

from tests.test_step4 import _kleine_teile  # noqa: F401  (Fixture)
from tests.test_verwaltung import admin  # noqa: F401  (Fixture)

API = "/api/v1"
GB = 2 ** 30


class FalschesOllama:
    """Lädt Modelle mit Kontext; /api/ps meldet, wie viel davon auf der Karte liegt (Tabelle passt)."""

    def __init__(self, passt):
        self.passt, self.geladen, self.anfragen = passt, None, []

    def __call__(self, req: httpx.Request):
        pfad = req.url.path
        koerper = json.loads(req.content) if req.content else {}
        self.anfragen.append((pfad, koerper))
        if pfad == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": m, "digest": "abc123def456"} for m in
                                                       ("gemma4:12b", "gemma4:e4b")]})
        if pfad == "/api/generate":
            if koerper.get("keep_alive") == 0:
                self.geladen = None
            else:
                self.geladen = (koerper["model"], koerper["options"]["num_ctx"])
            return httpx.Response(200, json={"done": True})
        if pfad == "/api/ps":
            if self.geladen is None:
                return httpx.Response(200, json={"models": []})
            m, k = self.geladen
            karte = 8 * GB if self.passt(m, k) else 6 * GB
            return httpx.Response(200, json={"models": [{"name": m, "model": m, "size": 8 * GB, "size_vram": karte,
                                                        "details": {"quantization_level": "Q4_K_M"}}]})
        return httpx.Response(404, json={})


def _laden(monkeypatch, passt, model="auto", vram=16000):
    from app import arbeitsweise, worker_prozess

    monkeypatch.setattr(arbeitsweise, "grafikspeicher_mb", lambda: vram)
    ollama = FalschesOllama(passt)
    client = httpx.Client(transport=httpx.MockTransport(ollama))
    k, kennung, abgestuft = worker_prozess.modell_laden("http://ollama:11434", "0.40.0",
                                                        {"model": model, "context": 32768}, lambda h: h, client)
    return k, abgestuft, ollama


def test_kleinere_stufen():
    from app.recapmodell import GROSS, KLEIN, MIN_KONTEXT, kleinere

    assert kleinere(GROSS, 32768) == [(GROSS, 24576), (KLEIN, 24576), (KLEIN, MIN_KONTEXT)]
    assert kleinere(KLEIN, 24576) == [(KLEIN, MIN_KONTEXT)]
    assert kleinere(KLEIN, MIN_KONTEXT) == [] and kleinere("qwen3:8b", 32768) == []


def test_auto_passt_sofort(monkeypatch):
    k, abgestuft, ollama = _laden(monkeypatch, lambda m, k: True)
    assert (k.modell, k.kontext, abgestuft) == ("gemma4:12b", 32768, False)
    assert k.belegung() == {"ganzAufKarte": True, "groesseMb": 8192, "aufKarteMb": 8192, "quantisierung": "Q4_K_M"}


def test_auto_eine_stufe_kleiner(monkeypatch):
    k, abgestuft, ollama = _laden(monkeypatch, lambda m, k: m == "gemma4:e4b")
    assert (k.modell, k.kontext, abgestuft) == ("gemma4:e4b", 24576, True)
    entladen = [b for p, b in ollama.anfragen if p == "/api/generate" and b.get("keep_alive") == 0]
    assert len(entladen) == 2  # 12b mit 32k und mit 24k wieder entladen


def test_auto_kleinste_stufe_auch_wenn_nichts_passt(monkeypatch):
    k, abgestuft, _ = _laden(monkeypatch, lambda m, k: False)
    assert (k.modell, k.kontext, abgestuft) == ("gemma4:e4b", 12288, True)


def test_festes_modell_wird_nie_getauscht(monkeypatch):
    k, abgestuft, ollama = _laden(monkeypatch, lambda m, k: False, model="gemma4:12b")
    assert (k.modell, k.kontext, abgestuft) == ("gemma4:12b", 32768, False)
    assert not any(p == "/api/generate" for p, _ in ollama.anfragen)  # kein Vorladen ohne „auto“


def test_einlesen_und_schreiben_gemessen():
    from app.sprachmodell import OllamaKlient, Zaehler

    def antwort(req):
        if req.url.path == "/api/show":
            return httpx.Response(404, json={})
        zeilen = [{"message": {"content": '{"ok": true}'}, "done": True, "prompt_eval_count": 1000,
                   "prompt_eval_duration": 2 * 10 ** 9, "eval_count": 100, "eval_duration": 4 * 10 ** 9}]
        return httpx.Response(200, content="\n".join(json.dumps(z) for z in zeilen).encode())

    k = OllamaKlient("http://o:1", "gemma4:12b", client=httpx.Client(transport=httpx.MockTransport(antwort)))
    z = Zaehler()
    z.aufruf(k, "s", "n")
    assert z.einlesen_s_mittel == 500.0 and z.token_s_mittel == 25.0


def test_messwerte_kapitel(monkeypatch):
    from app import arbeitsweise, worker_prozess
    from app.sprachmodell import Zaehler

    monkeypatch.setattr(arbeitsweise, "grafikkarte", lambda: {"karte": "RTX 3060 Ti", "kartenspeicherMb": 8192})
    k, abgestuft, _ = _laden(monkeypatch, lambda m, k: m == "gemma4:e4b")
    z = Zaehler(token_s=[40.0, 44.0], einlesen_s=[800.0])
    m = worker_prozess.llm_messwerte(k, z, abgestuft)
    assert m == {"art": "kapitel", "modell": "gemma4:e4b", "kontext": 24576, "einlesenTokS": 800.0,
                 "schreibenTokS": 42.0, "quantisierung": "Q4_K_M", "ganzAufKarte": True, "abgestuft": True,
                 "karte": "RTX 3060 Ti", "kartenspeicherMb": 8192}


def test_grafikkarte_ueber_nvidia_smi(monkeypatch):
    import subprocess
    import types

    from app import arbeitsweise

    monkeypatch.setattr(arbeitsweise.shutil, "which", lambda n: "/usr/bin/nvidia-smi")
    monkeypatch.setattr(arbeitsweise.subprocess, "run", lambda *a, **k: types.SimpleNamespace(
        stdout="NVIDIA GeForce RTX 4060 Ti, 16380\n"))
    assert arbeitsweise.grafikkarte() == {"karte": "NVIDIA GeForce RTX 4060 Ti", "kartenspeicherMb": 16380}
    monkeypatch.setattr(arbeitsweise.subprocess, "run", lambda *a, **k: (_ for _ in ()).throw(
        subprocess.TimeoutExpired("x", 1)))
    assert arbeitsweise.grafikkarte() == {}
    monkeypatch.setattr(arbeitsweise.shutil, "which", lambda n: None)
    assert arbeitsweise.grafikkarte() == {}


def test_stufen_der_abschrift(tmp_path):
    from app.transkription import Stufen, abschrift_messwerte

    st = Stufen()
    with st("erkennung"):
        pass
    with st("erkennung"):
        pass
    m = abschrift_messwerte({"audioSeconds": 3600, "computeSeconds": 360}, st)
    assert m["art"] == "abschrift" and m["minutenJeStunde"] == 6.0 and set(m["stufen"]) == {"erkennung"}


def test_tisch_misst_jeden_schritt(tmp_path):
    from app.transkription import Stufen, _tisch
    from tests.test_step4 import IntroMotor
    from tests.test_step2a import audio_abschnitte

    dateien = audio_abschnitte(tmp_path, (12,))
    st = Stufen()
    _tisch(IntroMotor(), {"expectedSpeakers": 2}, dateien, tmp_path, lambda p: None, "de", [], st)
    assert set(st.s) == {"einlesen", "erkennung", "ausrichtung", "sprechertrennung", "auswertung"}


# ---------------------------------------------------------------- Server: speichern und zeigen
def test_messwerte_bereinigt():
    from app import messwerte

    roh = {"art": "abschrift", "minutenJeStunde": 6.04, "stufen": {"erkennung": 78, "boese": 1, "ausrichtung": -2},
           "karte": "<b>RTX</b>" + "x" * 200, "einlesenTokS": float("nan"), "fremd": {"a": 1}, "ganzAufKarte": "ja"}
    m = messwerte.bereinigen(roh)
    assert m == {"art": "abschrift", "minutenJeStunde": 6.0, "stufen": {"erkennung": 78.0},
                 "karte": ("<b>RTX</b>" + "x" * 200)[:80]}
    assert messwerte.bereinigen(None) == {} and messwerte.bereinigen([1]) == {}


def test_messwerte_ueber_worker_bis_in_die_warteschlange(client, world, dbs, tmp_path, admin):  # noqa: F811
    from fastapi.testclient import TestClient

    from app.models import Job
    from app.worker_prozess import WorkerProzess
    from tests.test_step2a import audio_abschnitte, hochladen, neue_session, worker_token

    def verarbeite(auftrag, dateien, arbeit, fortschritt):
        return {"audioSeconds": 60, "computeSeconds": 6, "segments": [], "speakers": [],
                "metrics": {"art": "abschrift", "minutenJeStunde": 6.0, "stufen": {"erkennung": 3.0},
                            "karte": "RTX 3060 Ti", "kartenspeicherMb": 8192, "boese": "<script>"}}

    s = neue_session(client, world)
    hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (31,)))
    gemeldet = []
    knecht = WorkerProzess("http://testserver", worker_token(dbs), tmp_path / "k", verarbeite,
                           client=TestClient(client.app), claim_wait=0, melden=lambda e, **d: gemeldet.append((e, d)))
    assert knecht.einen_auftrag()
    dbs.expire_all()
    job = dbs.query(Job).filter_by(session_id=s["id"], type="transcribe").one()
    assert json.loads(job.messwerte) == {"art": "abschrift", "minutenJeStunde": 6.0, "stufen": {"erkennung": 3.0},
                                         "karte": "RTX 3060 Ti", "kartenspeicherMb": 8192}
    fertig = [d for e, d in gemeldet if e == "fertig"]
    assert fertig and fertig[0]["messwerte"]["minutenJeStunde"] == 6.0
    seite = client.get("/verwaltung/warteschlange").text
    assert "6,0 min Rechenzeit je Aufnahmestunde" in seite and "Erkennung 0,1 min" in seite and "<script>" not in seite
    # Spielende sehen nichts davon
    assert "messwerte" not in json.dumps(client.get(f"{API}/sessions/{s['id']}", headers=world["pl"]).json()).lower()


def test_messwerte_texte_uebersetzt():
    from app import messwerte
    from app.verwaltung.i18n import EN

    assert set(messwerte.TEXTE_UI) <= set(EN)


# ---------------------------------------------------------------- Cloud: Zwischenspeicher
def test_zwischengespeicherte_token_guenstiger():
    from app import sprachmodell as sm

    def anbieter(req):
        return httpx.Response(200, json={"choices": [{"message": {"content": '{"ok": true}'}}], "usage": {
            "prompt_tokens": 100_000, "completion_tokens": 0, "prompt_tokens_details": {"cached_tokens": 80_000}}})

    mistral = sm.OpenAIKlient("https://api.mistral.ai/v1", "sk", "mistral-large-latest",
                              httpx.Client(transport=httpx.MockTransport(anbieter)), cent_pro_mio=(50, 150))
    andere = sm.OpenAIKlient("https://llm.example/v1", "sk", "m",
                             httpx.Client(transport=httpx.MockTransport(anbieter)), cent_pro_mio=(50, 150))
    z1, z2 = sm.Zaehler(), sm.Zaehler()
    z1.aufruf(mistral, "s", "n")
    z2.aufruf(andere, "s", "n")
    assert z1.tokens_zwischengespeichert == 80_000 and z1.tokens_in == 100_000
    assert z1.kosten == pytest.approx((20_000 + 8_000) * 50 / 1e6) and z2.kosten == pytest.approx(5.0)


def test_exklusive_trennung_standard_aus():
    from app.config import Settings

    assert Settings().sprecher_exklusiv is False
