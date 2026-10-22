#!/bin/bash
# Optional: statische Website neben dem Server (docs/BETRIEB.md, „Website mitliefern“).
# Übernimmt die Caddy-Regeln der Website aus <WEBSITE_ORDNER>/_caddy/*.caddy und lädt Caddy neu – ohne Neustart.
# Aufgerufen per sudo vom Nutzer, der die Website hochlädt (eingerichtet von website-zugang.sh), ohne Argumente.
# Die Regeln dürfen nur Dateien aus /srv/website ausliefern (Prüfung unten). Lehnt Caddy sie ab, bleiben die alten
# Regeln auf der Platte und in Betrieb. Test der Prüfung allein:  website-neu-laden.sh --pruefen DATEI
set -euo pipefail
ZIEL=${TALEWARD_ZIEL:-/opt/taleward}
WURZEL=/srv/website              # so sieht Caddy den Website-Ordner (docker-compose.yml)
MAX_BYTES=262144

regeln_pruefen() {
  local datei=$1
  if [ ! -f "$datei" ] || [ -L "$datei" ] || [ "$(wc -c <"$datei")" -gt "$MAX_BYTES" ]; then
    echo "Abgelehnt: $(basename "$datei") ist keine normale Datei oder zu groß." >&2
    return 1
  fi
  # Nichts, was über statisches Ausliefern hinausgeht: keine weiteren Dateien einbinden, nichts weiterleiten,
  # keine Platzhalter für Umgebung oder Dateien, keine Protokolle, Zertifikate oder Verwaltung, kein „..“
  if grep -nEi '(^|[[:space:]])(import|reverse_proxy|php_fastcgi|forward_auth|fs|storage|admin|acme_ca|acme_dns|tls|log|templates|invoke|metrics|tracing)([[:space:]]|\{|$)|\{(env|file|system)\.|\{\$|\.\.' "$datei" >&2; then
    echo "Abgelehnt: $(basename "$datei") enthält nicht erlaubte Angaben (Zeilen oben)." >&2
    return 1
  fi
  # „root“ nur innerhalb des Website-Ordners
  if grep -nE '^[[:space:]]*root([[:space:]]|$)' "$datei" \
     | grep -vE ":[[:space:]]*root([[:space:]]+(\*|@[A-Za-z0-9_-]+|/[^[:space:]]*))?[[:space:]]+$WURZEL(/[^[:space:]]*)?[[:space:]]*$" >&2; then
    echo "Abgelehnt: „root“ muss in $WURZEL liegen (Zeilen oben)." >&2
    return 1
  fi
}

if [ "${1:-}" = "--pruefen" ]; then
  regeln_pruefen "${2:?Datei fehlt}"
  exit
fi
[ "$#" -eq 0 ] || { echo "Ohne Argumente aufrufen." >&2; exit 2; }

ORDNER=$(sed -n 's/^WEBSITE_ORDNER=//p' "$ZIEL/.env" | tail -1)
if [ -z "$ORDNER" ] || [ ! -d "$ORDNER" ] || [ -L "$ORDNER/_caddy" ]; then
  echo "Kein Website-Ordner eingerichtet (WEBSITE_ORDNER in $ZIEL/.env)." >&2
  exit 1
fi
REGELN="$ZIEL/website-caddy"
mkdir -p "$REGELN"
NEU=$(mktemp -d) ALT=$(mktemp -d)
trap 'rm -rf "$NEU" "$ALT"' EXIT
shopt -s nullglob
# Erst kopieren (Verknüpfungen bleiben Verknüpfungen), dann die Kopie prüfen – was geprüft ist, wird auch übernommen
for f in "$ORDNER"/_caddy/*.caddy; do
  cp -P -- "$f" "$NEU/"
done
for f in "$NEU"/*; do
  regeln_pruefen "$f"
done
cp -a "$REGELN"/. "$ALT"/
rm -f "$REGELN"/*.caddy
for f in "$NEU"/*.caddy; do install -m 644 "$f" "$REGELN/"; done
cd "$ZIEL"
if docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
  echo "Caddy neu geladen."
  exit 0
fi
rm -f "$REGELN"/*.caddy
cp -a "$ALT"/. "$REGELN"/
echo "Caddy hat die neuen Regeln abgelehnt – die bisherigen bleiben in Betrieb." >&2
exit 1
