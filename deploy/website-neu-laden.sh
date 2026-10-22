#!/bin/bash
# Optional: statische Website neben dem Server (docs/BETRIEB.md, „Website mitliefern“).
# Übernimmt die Caddy-Regeln der Website aus <WEBSITE_ORDNER>/_caddy/*.caddy und lädt Caddy neu – ohne Neustart.
# Aufgerufen per sudo vom Nutzer, der die Website hochlädt (eingerichtet von website-zugang.sh), ohne Argumente.
# Geprüft wird, was Caddy aus den Regeln macht (website_regeln.py): nur erlaubte Bausteine, nur die Adressen aus
# WEBSITE_DOMAINS (nie die des Servers), Dateien nur aus /srv/website. Verknüpfungen im Website-Ordner → Abbruch.
# Lehnt Caddy die Regeln ab, bleiben die alten auf der Platte und in Betrieb.
set -euo pipefail
export LC_ALL=C   # gleiche Reihenfolge der Dateien wie beim Einbinden durch Caddy
ZIEL=${TALEWARD_ZIEL:-/opt/taleward}
PRUEFER="$ZIEL/website_regeln.py"
MAX_BYTES=262144

[ "$#" -eq 0 ] || { echo "Ohne Argumente aufrufen." >&2; exit 2; }
wert() { sed -n "s/^$1=//p" "$ZIEL/.env" | tail -1; }
ORDNER=$(wert WEBSITE_ORDNER)
SERVER=$(wert TALEWARD_DOMAIN)
read -r -a DOMAINS <<<"$(wert WEBSITE_DOMAINS)"
if [ -z "$ORDNER" ] || [ ! -d "$ORDNER" ] || [ -L "$ORDNER" ] || [ -L "$ORDNER/_caddy" ]; then
  echo "Kein Website-Ordner eingerichtet (WEBSITE_ORDNER in $ZIEL/.env)." >&2
  exit 1
fi
[ -f "$PRUEFER" ] || { echo "Prüfung fehlt ($PRUEFER) – erst den Server aktualisieren." >&2; exit 1; }
python3 -I "$PRUEFER" adressen "$SERVER" "${DOMAINS[@]}" >/dev/null || {
  echo "Adressen der Website fehlen oder sind ungültig (WEBSITE_DOMAINS in $ZIEL/.env, website-zugang.sh)." >&2
  exit 1
}
# Verknüpfungen im Website-Ordner könnten auf Dateien außerhalb zeigen (Caddy folgt ihnen) – dann gar nichts übernehmen
if [ -n "$(find "$ORDNER" -type l -print -quit)" ]; then
  echo "Abgelehnt: Im Website-Ordner liegen Verknüpfungen – nichts übernommen." >&2
  exit 1
fi

REGELN="$ZIEL/website-caddy"
mkdir -p "$REGELN"
NEU=$(mktemp -d) ALT=$(mktemp -d)
trap 'rm -rf "$NEU" "$ALT"; rm -f "$REGELN/pruefen.txt"' EXIT
shopt -s nullglob
# Erst kopieren (Verknüpfungen bleiben Verknüpfungen), dann die Kopie prüfen – was geprüft ist, wird auch übernommen
for f in "$ORDNER"/_caddy/*.caddy; do
  cp -P -- "$f" "$NEU/"
done
NEUE=("$NEU"/*)
if [ "${#NEUE[@]}" -eq 0 ]; then
  echo "Keine Caddy-Regeln gefunden ($ORDNER/_caddy/*.caddy) – nichts geändert." >&2
  exit 1
fi
cd "$ZIEL"
: >"$NEU/alle.txt"
for f in "${NEUE[@]}"; do
  name=$(basename "$f")
  if [[ ! "$name" =~ ^[A-Za-z0-9_-]+\.caddy$ ]] || [ ! -f "$f" ] || [ -L "$f" ] \
     || [ "$(wc -c <"$f")" -gt "$MAX_BYTES" ]; then
    echo "Abgelehnt: $name ist keine normale Datei, hat einen ungewöhnlichen Namen oder ist zu groß." >&2
    exit 1
  fi
  python3 -I "$PRUEFER" vorab "$f" || { echo "($name)" >&2; exit 1; }
  cat -- "$f" >>"$NEU/alle.txt"
  printf '\n' >>"$NEU/alle.txt"
done
# Was macht Caddy daraus? Alle Dateien zusammen – so, wie das Caddyfile sie einbindet – im Caddy-Gerät übersetzen
# (gleiche Fassung wie im Betrieb), dann das Ergebnis prüfen
install -m 644 "$NEU/alle.txt" "$REGELN/pruefen.txt"
if ! docker compose exec -T caddy caddy adapt --adapter caddyfile --config /etc/caddy/website/pruefen.txt \
     >"$NEU/regeln.json" 2>"$NEU/regeln.fehler"; then
  echo "Abgelehnt: Caddy versteht die Regeln nicht:" >&2
  tail -n 3 "$NEU/regeln.fehler" >&2
  exit 1
fi
python3 -I "$PRUEFER" json "$NEU/regeln.json" "$SERVER" "${DOMAINS[@]}" || exit 1
rm -f "$REGELN/pruefen.txt"

cp -a "$REGELN"/. "$ALT"/
rm -f "$REGELN"/*.caddy
for f in "${NEUE[@]}"; do install -m 644 "$f" "$REGELN/"; done
if docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile; then
  echo "Caddy neu geladen."
  exit 0
fi
rm -f "$REGELN"/*.caddy
cp -a "$ALT"/. "$REGELN"/
echo "Caddy hat die neuen Regeln abgelehnt – die bisherigen bleiben in Betrieb." >&2
exit 1
