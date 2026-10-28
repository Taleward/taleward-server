"""Server 0.4.137: Testinstanz neben dem Server auf demselben VPS (deploy/test-instanz.sh) – eigene Adresse, eigene
Daten, Zahlungen dort nie mit echtem Geld."""
import json
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"


def _ordner(tmp_path, env_text):
    ziel = tmp_path / "taleward"
    ziel.mkdir()
    (ziel / ".env").write_text(env_text, encoding="utf-8")
    stub = tmp_path / "bin"
    stub.mkdir()
    for name, inhalt in {"docker": 'echo "$@" >> "$(dirname "$0")/aufrufe"\n', "chown": "exit 0\n"}.items():
        (stub / name).write_text("#!/bin/sh\n" + inhalt)
        (stub / name).chmod(0o755)
    return ziel, stub


def _aufruf(ziel, stub, *args):
    env = {**os.environ, "TALEWARD_ZIEL": str(ziel), "PATH": f"{stub}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(DEPLOY / "test-instanz.sh"), *args], capture_output=True, text=True, env=env)


def test_an_und_aus(tmp_path):
    ziel, stub = _ordner(tmp_path, "TALEWARD_DOMAIN=srv.example.org\nCOMPOSE_PROFILES=worker\n"
                                   "WEBSITE_DOMAINS=example.org www.example.org\n")
    r = _aufruf(ziel, stub, "an", "Test.Example.org")
    assert r.returncode == 0, r.stderr
    env = (ziel / ".env").read_text()
    assert "TALEWARD_TEST_DOMAIN=test.example.org" in env and "COMPOSE_PROFILES=worker,test" in env
    regel = (ziel / "test-caddy" / "test.caddy").read_text()
    assert regel.splitlines()[1] == "test.example.org {" and "reverse_proxy testserver:8000" in regel
    assert (ziel / "test-daten").is_dir()
    assert _aufruf(ziel, stub, "an", "test.example.org").returncode == 0  # zweimal: Profil nur einmal
    assert (ziel / ".env").read_text().count("test") == 2
    r = _aufruf(ziel, stub, "aus")
    assert r.returncode == 0 and not (ziel / "test-caddy" / "test.caddy").exists()
    env = (ziel / ".env").read_text()
    assert "COMPOSE_PROFILES=worker\n" in env and (ziel / "test-daten").is_dir()  # Daten bleiben
    assert "stop testserver" in (stub / "aufrufe").read_text()


def test_nur_aus_profil_test_wird_die_zeile_entfernt(tmp_path):
    ziel, stub = _ordner(tmp_path, "TALEWARD_DOMAIN=srv.example.org\n")
    assert _aufruf(ziel, stub, "an", "test.example.org").returncode == 0
    assert "COMPOSE_PROFILES=test" in (ziel / ".env").read_text()
    assert _aufruf(ziel, stub, "aus").returncode == 0
    assert "COMPOSE_PROFILES" not in (ziel / ".env").read_text()


@pytest.mark.parametrize("adresse", ["srv.example.org", "www.example.org", "", "test example.org", "a;b.org",
                                     "test.example.org\nX=1", "-x.example.org", "localhost"])
def test_abgelehnte_adressen(tmp_path, adresse):
    ziel, stub = _ordner(tmp_path, "TALEWARD_DOMAIN=srv.example.org\nWEBSITE_DOMAINS=example.org www.example.org\n")
    r = _aufruf(ziel, stub, "an", adresse)
    assert r.returncode != 0
    assert not (ziel / "test-caddy" / "test.caddy").exists() and "TEST_DOMAIN" not in (ziel / ".env").read_text()


def test_caddy_nimmt_die_regel_an(tmp_path):
    caddy = os.environ.get("CADDY") or shutil.which("caddy")
    if not caddy:
        pytest.skip("Caddy nicht vorhanden")
    ziel, stub = _ordner(tmp_path, "TALEWARD_DOMAIN=srv.example.org\n")
    assert _aufruf(ziel, stub, "an", "test.example.org").returncode == 0
    datei = tmp_path / "Caddyfile"
    datei.write_text((DEPLOY / "Caddyfile").read_text().replace("/etc/caddy/test/", f"{ziel}/test-caddy/"))
    env = {**os.environ, "ACME_EMAIL": "a@example.org", "TALEWARD_DOMAIN": "srv.example.org"}
    r = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", str(datei)], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    routen = json.loads(r.stdout)["apps"]["http"]["servers"]["srv0"]["routes"]
    hosts = [h for x in routen for m in x.get("match", []) for h in m.get("host", [])]
    assert "test.example.org" in hosts and "srv.example.org" in hosts


def test_compose_und_update_bringen_die_testinstanz_mit():
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert 'profiles: ["test"]' in compose and "- ./test-daten:/data" in compose
    assert 'TALEWARD_TESTINSTANZ: "1"' in compose and "- ./test-caddy:/etc/caddy/test:ro" in compose
    assert "import /etc/caddy/test/*.caddy" in (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "test-instanz.sh:test-instanz.sh" in (DEPLOY / "aktualisieren.sh").read_text(encoding="utf-8")
    assert subprocess.run(["bash", "-n", str(DEPLOY / "test-instanz.sh")]).returncode == 0


def test_testinstanz_nie_live(dbs, monkeypatch):
    from app import zahlung
    from app.config import get_settings
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "zahlung.live", "1")
    dbs.commit()
    assert zahlung.live(dbs)
    monkeypatch.setattr(get_settings(), "taleward_testinstanz", True)
    assert not zahlung.live(dbs)
    assert not zahlung.stripe_schluessel_erlaubt("sk_live_abc", dbs)


def test_verwaltung_zeigt_testinstanz(client, admin, monkeypatch):  # noqa: F811
    from app.config import get_settings

    assert "Testinstanz" not in client.get("/verwaltung/").text
    monkeypatch.setattr(get_settings(), "taleward_testinstanz", True)
    assert "· Testinstanz" in client.get("/verwaltung/").text


from tests.test_verwaltung import admin  # noqa: E402, F401
