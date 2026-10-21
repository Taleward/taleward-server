"""Server 0.4.117: Taleward-Spiegel des Sprechermodells unter taleward.org/modelle ist eingetragen – neue Server
brauchen kein Hugging-Face-Konto mehr. Fällt der Spiegel aus, lädt ein Server mit eigenem Zugang von Hugging Face."""
import httpx
import pytest

from tests.test_modellablage import REPO, SHA, hf_transport, hf_zugang  # noqa: F401  (Fixture)
from tests.test_verwaltung import admin  # noqa: F401  (Fixture)


def test_spiegel_ist_eingetragen():
    from app import modellablage
    from app.config import Settings

    assert Settings.model_fields["model_mirror_url"].default == "https://taleward.org/modelle"
    fassung, summe = modellablage.SPIEGEL[REPO]
    assert len(fassung) == 40 and len(summe) == 64
    assert modellablage.FASSUNGEN.get(REPO) in (None, fassung)  # sonst würde der Spiegel nie genutzt


def _spiegel_an(monkeypatch):
    from app.config import get_settings

    monkeypatch.setenv("MODEL_MIRROR_URL", "https://spiegel.taleward.example/modelle")
    get_settings.cache_clear()


def test_spiegel_aus_faellt_auf_hugging_face_zurueck(client, dbs, hf_zugang, monkeypatch):  # noqa: F811
    from app import modellablage

    _spiegel_an(monkeypatch)
    protokoll: list[str] = []
    hf = hf_transport(protokoll=protokoll)

    def beide(request: httpx.Request) -> httpx.Response:
        if request.url.host == "spiegel.taleward.example":
            protokoll.append(str(request.url))
            return httpx.Response(404)
        return hf._transport.handle_request(request)

    stand = modellablage.holen(dbs, klient=httpx.Client(transport=httpx.MockTransport(beide)))
    assert stand.quelle == "huggingface" and stand.fassung == SHA
    assert protokoll[0].startswith("https://spiegel.taleward.example/modelle/" + REPO + "/")


def test_spiegel_aus_ohne_zugang_sagt_es(client, dbs, monkeypatch):
    from app import modellablage

    _spiegel_an(monkeypatch)
    klient = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(404)))
    with pytest.raises(modellablage.AblageFehler, match="Spiegel"):
        modellablage.holen(dbs, klient=klient)


def test_assistent_ohne_konto_mit_spiegel(client, admin, monkeypatch):  # noqa: F811
    from app import modellablage

    monkeypatch.setitem(modellablage.SPIEGEL, REPO, modellablage.SPIEGEL[REPO])
    client.post("/verwaltung/assistent/betrieb/art", data={"csrf": admin, "art": "lokal"})
    seite = client.get("/verwaltung/assistent/betrieb").text
    assert "Taleward-Spiegel" not in seite and "<details><summary>" not in seite  # Spiegel in Tests abgeschaltet
    _spiegel_an(monkeypatch)
    seite = client.get("/verwaltung/assistent/betrieb").text
    assert "Ein Konto braucht ihr dafür nicht." in seite
    assert "<details><summary>Stattdessen eigenen Hugging-Face-Zugang nutzen (freiwillig)</summary>" in seite


def test_transkription_hf_eingeklappt_mit_spiegel(client, admin, monkeypatch):  # noqa: F811
    seite = client.get("/verwaltung/transkription").text
    assert "Ohne den Taleward-Spiegel braucht er dafür einmalig einen Hugging-Face-Zugang." in seite
    assert "Eigenen Hugging-Face-Zugang nutzen" not in seite
    _spiegel_an(monkeypatch)
    seite = client.get("/verwaltung/transkription").text
    assert "einmal vom Taleward-Spiegel" in seite
    assert "<details><summary>Eigenen Hugging-Face-Zugang nutzen (freiwillig)</summary>" in seite
    en = client.get("/verwaltung/transkription", headers={"Accept-Language": "en"}).text
    assert "Use your own Hugging Face access (optional)" in en
