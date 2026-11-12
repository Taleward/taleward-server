"""Server 0.4.164: Platz für eine Zusatz-App neben dem Server (deploy/zusatz-app.sh, Netz taleward-zusatz)."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path

import pytest
import yaml

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"
ENV = ("TALEWARD_DOMAIN=srv.example.org\nTALEWARD_TEST_DOMAIN=test.example.org\n"
       "WEBSITE_DOMAINS=example.org www.example.org\n")


def _ordner(tmp_path, pruefung_ok=True):
    ziel = tmp_path / "taleward"
    ziel.mkdir()
    (ziel / ".env").write_text(ENV, encoding="utf-8")
    stub = tmp_path / "bin"
    stub.mkdir()
    # docker-Attrappe: merkt sich Aufrufe; „run … caddy validate“ scheitert, wenn pruefung.txt „nein“ sagt oder eine
    # Regel „kaputt“ enthält; „ps“ meldet Caddy als laufend
    (stub / "docker").write_text(f"""#!/bin/sh
echo "$@" >> "{tmp_path}/aufrufe"
case "$*" in
  *"run "*validate*)
    [ "$(cat "{tmp_path}/pruefung.txt")" = ja ] || exit 1
    if grep -l kaputt extra-caddy/*.caddy >/dev/null 2>&1; then exit 1; fi
    exit 0 ;;
  *"ps "*) echo caddy ;;
esac
exit 0
""")
    (stub / "docker").chmod(0o755)
    (tmp_path / "pruefung.txt").write_text("ja" if pruefung_ok else "nein")
    return ziel, stub


def _aufruf(ziel, stub, *args):
    env = {**os.environ, "TALEWARD_ZIEL": str(ziel), "PATH": f"{stub}:{os.environ['PATH']}"}
    return subprocess.run(["bash", str(DEPLOY / "zusatz-app.sh"), *args], capture_output=True, text=True, env=env)


def test_an_liste_aus(tmp_path):
    ziel, stub = _ordner(tmp_path)
    r = _aufruf(ziel, stub, "an", "App.Example.org", "meine-app:8080")
    assert r.returncode == 0, r.stderr
    regel = (ziel / "extra-caddy" / "app.example.org.caddy").read_text()
    zeilen = [z.strip() for z in regel.splitlines() if z.strip() and not z.startswith("#")]
    assert zeilen == ["app.example.org {", "encode zstd gzip", "header {", "-Server", "-Via",
                      'Strict-Transport-Security "max-age=31536000"', 'X-Content-Type-Options "nosniff"',
                      'Referrer-Policy "strict-origin-when-cross-origin"', "defer", "}", "request_body {",
                      "max_size 16MB", "}", "reverse_proxy meine-app:8080", "}"]
    for verboten in ("import", "root", "file_server", "log", "{$"):
        assert verboten not in regel.split("\n", 1)[1]
    aufrufe = (tmp_path / "aufrufe").read_text()
    assert "validate" in aufrufe and "caddy reload" in aufrufe
    assert "app.example.org → meine-app:8080" in _aufruf(ziel, stub, "liste").stdout
    # Ziel ändern ersetzt die Regel
    assert _aufruf(ziel, stub, "an", "app.example.org", "andere-app:3000").returncode == 0
    assert "reverse_proxy andere-app:3000" in (ziel / "extra-caddy" / "app.example.org.caddy").read_text()
    r = _aufruf(ziel, stub, "aus", "app.example.org")
    assert r.returncode == 0 and not (ziel / "extra-caddy" / "app.example.org.caddy").exists()
    assert _aufruf(ziel, stub, "aus", "app.example.org").returncode != 0


@pytest.mark.parametrize("adresse", ["srv.example.org", "SRV.example.org", "test.example.org", "www.example.org",
                                     "example.org", "*.example.org", "", "a b.example.org", "a;b.org", "localhost",
                                     "app.example.org\nX=1", "-x.example.org", "app.example.org:443", "http://a.org"])
def test_abgelehnte_adressen(tmp_path, adresse):
    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", adresse, "meine-app:8080").returncode != 0
    assert not list((ziel / "extra-caddy").glob("*.caddy"))


@pytest.mark.parametrize("ziel_", ["server:8000", "testserver:8000", "caddy:80", "worker:1", "ollama:11434",
                                   "taleward-server-1:8000", "taleward_server_1:8000", "meine-app", "meine-app:",
                                   "meine-app:0", "meine-app:70000", "meine-app:080", "Meine-App:8080", "a;b:80",
                                   "meine app:80", "-x:80", "meine-app:80:90", "http://meine-app:80", ""])
def test_abgelehnte_ziele(tmp_path, ziel_):
    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", ziel_).returncode != 0
    assert not list((ziel / "extra-caddy").glob("*.caddy"))


def test_hoechstens_fuenf(tmp_path):
    ziel, stub = _ordner(tmp_path)
    for i in range(5):
        assert _aufruf(ziel, stub, "an", f"app{i}.example.org", f"app{i}:80").returncode == 0
    assert _aufruf(ziel, stub, "an", "app5.example.org", "app5:80").returncode != 0
    assert _aufruf(ziel, stub, "an", "app0.example.org", "neu:81").returncode == 0  # ändern geht weiter


def test_abgelehnte_pruefung_laesst_alles_wie_es_war(tmp_path):
    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", "meine-app:8080").returncode == 0
    vorher = (ziel / "extra-caddy" / "app.example.org.caddy").read_text()
    (tmp_path / "pruefung.txt").write_text("nein")
    assert _aufruf(ziel, stub, "an", "app.example.org", "andere:9000").returncode != 0
    assert (ziel / "extra-caddy" / "app.example.org.caddy").read_text() == vorher
    assert _aufruf(ziel, stub, "an", "neu.example.org", "neu:9000").returncode != 0
    assert not (ziel / "extra-caddy" / "neu.example.org.caddy").exists()
    assert "caddy reload" not in (tmp_path / "aufrufe").read_text().split("validate")[-1]


# ---------------------------------------------------------------- Compose und Caddyfile
def test_netz_nur_mit_caddy():
    compose = yaml.safe_load((DEPLOY / "docker-compose.yml").read_text())
    assert compose["networks"]["zusatz"] == {"name": "taleward-zusatz"}  # nicht external: up scheitert nie daran
    for name, dienst in compose["services"].items():
        netze = dienst.get("networks") or {}
        if name == "caddy":
            assert set(netze) == {"default", "zusatz"}
            assert "./extra-caddy:/etc/caddy/extra:ro" in dienst["volumes"]
        else:
            assert "zusatz" not in netze, name
    assert "import /etc/caddy/extra/*.caddy" in (DEPLOY / "Caddyfile").read_text()


def test_update_und_installation_lassen_die_regeln_stehen():
    akt = (DEPLOY / "aktualisieren.sh").read_text()
    assert "zusatz-app.sh:zusatz-app.sh" in akt and "mkdir -p website-caddy test-caddy extra-caddy" in akt
    assert not re.search(r"rm [^\n]*extra-caddy", akt) and "extra-caddy:" not in akt  # nie überschrieben
    assert "zusatz_absichern\n  # Platz sparen" in akt
    inst = (WURZEL / "install.sh").read_text()
    assert "zusatz-app.sh; do" in inst and '"$ZIEL/extra-caddy"' in inst


def _absichern(tmp_path, regeln):
    """Die Funktionen caddy_ok/zusatz_absichern aus aktualisieren.sh allein ausführen."""
    ziel, stub = _ordner(tmp_path)
    akt = (DEPLOY / "aktualisieren.sh").read_text()
    funktionen = akt[akt.index("caddy_ok() {"):akt.index("wert_setzen() {")]
    (ziel / "extra-caddy").mkdir()
    for name, inhalt in regeln.items():
        (ziel / "extra-caddy" / name).write_text(inhalt)
    env = {**os.environ, "PATH": f"{stub}:{os.environ['PATH']}"}
    r = subprocess.run(["bash", "-c", funktionen + "\nzusatz_absichern"], cwd=ziel, capture_output=True, text=True,
                       env=env)
    return ziel, r


def test_kaputte_regel_legt_taleward_nicht_lahm(tmp_path):
    ziel, r = _absichern(tmp_path, {"gut.example.org.caddy": "gut", "kaputt.example.org.caddy": "kaputt"})
    assert r.returncode == 0
    assert (ziel / "extra-caddy" / "gut.example.org.caddy").exists()
    assert (ziel / "extra-caddy" / "aus" / "kaputt.example.org.caddy").exists()
    assert not (ziel / "extra-caddy" / "kaputt.example.org.caddy").exists()
    assert "kaputt.example.org abgeschaltet" in r.stdout
    assert "restart caddy" in (tmp_path / "aufrufe").read_text()


def test_ohne_regeln_oder_wenn_alles_passt_nichts_anfassen(tmp_path):
    ziel, r = _absichern(tmp_path, {"gut.example.org.caddy": "gut"})
    assert r.returncode == 0 and (ziel / "extra-caddy" / "gut.example.org.caddy").exists()
    assert "restart" not in (tmp_path / "aufrufe").read_text()
    (tmp_path / "aufrufe").unlink()
    shutil.rmtree(tmp_path / "taleward")
    shutil.rmtree(tmp_path / "bin")
    ziel, r = _absichern(tmp_path, {})
    assert r.returncode == 0 and not (tmp_path / "aufrufe").exists()  # ohne Regeln nicht einmal prüfen


def test_caddy_nimmt_die_regel_an(tmp_path):
    caddy = os.environ.get("CADDY") or shutil.which("caddy")
    if not caddy:
        pytest.skip("Caddy nicht vorhanden")
    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", "meine-app:8080").returncode == 0
    datei = tmp_path / "Caddyfile"
    datei.write_text((DEPLOY / "Caddyfile").read_text().replace("/etc/caddy/extra/", f"{ziel}/extra-caddy/"))
    env = {**os.environ, "ACME_EMAIL": "a@example.org", "TALEWARD_DOMAIN": "srv.example.org"}
    r = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", str(datei)], capture_output=True,
                       text=True, env=env)
    assert r.returncode == 0, r.stderr
    assert "meine-app:8080" in json.dumps(json.loads(r.stdout))
