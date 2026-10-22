"""Server 0.4.121/0.4.122: optional eine statische Website neben dem Server über denselben Caddy (Website-Ordner,
Neuladen ohne Neustart über einen eingeschränkten Nutzer) und Aufräumen alter Docker-Bilder. Die Prüfung der Regeln
selbst: tests/test_server_04124.py."""
import subprocess
from pathlib import Path

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"


def test_caddy_und_compose_binden_die_website_optional_ein():
    caddy = (DEPLOY / "Caddyfile").read_text(encoding="utf-8")
    assert "import /etc/caddy/website/*.caddy" in caddy  # ohne Dateien nur eine Warnung, Caddy läuft weiter
    compose = (DEPLOY / "docker-compose.yml").read_text(encoding="utf-8")
    assert "- ${WEBSITE_ORDNER:-./website}:/srv/website:ro" in compose
    assert "- ./website-caddy:/etc/caddy/website:ro" in compose
    neu = (DEPLOY / "website-neu-laden.sh").read_text(encoding="utf-8")
    assert "caddy reload --config /etc/caddy/Caddyfile" in neu
    assert 'cp -P -- "$f" "$NEU/"' in neu  # erst kopieren, dann die Kopie prüfen
    assert 'cp -a "$ALT"/. "$REGELN"/' in neu  # abgelehnt → alte Regeln zurück
    assert "Keine Caddy-Regeln gefunden" in neu  # ohne Regeldatei bleibt alles, wie es ist


def test_zugang_nur_hochladen_und_neu_laden():
    z = (DEPLOY / "website-zugang.sh").read_text(encoding="utf-8")
    assert 'command="%s",restrict %s' in z
    assert "neu-laden) exec sudo -n $NEULADEN ;;" in z
    assert 'echo "$NUTZER ALL=(root) NOPASSWD: $NEULADEN"' in z and "visudo -cqf" in z
    assert "passwd -l" in z and "NUTZER=${WEBSITE_NUTZER:-taleward-website}" in z
    for skript in ("website-zugang.sh", "website-neu-laden.sh"):
        assert subprocess.run(["bash", "-n", str(DEPLOY / skript)]).returncode == 0


def test_installation_und_update_bringen_die_skripte_mit():
    install = (WURZEL / "install.sh").read_text(encoding="utf-8")
    assert "for datei in website-zugang.sh website-neu-laden.sh website_regeln.py; do" in install
    akt = (DEPLOY / "aktualisieren.sh").read_text(encoding="utf-8")
    assert "website-zugang.sh:website-zugang.sh website-neu-laden.sh:website-neu-laden.sh" in akt
    assert 'docker image prune -af --filter "until=168h"' in akt
    # Bau-Zwischenstände nur ohne eingebauten Worker (sein KI-Paket bleibt im Zwischenspeicher)
    assert akt.index("COMPOSE_PROFILES=") < akt.index("docker builder prune")
