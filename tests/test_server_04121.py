"""Server 0.4.121: optional eine statische Website neben dem Server über denselben Caddy (Website-Ordner, geprüfte
Caddy-Regeln, Neuladen ohne Neustart über einen eingeschränkten Nutzer) und Aufräumen alter Docker-Bilder."""
import os
import shutil
import subprocess
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"

GUT = """example.org {
	root * /srv/website
	encode zstd gzip
	header {
		Content-Security-Policy "default-src 'self'; script-src 'sha256-AbC+/9=' ; img-src 'self' data:"
		Strict-Transport-Security "max-age=31536000"
		-Server
	}
	@intern path /_caddy/*
	respond @intern 404
	@app path /app /app/*
	redir @app https://app.example.org{uri} 301
	handle_errors {
		rewrite * /404.html
		file_server
	}
	file_server
}

www.example.org {
	redir https://example.org{uri} 301
}

app.example.org {
	root * /srv/website/app
	header /auth/app/* Cache-Control "no-store"
	try_files {path} /index.html
	file_server
}
"""

SCHLECHT = [
    "x.example.org {\n\treverse_proxy server:8000\n}\n",
    "x.example.org {\n\troot * /data\n\tfile_server\n}\n",
    "x.example.org {\n\troot * /srv/website/../../data\n\tfile_server\n}\n",
    "x.example.org {\n\tfile_server {\n\t\troot /etc\n\t}\n}\n",
    "x.example.org {\n\trespond \"{env.ACME_EMAIL}\"\n}\n",
    "x.example.org {\n\trespond \"{file./data/caddy/x}\"\n}\n",
    "x.example.org {\n\trespond {$TALEWARD_DOMAIN}\n}\n",
    "import /etc/caddy/Caddyfile\n",
    "x.example.org {\n\tlog {\n\t\toutput file /data/x.log\n\t}\n}\n",
    "x.example.org {\n\ttemplates\n\tfile_server\n}\n",
    "x.example.org {\n\ttls intern@example.org\n}\n",
]


def _pruefen(pfad: Path) -> subprocess.CompletedProcess:
    return subprocess.run(["bash", str(DEPLOY / "website-neu-laden.sh"), "--pruefen", str(pfad)],
                          capture_output=True, text=True, env={**os.environ, "LC_ALL": "C.UTF-8"})


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_regeln_der_website_werden_geprueft(tmp_path):
    gut = tmp_path / "gut.caddy"
    gut.write_text(GUT, encoding="utf-8")
    r = _pruefen(gut)
    assert r.returncode == 0, r.stderr
    for i, text in enumerate(SCHLECHT):
        f = tmp_path / f"schlecht{i}.caddy"
        f.write_text(text, encoding="utf-8")
        assert _pruefen(f).returncode != 0, text
    link = tmp_path / "link.caddy"
    link.symlink_to(gut)
    assert _pruefen(link).returncode != 0  # Verknüpfungen nie (könnten auf fremde Dateien zeigen)
    gross = tmp_path / "gross.caddy"
    gross.write_text("# " + "x" * 300_000, encoding="utf-8")
    assert _pruefen(gross).returncode != 0


def test_caddy_und_compose_binden_die_website_optional_ein():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "import /etc/caddy/website/*.caddy" in caddy  # ohne Dateien nur eine Warnung, Caddy läuft weiter
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- ${WEBSITE_ORDNER:-./website}:/srv/website:ro" in compose
    assert "- ./website-caddy:/etc/caddy/website:ro" in compose
    neu = (DEPLOY / "website-neu-laden.sh").read_text(encoding="utf-8")
    assert "WURZEL=/srv/website" in neu and "caddy reload --config /etc/caddy/Caddyfile" in neu
    assert 'cp -P -- "$f" "$NEU/"' in neu  # erst kopieren, dann die Kopie prüfen
    assert 'cp -a "$ALT"/. "$REGELN"/' in neu  # abgelehnt → alte Regeln zurück


def test_zugang_nur_hochladen_und_neu_laden():
    z = (DEPLOY / "website-zugang.sh").read_text(encoding="utf-8")
    assert 'command="%s",restrict %s' in z
    assert '"rsync --server "*) exec /usr/bin/rrsync -wo $ORDNER ;;' in z
    assert "neu-laden) exec sudo -n $NEULADEN ;;" in z
    assert 'echo "$NUTZER ALL=(root) NOPASSWD: $NEULADEN"' in z and "visudo -cqf" in z
    assert "passwd -l" in z
    for skript in ("website-zugang.sh", "website-neu-laden.sh"):
        assert subprocess.run(["bash", "-n", str(DEPLOY / skript)]).returncode == 0


def test_installation_und_update_bringen_die_skripte_mit():
    install = (WURZEL / "install.sh").read_text(encoding="utf-8")
    assert "for datei in website-zugang.sh website-neu-laden.sh; do" in install
    akt = (DEPLOY / "aktualisieren.sh").read_text(encoding="utf-8")
    assert "website-zugang.sh:website-zugang.sh website-neu-laden.sh:website-neu-laden.sh" in akt
    assert 'docker image prune -af --filter "until=168h"' in akt
    # Bau-Zwischenstände nur ohne eingebauten Worker (sein KI-Paket bleibt im Zwischenspeicher)
    assert akt.index("COMPOSE_PROFILES=") < akt.index("docker builder prune")
