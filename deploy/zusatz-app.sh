#!/bin/bash
# Optional: eine eigene Zusatz-App neben dem Server auf demselben VPS (docs/BETRIEB.md, „Zusatz-App neben dem Server“).
# Der Caddy von Taleward leitet eine eigene Adresse an einen Container weiter, der im Docker-Netz „taleward-zusatz“
# hängt. In diesem Netz ist von Taleward nur Caddy – Server, Testinstanz und Worker erreicht die Zusatz-App nie.
# Die Regel entsteht immer aus derselben festen Vorlage; eigener Caddy-Text ist nicht vorgesehen.
#
#   sudo bash /opt/taleward/zusatz-app.sh an app.example.org meine-app:8080   # einschalten bzw. Ziel ändern
#   sudo bash /opt/taleward/zusatz-app.sh aus app.example.org                 # ausschalten
#   sudo bash /opt/taleward/zusatz-app.sh liste                               # was ist eingerichtet
# shellcheck disable=SC1111  # deutsche Anführungszeichen sind Absicht
set -euo pipefail
ZIEL=${TALEWARD_ZIEL:-/opt/taleward}
ENV="$ZIEL/.env"
REGELN="$ZIEL/extra-caddy"
HOECHSTENS=5
# Namen, die zu Taleward gehören – dorthin leitet keine Zusatz-Regel
VERBOTEN="server testserver caddy worker worker-cpu ollama ollama-cpu localhost"
cd "$ZIEL"

wert() { sed -n "s/^$1=//p" "$ENV" | tail -1; }
# S69: Wer hängt im Netz der Zusatz-Apps? Je Container eine Zeile
# „<kennung> <name> ,<netze>, ,<namen im Zusatz-Netz>,“
zusatz_container() {
  local ids kennung name rest feld netze namen
  ids=$(docker network inspect taleward-zusatz -f '{{range $id, $c := .Containers}}{{$id}} {{end}}' 2>/dev/null) \
    || return 0
  [ -n "${ids// /}" ] || return 0
  # shellcheck disable=SC2086,SC2016  # mehrere Kennungen; $n/$v sind Teil der Docker-Vorlage
  docker inspect -f '{{.Id}} {{slice .Name 1}}{{range $n, $v := .NetworkSettings.Networks}} {{$n}}={{range $v.Aliases}}{{.}},{{end}}{{end}}' \
    $ids 2>/dev/null | while read -r kennung name rest; do
    netze=","; namen=",$name,"
    for feld in $rest; do
      netze="$netze${feld%%=*},"
      [ "${feld%%=*}" = taleward-zusatz ] && namen="$namen${feld#*=}"
    done
    echo "$kennung $name $netze $namen"
  done
}
projekt() {  # Name des Compose-Projekts von Taleward (Netze heißen <projekt>_default und <projekt>_kern)
  local p
  p=$(sed -n 's/^COMPOSE_PROJECT_NAME=//p' .env 2>/dev/null | tail -1)
  p=${p:-$(basename "$PWD")}
  printf '%s' "$p" | tr '[:upper:]' '[:lower:]'
}
# Namen von Taleward-Diensten, die im Netz der Zusatz-Apps niemand außer Caddy tragen darf
TALEWARD_NAMEN='^(server|testserver|caddy|localhost|worker.*|ollama.*|taleward[-_.].*)$'
fremd_belegt() {  # gibt belegte Namen aus (leer = alles in Ordnung)
  local p caddy kennung name netze namen n
  p=$(projekt)
  # S69.1: Caddy von Taleward nur an seiner Kennung erkennen, nie am Namen; läuft er nicht, wird niemand ausgenommen
  caddy=$(docker compose ps -q caddy 2>/dev/null | head -n 1)
  zusatz_container | while read -r kennung name netze namen; do
    if [ -n "$caddy" ] && [ "${kennung#"$caddy"}" != "$kennung" ]; then continue; fi
    case "$name" in "$p"-*|"$p"_*) echo "$name"; continue ;; esac  # Projektnamen gehören nur Taleward
    for n in ${namen//,/ }; do
      if [[ "$n" =~ $TALEWARD_NAMEN ]]; then echo "$n ($name)"; fi
    done
  done
}
# Testinstanz-Regel aus Fassungen vor 0.4.166 (Weiterleitung per Name) auf die feste Adresse umstellen
test_regel_auffrischen() {
  local regel=test-caddy/test.caddy
  # shellcheck disable=SC2016  # {$…} ist ein Caddy-Platzhalter, keine Shell-Variable
  if [ -f "$regel" ] && grep -q '^	reverse_proxy testserver:8000$' "$regel"; then
    sed -i 's/^	reverse_proxy testserver:8000$/	reverse_proxy {$TALEWARD_TEST_ZIEL:testserver:8000}/' "$regel"
    return 0
  fi
  return 1
}
alle_parken() {  # alle Zusatz-Regeln nach extra-caddy/aus/ – Taleward läuft weiter
  local regel
  mkdir -p "$REGELN/aus"
  for regel in "$REGELN"/*.caddy; do
    [ -f "$regel" ] && mv -f "$regel" "$REGELN/aus/"
  done
  neu_laden || true
}
netz_pruefen() {  # bricht ab, wenn im Netz der Zusatz-Apps jemand einen Taleward-Namen trägt (zweite Linie zu S69)
  local belegt
  belegt=$(fremd_belegt)
  [ -z "$belegt" ] && return 0
  alle_parken
  echo "ACHTUNG: Im Netz „taleward-zusatz“ trägt ein Container den Namen eines Taleward-Dienstes:" >&2
  printf '  %s\n' "$belegt" >&2
  echo "Alle Zusatz-Apps sind deshalb abgeschaltet (extra-caddy/aus/). Taleward läuft weiter." >&2
  echo "Den Dienst bzw. Alias der Zusatz-App umbenennen, dann die Zusatz-App neu einschalten." >&2
  exit 1
}
caddy_ok() {  # Gesamte Caddy-Konfiguration samt aller Zusatz-Regeln prüfen, ohne den laufenden Caddy anzufassen
  docker compose run --rm --no-deps -T caddy caddy validate --config /etc/caddy/Caddyfile --adapter caddyfile \
    >/dev/null 2>&1
}
neu_laden() {  # Laufenden Caddy neu laden; schlägt das fehl, behält Caddy die alte Konfiguration
  if docker compose ps --status running --services 2>/dev/null | grep -qx caddy; then
    docker compose exec -T caddy caddy reload --config /etc/caddy/Caddyfile --adapter caddyfile >/dev/null 2>&1
  else
    docker compose up -d caddy >/dev/null 2>&1
  fi
}
adresse_pruefen() {  # gibt die Adresse in Kleinbuchstaben aus oder bricht ab
  local adresse
  adresse=$(printf '%s' "${1:-}" | tr '[:upper:]' '[:lower:]')
  if ! [[ "$adresse" =~ ^([a-z0-9]([a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$ ]] || [ ${#adresse} -gt 253 ]; then
    echo "Bitte eine Adresse angeben, z. B.: sudo bash $0 an app.example.org meine-app:8080" >&2
    exit 2
  fi
  echo "$adresse"
}

[ -f "$ENV" ] || { echo "Keine Einstellungen gefunden ($ENV) – ist Taleward hier installiert?" >&2; exit 1; }
mkdir -p "$REGELN"

case "${1:-}" in
  an)
    adresse=$(adresse_pruefen "${2:-}")
    for besetzt in "$(wert TALEWARD_DOMAIN)" "$(wert TALEWARD_TEST_DOMAIN)" $(wert WEBSITE_DOMAINS); do
      if [ -n "$besetzt" ] && [ "$adresse" = "$(printf '%s' "$besetzt" | tr '[:upper:]' '[:lower:]')" ]; then
        echo "Abgelehnt: Die Adresse gehört schon zu Taleward (Server, Testinstanz oder Website)." >&2
        exit 1
      fi
    done
    ziel=${3:-}
    container=${ziel%:*}
    port=${ziel##*:}
    # Containername: mit Buchstaben beginnen, nicht mit . oder - enden – also nie eine IP-Adresse (S69)
    if [ "$ziel" = "$container" ] || ! [[ "$container" =~ ^[a-z]([a-z0-9_.-]{0,61}[a-z0-9_])?$ ]] \
       || ! [[ "$port" =~ ^[1-9][0-9]{0,4}$ ]] || [ "$port" -gt 65535 ]; then
      echo "Bitte das Ziel als <container>:<port> angeben, z. B. meine-app:8080 (am Anfang ein Buchstabe, dann" \
           "Kleinbuchstaben, Ziffern, - _ .; keine IP-Adresse)." >&2
      exit 2
    fi
    for name in $VERBOTEN; do
      if [ "$container" = "$name" ]; then
        echo "Abgelehnt: „$container“ ist ein Teil von Taleward." >&2
        exit 1
      fi
    done
    if [[ "$container" =~ ^taleward[-_.] ]]; then
      echo "Abgelehnt: Namen, die mit „taleward-“ beginnen, gehören zu Taleward." >&2
      exit 1
    fi
    test_regel_auffrischen && docker compose restart caddy >/dev/null 2>&1 || true
    netz_pruefen
    # Das Ziel muss im Netz der Zusatz-Apps hängen und darf nicht zugleich im Netz von Taleward sein
    p=$(projekt)
    treffer=$(zusatz_container | while read -r _kennung name netze namen; do
      case "$namen" in *",$container,"*) echo "$name $netze" ;; esac
    done)
    if [ -z "$treffer" ]; then
      echo "Abgelehnt: Kein Container „$container“ im Docker-Netz „taleward-zusatz“. Erst die Zusatz-App mit" \
           "„networks: [taleward-zusatz]“ starten (docs/BETRIEB.md)." >&2
      exit 1
    fi
    case "$treffer" in *",${p}_default,"*|*",${p}_kern,"*)
      echo "Abgelehnt: „$container“ hängt auch im Netz von Taleward. Zusatz-Apps nur ins Netz „taleward-zusatz“." >&2
      exit 1 ;;
    esac
    datei="$REGELN/$adresse.caddy"
    anzahl=$(find "$REGELN" -maxdepth 1 -name '*.caddy' ! -name "$adresse.caddy" | wc -l)
    if [ "$anzahl" -ge "$HOECHSTENS" ]; then
      echo "Abgelehnt: Es sind schon $HOECHSTENS Zusatz-Apps eingerichtet. Erst eine ausschalten." >&2
      exit 1
    fi
    alt=""
    [ -f "$datei" ] && alt=$(cat "$datei")
    cat >"$datei.neu" <<EOF
# Von zusatz-app.sh angelegt – nicht von Hand ändern. Ausschalten: sudo bash $ZIEL/zusatz-app.sh aus $adresse
$adresse {
	encode zstd gzip
	header {
		-Server
		-Via
		Strict-Transport-Security "max-age=31536000"
		X-Content-Type-Options "nosniff"
		Referrer-Policy "strict-origin-when-cross-origin"
		defer
	}
	request_body {
		max_size 16MB
	}
	reverse_proxy $container:$port
}
EOF
    mv -f "$datei.neu" "$datei"
    if ! caddy_ok; then
      if [ -n "$alt" ]; then printf '%s\n' "$alt" >"$datei"; else rm -f "$datei"; fi
      echo "Abgelehnt: Caddy nimmt die Regel nicht an. Es bleibt alles, wie es war." >&2
      exit 1
    fi
    if ! neu_laden; then
      if [ -n "$alt" ]; then printf '%s\n' "$alt" >"$datei"; else rm -f "$datei"; fi
      neu_laden || true
      echo "Caddy konnte die Regel nicht laden. Es bleibt alles, wie es war." >&2
      exit 1
    fi
    echo "Zusatz-App unter https://$adresse → $container:$port (das Zertifikat holt Caddy in der ersten Minute)."
    echo "Der Container muss im Docker-Netz „taleward-zusatz“ hängen (docs/BETRIEB.md)."
    ;;
  aus)
    adresse=$(adresse_pruefen "${2:-}")
    if [ ! -f "$REGELN/$adresse.caddy" ]; then
      echo "Für $adresse ist keine Zusatz-App eingerichtet." >&2
      exit 1
    fi
    rm -f "$REGELN/$adresse.caddy"
    neu_laden || true
    echo "Zusatz-App $adresse ist aus. Der Container selbst läuft weiter, bis du ihn anhältst."
    ;;
  liste)
    test_regel_auffrischen && docker compose restart caddy >/dev/null 2>&1 || true
    netz_pruefen
    gefunden=0
    for datei in "$REGELN"/*.caddy; do
      [ -f "$datei" ] || continue
      gefunden=1
      printf '%s → %s\n' "$(basename "$datei" .caddy)" "$(sed -n 's/^\treverse_proxy //p' "$datei")"
    done
    for datei in "$REGELN"/aus/*.caddy; do
      [ -f "$datei" ] || continue
      gefunden=1
      printf '%s (abgeschaltet, passte nach einem Update nicht mehr)\n' "$(basename "$datei" .caddy)"
    done
    [ "$gefunden" = 1 ] || echo "Keine Zusatz-App eingerichtet."
    ;;
  *)
    echo "Aufruf: sudo bash $0 an <adresse> <container>:<port> | aus <adresse> | liste" >&2
    exit 2
    ;;
esac
