"""Server 0.4.126: kein Zugriffsprotokoll mit Adressen der Aufrufer (Server und Caddy); Zähler für Anmeldeversuche
vergessen Adressen nach Ablauf ihres Fensters."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent


def test_server_ohne_zugriffsprotokoll(monkeypatch):
    import uvicorn
    from typer.testing import CliRunner

    from app.cli import app as cli

    aufrufe = []
    monkeypatch.setattr(uvicorn, "run", lambda *a, **k: aufrufe.append(k))
    r = CliRunner().invoke(cli, ["serve", "--port", "8000"])
    assert r.exit_code == 0, r.output
    assert aufrufe and aufrufe[0]["access_log"] is False


FELDER = ["request>remote_ip", "request>client_ip", "request>remote_port", "request>headers", "request>uri"]


def test_caddy_meldungen_ohne_adressen(tmp_path):
    text = (WURZEL / "deploy" / "Caddyfile").read_text(encoding="utf-8")
    for feld in FELDER:
        assert f"{feld} delete" in text
    caddy = os.environ.get("CADDY") or shutil.which("caddy")
    if not caddy:
        pytest.skip("Caddy nicht vorhanden – nur der Text geprüft")
    env = {**os.environ, "ACME_EMAIL": "a@example.org", "TALEWARD_DOMAIN": "srv.example.org"}
    r = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", str(WURZEL / "deploy" / "Caddyfile")],
                       capture_output=True, text=True, env=env)
    assert r.returncode == 0, r.stderr
    encoder = json.loads(r.stdout)["logging"]["logs"]["default"]["encoder"]
    assert encoder["format"] == "filter"
    assert all(encoder["fields"][f] == {"filter": "delete"} for f in FELDER)


def test_zaehler_vergessen_abgelaufene_adressen(monkeypatch):
    from app import begrenzung

    z = begrenzung.Zaehler()
    jetzt = [1000.0]
    monkeypatch.setattr(begrenzung.time, "monotonic", lambda: jetzt[0])
    z.zaehlen("login-adr:198.51.100.7", 15 * 60)
    z.zaehlen("join-adr:198.51.100.8", 60 * 60)
    jetzt[0] += 16 * 60
    assert z.abgelaufene_vergessen() == 1
    assert list(z._daten) == ["join-adr:198.51.100.8"]
    jetzt[0] += 60 * 60
    assert z.abgelaufene_vergessen() == 1 and not z._daten


def test_datenschutz_vorlage_nennt_die_frist():
    text = (WURZEL / "app" / "datenschutz.py").read_text(encoding="utf-8")
    assert "kein Zugriffsprotokoll" in text and "höchstens eine Stunde im Arbeitsspeicher" in text


def test_update_startet_caddy_bei_neuer_caddyfile_neu():
    akt = (WURZEL / "deploy" / "aktualisieren.sh").read_text(encoding="utf-8")
    assert 'caddy_vorher=$(sha256sum Caddyfile' in akt and "docker compose restart caddy" in akt
    assert akt.index("caddy_vorher=") < akt.index('for paar in aktualisieren.sh') < akt.index("restart caddy")
