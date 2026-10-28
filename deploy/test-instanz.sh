#!/bin/bash
# Optional: eine Testinstanz neben dem Server auf demselben VPS (docs/BETRIEB.md, „Testinstanz“).
# Eigene Adresse, eigene Daten (/opt/taleward/test-daten), dasselbe Programm wie der Server. Zahlungen laufen dort
# immer nur im Testmodus der Anbieter. Gedacht zum Ausprobieren, etwa des Aufladens mit Test-Zugängen.
#
#   sudo bash /opt/taleward/test-instanz.sh an test.example.org   # einschalten (Adresse muss auf diesen Server zeigen)
#   sudo bash /opt/taleward/test-instanz.sh aus                   # ausschalten, Daten bleiben in test-daten
#   sudo bash /opt/taleward/test-instanz.sh code                  # Einrichtungscode der Testinstanz anzeigen
# shellcheck disable=SC1111  # deutsche Anführungszeichen sind Absicht
set -euo pipefail
ZIEL=${TALEWARD_ZIEL:-/opt/taleward}
ENV="$ZIEL/.env"
REGELN="$ZIEL/test-caddy"
cd "$ZIEL"

wert() { sed -n "s/^$1=//p" "$ENV" | tail -1; }
setzen() {  # Name, Wert – vorhandene Zeile ersetzen oder anhängen
  if grep -q "^$1=" "$ENV"; then
    sed -i "s|^$1=.*|$1=$2|" "$ENV"
  else
    printf '%s=%s\n' "$1" "$2" >>"$ENV"
  fi
}
profile_ohne_test() { wert COMPOSE_PROFILES | tr ',' '\n' | grep -vx 'test' | grep -v '^$' | paste -sd, - || true; }

[ -f "$ENV" ] || { echo "Keine Einstellungen gefunden ($ENV) – ist Taleward hier installiert?" >&2; exit 1; }

case "${1:-}" in
  an)
    adresse=$(printf '%s' "${2:-}" | tr '[:upper:]' '[:lower:]')
    if ! [[ "$adresse" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]]; then
      echo "Bitte eine Adresse angeben, z. B.: sudo bash $0 an test.example.org" >&2
      exit 2
    fi
    if [ "$adresse" = "$(wert TALEWARD_DOMAIN)" ]; then
      echo "Abgelehnt: Das ist die Adresse des Servers selbst." >&2
      exit 1
    fi
    for w in $(wert WEBSITE_DOMAINS); do
      if [ "$adresse" = "$w" ]; then
        echo "Abgelehnt: Die Adresse gehört zur Website." >&2
        exit 1
      fi
    done
    mkdir -p test-daten "$REGELN"
    chown 1000:1000 test-daten && chmod 700 test-daten
    setzen TALEWARD_TEST_DOMAIN "$adresse"
    profile=$(profile_ohne_test)
    setzen COMPOSE_PROFILES "${profile:+$profile,}test"
    cat >"$REGELN/test.caddy" <<EOF
# Von test-instanz.sh angelegt. Ausschalten: sudo bash $ZIEL/test-instanz.sh aus
$adresse {
	encode zstd gzip
	header {
		-Server
		-Via
		X-Robots-Tag "noindex, nofollow"
		defer
	}
	request_body {
		max_size 64MB
	}
	reverse_proxy testserver:8000
}
EOF
    docker compose up -d
    docker compose restart caddy
    echo
    echo "Testinstanz läuft unter https://$adresse (das Zertifikat holt Caddy in der ersten Minute)."
    echo "Einrichtungscode: sudo bash $0 code"
    ;;
  aus)
    profile=$(profile_ohne_test)
    if [ -n "$profile" ]; then setzen COMPOSE_PROFILES "$profile"; else sed -i '/^COMPOSE_PROFILES=/d' "$ENV"; fi
    rm -f "$REGELN/test.caddy"
    docker compose stop testserver >/dev/null 2>&1 || true
    docker compose rm -f testserver >/dev/null 2>&1 || true
    docker compose restart caddy
    echo "Testinstanz ist aus. Ihre Daten liegen weiter in $ZIEL/test-daten (löschen: sudo rm -rf $ZIEL/test-daten)."
    ;;
  code)
    docker compose exec testserver chronik einrichtungscode
    ;;
  *)
    echo "Aufruf: sudo bash $0 an <adresse> | aus | code" >&2
    exit 2
    ;;
esac
