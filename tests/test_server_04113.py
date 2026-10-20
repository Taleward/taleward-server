"""Server 0.4.113: Der Worker-Container installiert nur mit Prüfsummen (wie die Worker-App)."""
import json
import re
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(WURZEL / "deploy"))
import ki_pakete  # noqa: E402

HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


def _export() -> str:
    if shutil.which("uv") is None:
        pytest.skip("uv fehlt")
    r = subprocess.run(["uv", "export", "--frozen", "--format", "requirements-txt", "--extra", "ki", "--no-dev",
                        "--no-emit-project", "--no-header", "--no-annotate", "-q"], cwd=WURZEL, check=True,
                       capture_output=True, text=True)
    return r.stdout


def _liste():
    return ki_pakete.pytorch_liste_lesen((WURZEL / "deploy" / "pytorch-pruefsummen.txt").read_text(encoding="utf-8"))


def test_dockerfile_installiert_nur_mit_pruefsummen():
    text = (WURZEL / "Dockerfile.worker").read_text(encoding="utf-8")
    befehle = re.sub(r"\\\n", " ", text)
    installs = re.findall(r"(?:uv pip|pip) install[^&\n]*", befehle)
    assert len(installs) == 6
    for befehl in installs:
        if befehl.rstrip().endswith(" ."):  # der Taleward-Code selbst: ohne Paketverzeichnis, ohne Bau-Umgebung
            assert "--no-index" in befehl and "--no-build-isolation" in befehl and "--no-deps" in befehl
        else:
            assert "--require-hashes" in befehl and "--no-deps" in befehl, befehl
    assert "engine-requirements.txt" not in text and "pip install --no-cache-dir uv" not in text


@pytest.mark.parametrize("datei", ["uv-werkzeug.txt", "bauwerkzeug.txt"])
def test_feste_listen_haben_pruefsummen(datei):
    text = (WURZEL / "deploy" / datei).read_text(encoding="utf-8")
    assert ki_pakete.eintraege(text) and ki_pakete.ohne_pruefsumme(text) == []
    assert all("==" in block.split()[0] for _, block in ki_pakete.eintraege(text))


def test_pytorch_liste_passt_zu_uv_lock():
    """Bei einem PyTorch-Wechsel die Liste neu erzeugen: python3 deploy/pytorch_pruefsummen.py uv.lock
    deploy/pytorch-pruefsummen.txt (braucht Zugang zu download.pytorch.org)."""
    lock = tomllib.loads((WURZEL / "uv.lock").read_text(encoding="utf-8"))
    fassung = {p["name"]: p["version"] for p in lock["package"] if p["name"] in ki_pakete.PYTORCH_VERZEICHNIS}
    liste = _liste()
    for variante in ki_pakete.VARIANTEN:
        for name, f in fassung.items():
            assert (variante, name, f"{f}+{variante}") in liste, (variante, name, f)


@pytest.mark.parametrize("variante", ki_pakete.VARIANTEN)
def test_listen_fuer_den_container(variante):
    geprueft, pytorch = ki_pakete.aufteilen(_export(), _liste(), variante)
    namen = [n for n, _ in ki_pakete.eintraege(geprueft)]
    assert ki_pakete.ohne_pruefsumme(geprueft) == [] and ki_pakete.ohne_pruefsumme(pytorch) == []
    assert "whisperx" in namen and "torchcodec" in namen and not set(namen) & set(ki_pakete.PYTORCH_VERZEICHNIS)
    assert any(n.startswith("nvidia-") for n in namen) is (variante == "cu128")
    assert [z.split("==")[0] for z in pytorch.splitlines()] == list(ki_pakete.PYTORCH_VERZEICHNIS)
    assert all(f"+{variante} " in z for z in pytorch.splitlines())
    bau = ki_pakete.bauwerkzeug((WURZEL / "deploy" / "bauwerkzeug.txt").read_text(encoding="utf-8"), geprueft)
    assert "hatchling" in [n for n, _ in ki_pakete.eintraege(bau)]
    assert not {n for n, _ in ki_pakete.eintraege(bau)} & set(namen)  # Fassungen aus uv.lock bleiben


def test_zeile_ohne_pruefsumme_bricht_ab(tmp_path):
    export = "six==1.17.0\nrequests==2.0.0 \\\n    --hash=sha256:" + "a" * 64 + "\n"
    with pytest.raises(ki_pakete.Fehler, match="six"):
        ki_pakete.aufteilen(export, {}, "cpu")
    with pytest.raises(ki_pakete.Fehler, match="torch"):
        ki_pakete.aufteilen("torch==2.10.0\n", {}, "cpu")
    with pytest.raises(ki_pakete.Fehler, match="Variante"):
        ki_pakete.aufteilen("", {}, "rocm")
    with pytest.raises(ki_pakete.Fehler):
        ki_pakete.bauwerkzeug("hatchling==1.0\n", "")
    (tmp_path / "e.txt").write_text(export, encoding="utf-8")
    (tmp_path / "l.txt").write_text("", encoding="utf-8")
    (tmp_path / "b.txt").write_text("", encoding="utf-8")
    assert ki_pakete.main(["x", *(str(tmp_path / n) for n in ("e.txt", "l.txt", "b.txt")), "cpu",
                           str(tmp_path / "aus")]) == 1
    assert not (tmp_path / "aus").exists()  # nichts geschrieben


def test_pytorch_liste_unlesbar():
    with pytest.raises(ki_pakete.Fehler):
        ki_pakete.pytorch_liste_lesen("cu128 torch==2.10.0+cu128 --hash=md5:abc\n")


# ================================================================ S41: Hörproben, „Dort ergänzen“, Links, Kleinkram
from tests.test_step4 import _kleine_teile  # noqa: E402,F401  (Fixture)
from tests.test_step5 import api  # noqa: E402,F401  (Fixture)
from tests.test_verwaltung import admin  # noqa: E402,F401  (Fixture)

API = "/api/v1"
OGG = b"OggS" + bytes(60)


def _b64(daten: bytes) -> str:
    import base64

    return base64.b64encode(daten).decode()


def _stimmen(client, world, dbs, tmp_path):
    from fastapi.testclient import TestClient

    from app.transkription import verarbeiter
    from app.worker_prozess import WorkerProzess
    from tests.test_schnittstelle_0421 import ProbenMotor
    from tests.test_step2a import audio_abschnitte, hochladen, neue_session, worker_token

    s = neue_session(client, world)
    hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (31,)))
    knecht = WorkerProzess("http://testserver", worker_token(dbs), tmp_path / "k", verarbeiter(ProbenMotor()),
                           client=TestClient(client.app), claim_wait=0)
    assert knecht.einen_auftrag()
    return s, client.get(f"{API}/sessions/{s['id']}/speakers", headers=world["gm"]).json()


def test_hoerprobe_nur_ascii_ziffern_und_nosniff(client, world, dbs, tmp_path):
    s, stimmen = _stimmen(client, world, dbs, tmp_path)
    basis = f"{API}/sessions/{s['id']}/speakers/{stimmen[0]['id']}/sample"
    for falsch in (chr(0xB2), chr(0x661), chr(0xFF11), " 1", "1 "):  # ², arabisch 1, breite 1
        assert client.get(basis, headers=world["gm"], params={"index": falsch}).status_code == 404, falsch
    r = client.get(basis, headers=world["gm"], params={"index": "1"})
    assert r.status_code == 200 and r.headers["x-content-type-options"] == "nosniff"


def test_proben_passen_zur_datei_auch_mit_luecke(tmp_path):
    import types

    from app import storage
    from app.routers.worker import SampleIn, _proben_speichern

    sp = types.SimpleNamespace(id="sp-1", sample_path=None, sample_text="alt", samples_json=None)
    proben = [SampleIn(start=1, end=2, text="Erste", ogg_base64=_b64(OGG + b"1")),
              SampleIn(start=3, end=4, text="   ", ogg_base64=_b64(OGG + b"2")),           # leer: fällt weg
              SampleIn(start=5, end=6, text="Dritte", ogg_base64=_b64(OGG + b"3"))]
    _proben_speichern("s-luecke", sp, proben)
    liste = json.loads(sp.samples_json)
    assert [p["text"] for p in liste] == ["Erste", "Dritte"]
    assert storage.sample_path("s-luecke", "sp-1", 1).read_bytes().endswith(b"3")  # samples[1] ↔ -1.ogg
    assert not storage.sample_path("s-luecke", "sp-1", 2).exists() and sp.sample_text == "Erste"


def test_proben_ohne_ogg_kennung_verworfen():
    import types

    from app.routers.worker import SampleIn, _hoerprobe, _proben_speichern

    assert _hoerprobe(_b64(b"<html>" + bytes(20))) is None and _hoerprobe("kein base64!") is None
    assert _hoerprobe(_b64(OGG)) == OGG and _hoerprobe(None) is None
    sp = types.SimpleNamespace(id="sp-2", sample_path=None, sample_text="", samples_json=None)
    _proben_speichern("s-falsch", sp, [SampleIn(start=0, text="Hallo", ogg_base64=_b64(b"RIFF" + bytes(40)))])
    assert sp.samples_json is None and sp.sample_path is None


def test_probenzeiten_endlich():
    from pydantic import ValidationError

    from app.routers.worker import SampleIn

    for falsch in (float("inf"), float("nan"), -1):
        with pytest.raises(ValidationError):
            SampleIn(start=falsch, text="x")
        with pytest.raises(ValidationError):
            SampleIn(start=0, end=falsch, text="x")


def test_hoerproben_hoechstens_bis_zur_frist(client, world, dbs, tmp_path):
    from datetime import timedelta

    from sqlalchemy import update

    from app import aufbewahrung
    from app.db import utcnow
    from app.models import Speaker, Upload

    s, stimmen = _stimmen(client, world, dbs, tmp_path)
    basis = f"{API}/sessions/{s['id']}/speakers/{stimmen[0]['id']}/sample"
    assert aufbewahrung.abgelaufene_loeschen(dbs, 7) >= 0
    dbs.commit()
    assert client.get(basis, headers=world["gm"]).status_code == 200  # innerhalb der Frist bleibt sie
    dbs.execute(update(Upload).where(Upload.session_id == s["id"]).values(completed_at=utcnow() - timedelta(days=8)))
    dbs.commit()
    aufbewahrung.abgelaufene_loeschen(dbs, 7)
    dbs.commit()
    assert client.get(basis, headers=world["gm"]).status_code == 410
    assert all(sp.sample_path is None for sp in dbs.query(Speaker).filter_by(session_id=s["id"]))
    assert len(client.get(f"{API}/sessions/{s['id']}/speakers", headers=world["gm"]).json()[0]["samples"]) == 3


def test_datenschutz_nennt_frist_der_hoerproben(dbs):
    from app.datenschutz import vorlage

    assert "Stimmen zugeordnet hat, höchstens" in vorlage(dbs)


# ---------------------------------------------------------------- „Dort ergänzen“ (S41.5, S41.11b)
def _vorschlaege(client, w, dbs, tmp_path, api, liste):  # noqa: F811
    from tests.test_step2b import zusammenfassen
    from tests.test_step5 import einstellen, mit_geheimnissen

    s, oeff, geheim, teilweise = mit_geheimnissen(client, w, dbs, tmp_path)
    api.vorschlaege = liste(oeff, geheim)
    einstellen(dbs, art="api", api_key="sk-test-schluessel-1234", weg="abschrift")
    assert zusammenfassen(dbs)
    vs = {p["title"]: p for p in client.get(f"{API}/sessions/{s['id']}/proposals", headers=w["gm"]).json()}
    return s, oeff, geheim, vs


def _eintraege(client, w, h):
    return {e["name"]: e for e in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=h).json()}


def test_geheimes_ergaenzen_ohne_erwaehnung_und_mit_ausnahmen(client, world, dbs, tmp_path, api):  # noqa: F811
    w = world
    s, oeff, geheim, vs = _vorschlaege(client, w, dbs, tmp_path, api, lambda oeff, geheim: [
        {"entryType": "npc", "action": "create", "title": "Kuno", "detail": "Ist ein Spion.",
         "suggestedVisibility": "public"},
        {"entryType": "npc", "action": "create", "title": "Fürst Grau", "detail": "Hat eine Tochter.",
         "suggestedVisibility": "public"}])
    vorher = _eintraege(client, w, w["pl"])["Rabenfels"]
    # (a) geheimer Vorschlag in einen öffentlichen Eintrag
    assert client.patch(f"{API}/proposals/{vs['Kuno']['id']}", headers=w["gm"], json={
        "mergeIntoEntryId": oeff["id"], "visibility": "gm_only", "decision": "accepted"}).status_code == 200
    # (b) öffentlicher Vorschlag mit Ausnahmen in einen geheimen Eintrag
    assert client.patch(f"{API}/proposals/{vs['Fürst Grau']['id']}", headers=w["gm"], json={
        "mergeIntoEntryId": geheim["id"], "hiddenFromMemberIds": [w["pl_member"]],
        "decision": "accepted"}).status_code == 200
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    nachher = _eintraege(client, w, w["pl"])["Rabenfels"]
    assert nachher["updatedAt"] == vorher["updatedAt"]
    assert all(m["sessionNumber"] != s["number"] for m in nachher["mentions"])
    assert "Kuno" in _eintraege(client, w, w["gm"])["Rabenfels"]["gmNotes"]
    sl = _eintraege(client, w, w["gm"])[geheim["name"]]
    assert w["pl_member"] in sl["hiddenFromMemberIds"] and "Hat eine Tochter." in sl["summary"]


def test_ergaenzen_ins_leere_ohne_text_bleibt_geheim(client, world, dbs, tmp_path, api):  # noqa: F811
    w = world
    ziel = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"],
                       json={"type": "npc", "name": "Tarvok", "summary": "Ein Söldner.", "visibility": "public"}).json()
    s, _, _, vs = _vorschlaege(client, w, dbs, tmp_path, api, lambda oeff, geheim: [
        {"entryType": "npc", "action": "create", "title": "Tharvok", "detail": "", "suggestedVisibility": "public"}])
    assert client.patch(f"{API}/proposals/{vs['Tharvok']['id']}", headers=w["gm"], json={
        "mergeIntoEntryId": ziel["id"], "decision": "accepted"}).status_code == 200
    assert client.delete(f"{API}/entries/{ziel['id']}", headers=w["gm"]).status_code == 204
    assert client.post(f"{API}/sessions/{s['id']}/publish", headers=w["gm"]).status_code == 200
    assert _eintraege(client, w, w["gm"])["Tharvok"]["visibility"] == "gm_only"
    assert "Tharvok" not in _eintraege(client, w, w["pl"])


def test_charakter_vorschlag_ohne_dort_ergaenzen(client, world, dbs):
    from app.models import Proposal

    w = world
    ziel = client.post(f"{API}/campaigns/{w['cid']}/entries", headers=w["gm"],
                       json={"type": "npc", "name": "Tarvok", "visibility": "public", "summary": "x"}).json()
    p = Proposal(campaign_id=w["cid"], entry_type="npc", action="create", title="Tharvok", detail="Ein Freund.",
                 suggested_visibility="gm_only", source="character")
    dbs.add(p)
    dbs.commit()
    r = client.patch(f"{API}/proposals/{p.id}", headers=w["gm"], json={"mergeIntoEntryId": ziel["id"]})
    assert r.status_code == 400 and r.json()["code"] == "invalid_input"


# ---------------------------------------------------------------- Aliasse und Namen (S41.11a)
@pytest.mark.parametrize("aliase", [[""], ["x" * 41]])
def test_alias_ausserhalb_der_grenzen_abgelehnt(client, world, aliase):
    r = client.post(f"{API}/campaigns/{world['cid']}/entries", headers=world["gm"],
                    json={"type": "npc", "name": "Tarvok", "aliases": aliase})
    assert r.status_code == 400


def test_steuer_und_richtungszeichen_entfernt(client, world):
    rlo, lri, pdi, zwnj = chr(0x202E), chr(0x2066), chr(0x2069), chr(0x200C)
    r = client.post(f"{API}/campaigns/{world['cid']}/entries", headers=world["gm"], json={
        "type": "npc", "name": "Tar" + rlo + "vok" + chr(0), "aliases": ["Der" + lri + " Alte" + pdi, "A" + chr(7) + "B"]})
    assert r.status_code == 201, r.text
    e = r.json()
    assert e["name"] == "Tar vok" and e["aliases"] == ["Der Alte", "A B"]
    from app.aliasse import zeichen_bereinigen

    assert zeichen_bereinigen("Mehr" + zwnj + "dad") == "Mehr" + zwnj + "dad"  # ZWNJ gehört zu manchen Schriften


# ---------------------------------------------------------------- Links des Betreibers (S41.6)
@pytest.mark.parametrize("url,ok", [
    ("https://verein.example/impressum", True),
    ("https://vertraut.example@fremd.example/", False), ("https://a.example/ x", False),
    ("https://a.example\\@b.example/", False), ("http://verein.example/", False), ("https://", False),
    ("https://a.example/" + chr(7), False), ("javascript:alert(1)", False), ("https://a.example/" + "x" * 600, False),
])
def test_betreiber_link(url, ok):
    from app.links import betreiber_link

    assert betreiber_link(url) is ok


def test_falscher_link_nie_in_info(client, dbs):
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "impressum.url", "https://vertraut.example@fremd.example/")
    meta_schreiben(dbs, "impressum.text", "")
    meta_schreiben(dbs, "nutzungsbedingungen.url", "https://a.example\\@b.example/")
    dbs.commit()
    info = client.get(f"{API}/info").json()
    assert info["legalNoticeUrl"] is None and "fremd.example" not in json.dumps(info)
    assert "b.example" not in (info["termsUrl"] or "")


# ---------------------------------------------------------------- Kleinkram (S41.7, S41.10, S41.11c)
def test_zugestimmt_nur_mit_rueckfrage(dbs, world, monkeypatch):
    from typer.testing import CliRunner

    from app import cli
    from app.models import User

    monkeypatch.setattr(cli, "_db", lambda: dbs)
    runner = CliRunner()
    r = runner.invoke(cli.app, ["create-user", "-u", "fremd", "-n", "Fremd", "--zugestimmt"], input="n\n")
    assert r.exit_code == 1 and "eigenes Konto" in r.output
    assert dbs.query(User).filter_by(username="fremd").first() is None
    r = runner.invoke(cli.app, ["create-user", "-u", "fremd", "-n", "Fremd", "--zugestimmt"],
                      input="y\ngeheim-geheim\ngeheim-geheim\n")
    assert r.exit_code == 0, r.output
    assert dbs.query(User).filter_by(username="fremd").one().terms_accepted_version is not None


def test_zugestimmt_fuer_das_erste_konto_ohne_rueckfrage(dbs, monkeypatch):
    from typer.testing import CliRunner

    from app import cli

    monkeypatch.setattr(cli, "_db", lambda: dbs)
    r = CliRunner().invoke(cli.app, ["create-user", "-u", "ich", "-n", "Ich", "--zugestimmt", "--admin"],
                           input="geheim-geheim\ngeheim-geheim\n")
    assert r.exit_code == 0 and "eigenes Konto" not in r.output


def test_einzelplatz_rechtstexte_aus_dem_netz():
    from app.einzelplatz import pfad_aus_dem_netz as aus_dem_netz

    assert aus_dem_netz("/impressum") and aus_dem_netz("/nutzungsbedingungen") and not aus_dem_netz("/verwaltung/")


def test_neue_fassung_ohne_servernamen_abgelehnt(client, dbs, admin, monkeypatch):  # noqa: F811
    from app import nutzungsbedingungen
    from app.einstellungen import angaben

    a = angaben(dbs)
    monkeypatch.setattr("app.einstellungen.angaben", lambda db: type(a)(**{**a.__dict__, "server_name": ""}))
    vorher = nutzungsbedingungen.version(dbs)
    r = client.post("/verwaltung/nutzungsbedingungen/neue-fassung", data={"csrf": admin, "bestaetigung": ""},
                    follow_redirects=False)
    assert r.status_code == 303 and "nb=falsch" in r.headers["location"]
    dbs.expire_all()
    assert nutzungsbedingungen.version(dbs) == vorher
