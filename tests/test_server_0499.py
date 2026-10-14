"""Server 0.4.99: Aufnahmen werden nie länger verarbeitet als erlaubt – auch bei sehr vielen Tonspuren; Hochladen und
Messen belegen den Server nicht unnötig."""
import asyncio
import shutil
import subprocess

import pytest
from sqlalchemy import select

from tests.test_abrechnung_0485 import _modus
from tests.test_step2a import API, _kleine_teile, audio_abschnitte, hochladen, neue_session  # noqa: F401

pytestmark = pytest.mark.skipif(shutil.which("ffmpeg") is None, reason="ffmpeg fehlt")


def _viele_spuren(tmp_path, erste_s: int = 120, weitere: int = 9):
    """Spur 1 lang mit niedriger Bitrate, dazu viele kurze Spuren – solche Dateien gelten als nicht messbar."""
    pfad = tmp_path / "viele.mka"
    befehl = ["ffmpeg", "-loglevel", "error", "-y", "-f", "lavfi", "-i", f"sine=frequency=440:duration={erste_s}"]
    for i in range(weitere):
        befehl += ["-f", "lavfi", "-i", f"sine=frequency={500 + 50 * i}:duration=1"]
    for i in range(weitere + 1):
        befehl += ["-map", f"{i}:a"]
    subprocess.run(befehl + ["-c:a", "aac", "-b:a", "16k", str(pfad)], check=True)
    return pfad


def test_zusammenfuegen_hoert_an_der_grenze_auf(tmp_path):
    from app import audio

    dateien = audio_abschnitte(tmp_path, (20, 20))
    wav = tmp_path / "gut.wav"
    starts = audio.zusammenfuegen(dateien, wav, 50)
    assert starts[0] == 0 and 19.5 < starts[1] < 20.5 and 39.5 < audio.dauer(wav) < 40.5
    zu_lang = tmp_path / "zu-lang.wav"
    with pytest.raises(audio.AudioFehler) as e:
        audio.zusammenfuegen(dateien, zu_lang, 30)
    assert e.value.code == "recording_too_long" and not e.value.retryable
    # nie mehr als die Grenze (plus eine Sekunde) dekodiert
    assert zu_lang.stat().st_size <= 31 * audio.SAMPLE_RATE * 2 + 1024


def test_viele_tonspuren_werden_nicht_ueber_die_grenze_verarbeitet(tmp_path, monkeypatch):
    from app import audio

    datei = _viele_spuren(tmp_path, erste_s=120)
    assert audio.dauer_dekodiert(datei) is None  # zu viele Spuren: nicht messbar
    with pytest.raises(audio.AudioFehler) as e:
        audio.zusammenfuegen([datei], tmp_path / "a.wav", 60)
    assert e.value.code == "recording_too_long"
    monkeypatch.setattr(audio, "GRENZE_S", 30)  # auch ohne Angabe gilt eine feste Grenze
    with pytest.raises(audio.AudioFehler) as e:
        audio.zusammenfuegen([datei], tmp_path / "b.wav")
    assert e.value.code == "recording_too_long"


def test_nicht_messbar_mit_abrechnung_abgelehnt(client, world, dbs, tmp_path):
    from app import storage
    from app.models import Upload

    _modus(dbs)
    s = neue_session(client, world)
    up = hochladen(client, world["gm"], s["id"], [_viele_spuren(tmp_path, erste_s=30)], abschliessen=False)
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=world["gm"])
    assert r.status_code == 409 and r.json()["code"] == "recording_unreadable"
    assert "MP3" in r.json()["message"]
    dbs.expire_all()
    assert dbs.get(Upload, up["uploadId"]).state == "aborted" and not storage.upload_dir(up["uploadId"]).exists()


def test_worker_bekommt_die_grenze_nur_mit_abrechnung(client, world, dbs, tmp_path):
    from app import abrechnung
    from app.models import Job
    from app.routers.worker import _auftrag

    s = neue_session(client, world)
    hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (5,)))
    job = dbs.scalars(select(Job).where(Job.session_id == s["id"], Job.type == "transcribe")).one()
    assert "maxAudioSeconds" not in _auftrag(dbs, job)["session"]
    _modus(dbs)
    assert _auftrag(dbs, job)["session"]["maxAudioSeconds"] == abrechnung.MAX_AUFNAHME_SEKUNDEN + 60


def test_cloud_weg_dekodiert_nicht_ueber_die_grenze(dbs, monkeypatch):
    from app import abrechnung, audio

    gesehen = []
    monkeypatch.setattr(audio, "zusammenfuegen", lambda d, z, h=None: gesehen.append(h))
    assert abrechnung.verarbeitung_hoechstens(dbs) is None
    _modus(dbs)
    assert abrechnung.verarbeitung_hoechstens(dbs) == abrechnung.MAX_AUFNAHME_SEKUNDEN + 60


def test_messplatz_wartet_nicht_lange(client, world, dbs, tmp_path, monkeypatch):
    import time

    from app import errors
    from app.models import Upload
    from app.routers import uploads

    assert uploads.MESSPLATZ_WARTEN_S <= 60
    monkeypatch.setattr(uploads, "MESSPLATZ_WARTEN_S", 0.2)
    _modus(dbs)
    s = neue_session(client, world)
    up = hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (5,)), abschliessen=False)
    u = dbs.get(Upload, up["uploadId"])
    assert uploads._MESSPLAETZE.acquire(timeout=1) and uploads._MESSPLAETZE.acquire(timeout=1)
    try:
        t0 = time.monotonic()
        with pytest.raises(errors.ApiError) as e:
            uploads._messen(dbs, u)
        assert e.value.status == 429 and time.monotonic() - t0 < 5
        assert u.state == "open"  # die App kann den Abschluss erneut versuchen
    finally:
        uploads._MESSPLAETZE.release()
        uploads._MESSPLAETZE.release()
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=world["gm"])
    assert r.status_code == 202, r.text


def test_teile_werden_nicht_im_ereignis_takt_geschrieben(client, world, tmp_path, monkeypatch):
    from app.routers import uploads

    orte = []
    echt = uploads._teil_schreiben

    def merken(*a, **k):
        try:
            asyncio.get_running_loop()
            orte.append("ereignis-takt")
        except RuntimeError:
            orte.append("faden")
        return echt(*a, **k)
    monkeypatch.setattr(uploads, "_teil_schreiben", merken)
    s = neue_session(client, world)
    hochladen(client, world["gm"], s["id"], audio_abschnitte(tmp_path, (5,)))
    assert orte and set(orte) == {"faden"}
