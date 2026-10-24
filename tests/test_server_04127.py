"""Server 0.4.127: Antworten ohne Angaben zur Software (Server, Via); Hochladen der Website mit rrsync -munge."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent


def test_server_ohne_server_kopfzeile(monkeypatch):
    import uvicorn
    from typer.testing import CliRunner

    from app.cli import app as cli

    aufrufe = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: aufrufe.append(k))
    assert CliRunner().invoke(cli, ["serve", "--port", "8000"]).exit_code == 0
    assert aufrufe[0]["server_header"] is False


def test_caddy_entfernt_server_und_via():
    caddy = os.environ.get("CADDY") or shutil.which("caddy")
    if not caddy:
        pytest.skip("Caddy nicht vorhanden")
    env = {**os.environ, "ACME_EMAIL": "a@example.org", "TALEWARD_DOMAIN": "srv.example.org"}
    r = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", str(WURZEL / "deploy" / "Caddyfile")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    route = json.loads(r.stdout)["apps"]["http"]["servers"]["srv0"]["routes"][0]
    assert route["match"] == [{"host": ["srv.example.org"]}]
    kopf = [h for x in route["handle"][0]["routes"] for h in x["handle"] if h["handler"] == "headers"]
    assert kopf and kopf[0]["response"]["deferred"] is True
    assert sorted(kopf[0]["response"]["delete"]) == ["Server", "Via"]


def test_empfang_mit_munge():
    z = (WURZEL / "deploy" / "website-zugang.sh").read_text(encoding="utf-8")
    assert "rrsync -wo -munge $ORDNER" in z and "-munge-links" not in z


def test_hoechstens_zwei_pdf_prozesse_zugleich(monkeypatch):
    """S50: Ist kein Platz frei, kommt nach kurzer Wartezeit „gleich noch einmal“ (429); Normalfall unverändert."""
    import threading

    from app import errors, unterlagen
    from tests.dateien import pdf

    platz = threading.BoundedSemaphore(2)
    monkeypatch.setattr(unterlagen, "PDF_GLEICHZEITIG", platz)
    monkeypatch.setattr(unterlagen, "PDF_WARTEN", 0.2)
    assert platz.acquire() and platz.acquire()  # zwei Leseprozesse laufen schon
    with pytest.raises(errors.ApiError) as e:
        unterlagen.auslesen("pdf", pdf(["Text"]))
    assert e.value.status == 429 and e.value.code == "too_many_requests"
    platz.release()
    assert unterlagen.auslesen("pdf", pdf(["Der Wirt heisst Borin."])).abschnitte  # ein Platz frei: geht wieder
    assert platz.acquire(timeout=0) and not platz.acquire(timeout=0)  # Platz wurde zurückgegeben


def test_pdf_fehler_geben_den_platz_zurueck(monkeypatch):
    import threading

    from app import errors, unterlagen

    platz = threading.BoundedSemaphore(1)
    monkeypatch.setattr(unterlagen, "PDF_GLEICHZEITIG", platz)
    for _ in range(3):
        with pytest.raises(errors.ApiError):
            unterlagen.auslesen("pdf", b"%PDF-kaputt")
    assert platz.acquire(timeout=0)


@pytest.mark.skipif(shutil.which("bash") is None or not hasattr(os, "mkfifo"), reason="bash/mkfifo fehlt")
def test_neu_laden_lehnt_pipe_ab(tmp_path):
    """Keine normale Datei unter _caddy (z. B. eine Pipe) → Abbruch statt Hängen."""
    from tests.test_server_04124 import _einrichten

    ziel, web, env = _einrichten(tmp_path)
    os.mkfifo(web / "_caddy" / "z.caddy")
    r = subprocess.run(["bash", str(WURZEL / "deploy" / "website-neu-laden.sh")], capture_output=True, text=True,
                       env=env, timeout=20)
    assert r.returncode != 0 and "keine normale Datei" in r.stderr
    assert not (ziel / "website-caddy").exists() or not list((ziel / "website-caddy").glob("*.caddy"))
