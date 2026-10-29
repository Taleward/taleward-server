"""Server 0.4.140: Begriffslisten für 22 Systeme mit Sprachabschnitten (app/begriffe.py) – Schreibhilfe nur Stufe A,
System auch über den Namen, Wörterbuch, Verhörer als Vorschlag, Regelfilter mit Zahl-Regel."""
import pytest

from app import artefakte, begriffe

from tests.test_step2a import API
from tests.test_step4 import _kleine_teile  # noqa: F401 (Fixture)


def test_alle_listen_lesbar_und_im_rahmen():
    alle = begriffe.alle()
    assert len(alle) == 22
    for k, li in alle.items():
        assert li.erkennung, k
        for sp in ("", "en"):
            assert len(li.stufe_a.get(sp, ())) <= 40, k
        assert all(len(e.split()) <= 2 for sp in ("", "en") for e in li.stufe_a.get(sp, ())), k
        for sp in ("de", "en"):
            hilfe = ", ".join(begriffe.schreibhilfe(k, sp))
            assert len(hilfe) <= 600, (k, sp, len(hilfe))
        for falsch, richtig in li.verhoerer:
            assert falsch and richtig and falsch != richtig


def test_keine_verlage_oder_autoren():
    for k in begriffe.alle():
        text = (begriffe.ORDNER / f"{k}.txt").read_text(encoding="utf-8")
        for verboten in ("Ulisses Spiele", "Pegasus Spiele", "Uhrwerk Verlag",
                         "Mike Pondsmith", "Sapkowski"):
            assert verboten not in text, (k, verboten)


def test_system_ueber_den_namen():
    f = begriffe.system_fuer
    assert f("dsa") == "dsa" and f("other", "The Witcher") == "witcher" and f(None, "Warhammer 40k") == "warhammer40k"
    assert f("other", "Warhammer Fantasy 4. Edition") == "warhammer" and f("other", "Warhammer") is None
    assert f("other", "Mutant: Jahr Null") == "mutantjahrnull" and f("other", "D&D 5e") == "dnd"
    assert f("other", "Torgau") is None and f("other", "Fate Core") is None and f(None, None) is None


def test_schreibhilfe_je_sprache():
    de, en = begriffe.schreibhilfe("dnd", "de"), begriffe.schreibhilfe("dnd", "en")
    assert de[0] == "Faerûn" and "Waterdeep" not in de and "Rettungswurf" in de
    assert en[0] == "Dungeon Master" and "Waterdeep" in en and "Advantage" in en
    assert "Spielerhandbuch" not in de  # Stufe B nie in der Schreibhilfe


def test_namenshilfe_nimmt_die_liste_aus_dem_systemnamen(client, world, dbs):
    from app import namenshilfe
    from app.models import Campaign

    c = dbs.get(Campaign, world["cid"])
    c.system, c.system_name, c.language = "other", "The Witcher", "de"
    dbs.commit()
    hilfe = namenshilfe.fuer_kampagne(dbs, c)
    assert "Nilfgaard" in hilfe and "Wyzima" in hilfe and "Vizima" not in hilfe
    assert len(", ".join(hilfe)) <= namenshilfe.MAX_ZEICHEN


def test_zahlregel_fuer_alltagswoerter():
    text = ("Mira biss die Zähne zusammen, der Schmerz plus eins machte ihr zu schaffen. Der Schmerz in ihrer Brust "
            "blieb. Ihre Belastung 2 war hoch. Sie hatte zwei tiefe Wunden am Arm.")
    aus, befunde = artefakte.kapitel(text, [], [], (), ("Schmerz", "Belastung", "Wunden"))
    assert "plus eins" not in aus and "Belastung 2" not in aus
    assert "Der Schmerz in ihrer Brust blieb." in aus and "zwei tiefe Wunden am Arm" in aus
    assert [b["art"] for b in befunde].count("regel") == 2


def test_fach_und_alltag_mit_wortliste():
    fach, alltag = begriffe.regelwoerter("dsa", frozenset({"schmerz", "furcht", "attacke", "parade", "initiative"}))
    assert "Astralpunkte" in fach and "Schmerz" in alltag and "Furcht" in alltag and "Astralpunkte" not in alltag


def test_woerterbuch_und_verhoerer_als_vorschlag(client, world, dbs, tmp_path):
    import json

    from app import unsicher, woerterbuch
    from app.models import Campaign, TranscriptSegment
    from tests.test_step2b import transkribiert

    c = dbs.get(Campaign, world["cid"])
    c.system, c.system_name = "shadowrun", None
    dbs.commit()
    b = woerterbuch.kampagne(dbs, c)
    assert b.enthaelt("Saeder-Krupp") and b.enthaelt("Renraku") and b.enthaelt("Tamanous")  # auch Stufe B
    s = transkribiert(client, world, dbs, tmp_path)
    dbs.add(TranscriptSegment(session_id=s["id"], position=98, start=10.0, end=12.0, speaker_id=None,
                              text="Ruf den Tschummer an, sagte sie.",
                              unsicher=json.dumps([{"word": "Tschummer", "score": 0.2}])))
    dbs.commit()
    from app.models import GameSession

    alle = unsicher.begriffe(dbs, dbs.get(GameSession, s["id"]))
    t = {x["heard"]: x for x in alle}
    assert t["Tschummer"]["alternatives"][0] == "Chummer"
    seg = dbs.query(TranscriptSegment).filter_by(session_id=s["id"], position=98).one()
    assert "Tschummer" in seg.text  # nie still ersetzt
    assert client.get(f"{API}/sessions/{s['id']}", headers=world["gm"]).status_code == 200


def test_eingabe_traegt_die_aufteilung(world, dbs):
    from datetime import datetime, timezone

    from app.models import Campaign, GameSession
    from app.zusammenfassung import eingabe_bauen

    c = dbs.get(Campaign, world["cid"])
    c.system = "dsa"
    s = GameSession(campaign_id=c.id, number=1, state="awaiting_review", played_at=datetime(2026, 10, 1, tzinfo=timezone.utc))
    dbs.add(s)
    dbs.commit()
    e = eingabe_bauen(dbs, s).als_dict()
    assert "Astralpunkte" in e["regel_fach"] + e["regel_alltag"] and "Schmerz" in e["regel_alltag"]


# ---------------------------------------------------------------- Sprachbaustein (punkt_tab) über den Server
def _zip(dateien: dict[str, bytes]) -> bytes:
    import io
    import zipfile

    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("punkt_tab/", b"")
        for pfad, inhalt in dateien.items():
            z.writestr(pfad, inhalt)
    return puffer.getvalue()


PUNKT = {"punkt_tab/README": b"Pretrained Punkt Models -- Jan Strunk\n",
         "punkt_tab/german/abbrev_types.txt": b"z.b\nusw\n", "punkt_tab/german/ortho_context.tab": b"der\t12\n",
         "punkt_tab/english/sent_starters.txt": b"the\n"}


@pytest.fixture()
def baustein(monkeypatch):
    """Feste Quelle mit einer kleinen ZIP, Spiegel aus; liefert (inhalt, protokoll)."""
    import hashlib

    from app import modellablage
    from app.config import get_settings

    inhalt = _zip(PUNKT)
    fassung = "ab" * 20
    monkeypatch.setitem(modellablage.DIREKT, modellablage.SPRACHBAUSTEIN,
                        (fassung, "https://quelle.example/{fassung}/punkt_tab.zip", hashlib.sha256(inhalt).hexdigest()))
    monkeypatch.setenv("MODEL_MIRROR_URL", "")
    get_settings.cache_clear()
    yield inhalt, fassung
    get_settings.cache_clear()


def _klient(inhalt, protokoll=None):
    import httpx

    def antwort(r):
        if protokoll is not None:
            protokoll.append(str(r.url))
        return httpx.Response(200, content=inhalt) if r.url.path.endswith("/punkt_tab.zip") else httpx.Response(404)

    return httpx.Client(transport=httpx.MockTransport(antwort))


def test_sprachbaustein_auf_dem_server_und_beim_worker(client, dbs, baustein, monkeypatch):
    from fastapi.testclient import TestClient

    from app import modellablage, modelle, transkription
    from tests.test_step2a import worker_token

    inhalt, fassung = baustein
    protokoll = []
    stand = modellablage.holen(dbs, modellablage.SPRACHBAUSTEIN, klient=_klient(inhalt, protokoll))
    assert protokoll == [f"https://quelle.example/{fassung}/punkt_tab.zip"]
    assert stand.fassung == fassung and stand.quelle == "direkt"
    assert {d["pfad"] for d in stand.dateien} == {f"tokenizers/{p}" for p in PUNKT}
    ordner = modellablage._ordner(modellablage.SPRACHBAUSTEIN, fassung)
    assert "Jan Strunk" in (ordner / "NOTICE.txt").read_text(encoding="utf-8")
    assert not list(ordner.parent.glob("*.zip"))  # ZIP nicht liegen gelassen
    # Worker: in der Liste der Modelle, Laden über das Worker-Protokoll mit Prüfsummen
    h = {"Authorization": f"Bearer {worker_token(dbs)}"}
    wc = TestClient(client.app)
    assert wc.get("/worker/v1/config", headers=h).json()["models"] == {modellablage.SPRACHBAUSTEIN: fassung}
    quelle = modelle.ServerQuelle("http://testserver", h["Authorization"].removeprefix("Bearer "), klient=wc)
    eingetragen = []
    monkeypatch.setattr(transkription, "satz_pfad_eintragen", lambda p: eingetragen.append(p) or True)
    motor = transkription.WhisperXMotor.__new__(transkription.WhisperXMotor)
    motor.quelle = quelle
    b = motor._sprachbaustein()
    assert (b.pfad / "tokenizers" / "punkt_tab" / "german" / "abbrev_types.txt").read_bytes() == b"z.b\nusw\n"
    assert eingetragen == [b.pfad]
    # älterer Server oder Fehler: kein Abbruch, alles wie bisher
    motor.quelle = None
    assert motor._sprachbaustein() is None
    motor.quelle = quelle
    monkeypatch.setattr(modelle, "vom_server", lambda *a: (_ for _ in ()).throw(modelle.ModellFehler("weg")))
    assert motor._sprachbaustein() is None


def test_sprachbaustein_falsche_pruefsumme_oder_pfade(client, dbs, baustein):
    from app import modellablage

    inhalt, fassung = baustein
    with pytest.raises(modellablage.AblageFehler, match="Prüfsumme"):
        modellablage.holen(dbs, modellablage.SPRACHBAUSTEIN, klient=_klient(inhalt + b"x"))
    assert modellablage.vorhanden(dbs, modellablage.SPRACHBAUSTEIN) is None
    for boese in ({**PUNKT, "punkt_tab/../../boese.py": b"x"}, {**PUNKT, "anderes/datei.txt": b"x"},
                  {"punkt_tab/english/sent_starters.txt": b"the\n"}):
        z = _zip(boese)
        import hashlib

        modellablage.DIREKT[modellablage.SPRACHBAUSTEIN] = (fassung, "https://quelle.example/{fassung}/punkt_tab.zip",
                                                            hashlib.sha256(z).hexdigest())
        with pytest.raises(modellablage.AblageFehler):
            modellablage.holen(dbs, modellablage.SPRACHBAUSTEIN, klient=_klient(z))
        assert modellablage.vorhanden(dbs, modellablage.SPRACHBAUSTEIN) is None
        assert not modellablage._ordner(modellablage.SPRACHBAUSTEIN, fassung).exists()


def test_sprachbaustein_aus_der_wartung(client, dbs, baustein):
    import httpx

    from app import modellablage
    from app.einstellungen import meta_lesen, meta_schreiben

    inhalt, fassung = baustein
    tot = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    assert modellablage.automatisch(dbs, klient=tot) is None
    assert meta_lesen(dbs, "sprachbaustein.versuch") is None  # nicht gebraucht: nichts versucht
    meta_schreiben(dbs, "betrieb.art", "lokal")
    dbs.commit()
    modellablage.automatisch(dbs, klient=tot)
    assert "nicht ladbar" in meta_lesen(dbs, "sprachbaustein.fehler")
    modellablage.automatisch(dbs, klient=_klient(inhalt))  # frühestens nach einer Stunde
    assert modellablage.vorhanden(dbs, modellablage.SPRACHBAUSTEIN) is None
    meta_schreiben(dbs, "sprachbaustein.versuch", "")
    dbs.commit()
    modellablage.automatisch(dbs, klient=_klient(inhalt))
    assert modellablage.vorhanden(dbs, modellablage.SPRACHBAUSTEIN).fassung == fassung
    assert meta_lesen(dbs, "sprachbaustein.fehler") == ""


def test_sprachbaustein_vom_spiegel(client, dbs, baustein, monkeypatch, tmp_path):
    import hashlib

    import httpx

    from app import modellablage
    from app.config import get_settings

    inhalt, fassung = baustein
    modellablage.holen(dbs, modellablage.SPRACHBAUSTEIN, klient=_klient(inhalt))
    ordner, summe = modellablage.spiegel_erstellen(dbs, tmp_path, modellablage.SPRACHBAUSTEIN)
    assert (ordner / "NOTICE.txt").exists() and (ordner / "tokenizers/punkt_tab/german/ortho_context.tab").exists()
    # neuer Server: lädt vom Spiegel, die feste Quelle wird nicht gefragt
    import shutil

    shutil.rmtree(modellablage._ordner(modellablage.SPRACHBAUSTEIN, fassung))
    monkeypatch.setitem(modellablage.SPIEGEL, modellablage.SPRACHBAUSTEIN, (fassung, summe))
    monkeypatch.setenv("MODEL_MIRROR_URL", "https://spiegel.example/modelle")
    get_settings.cache_clear()
    gefragt = []

    def spiegel(r):
        gefragt.append(r.url.path)
        pre = f"/modelle/{modellablage.SPRACHBAUSTEIN}/{fassung}/"
        datei = ordner / r.url.path[len(pre):]
        return httpx.Response(200, content=datei.read_bytes()) if r.url.path.startswith(pre) and datei.is_file() \
            else httpx.Response(404)

    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, f"modell.{modellablage.SPRACHBAUSTEIN}", "")
    dbs.commit()
    stand = modellablage.holen(dbs, modellablage.SPRACHBAUSTEIN, klient=httpx.Client(transport=httpx.MockTransport(spiegel)))
    assert stand.quelle == "spiegel" and not any(p.endswith(".zip") for p in gefragt)
    assert hashlib.sha256((ordner / "manifest.json").read_bytes()).hexdigest() == summe


def test_sprachbaustein_fest_eingetragen():
    from app import modellablage

    fassung, url, sha = modellablage.DIREKT[modellablage.SPRACHBAUSTEIN]
    assert len(fassung) == 40 and len(sha) == 64 and url.startswith("https://")
    assert modellablage.SPIEGEL[modellablage.SPRACHBAUSTEIN][0] == fassung


def test_satz_pfad_vorn_im_suchpfad(monkeypatch, tmp_path):
    import sys
    import types

    from app import transkription

    data = types.ModuleType("nltk.data")
    data.path = ["/usr/share/nltk_data"]
    paket = types.ModuleType("nltk")
    paket.data = data
    monkeypatch.setitem(sys.modules, "nltk", paket)
    monkeypatch.setitem(sys.modules, "nltk.data", data)
    assert transkription.satz_pfad_eintragen(tmp_path)
    assert transkription.satz_pfad_eintragen(tmp_path)
    assert data.path == [str(tmp_path), "/usr/share/nltk_data"]
