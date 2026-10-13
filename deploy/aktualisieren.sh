#!/usr/bin/env bash
# Taleward-Server automatisch aktualisieren (Docker-Paket). Läuft stündlich über den systemd-Timer
# taleward-aktualisieren.timer, den install.sh einrichtet; aktualisiert wird nur nachts (3–5 Uhr) oder sofort, wenn
# in der Verwaltung „Jetzt aktualisieren“ gewählt wurde.
#
# Ablauf: neueste freigegebene Fassung (Git-Tag v…, neuer als die laufende) ermitteln → Sicherung → genau den
# freigegebenen Commit bauen und starten → Gesundheit prüfen. Klappt der Start nicht, geht es zurück auf die alte
# Fassung und die Sicherung von eben.
# Freigabe: Installiert wird nur eine Fassung, deren freigabe.txt mit dem Freigabe-Schlüssel unterschrieben ist
# (Anhänge des Release v…, ssh-keygen -Y verify). Ohne Freigabe bleibt alles, wie es ist.
# Die Verwaltung schaltet das ein/aus (daten/aktualisierung/server-auto.txt) und zeigt das Ergebnis
# (daten/aktualisierung/server-status.json).
#
#   sudo /opt/taleward/aktualisieren.sh           # wie der Timer
#   sudo /opt/taleward/aktualisieren.sh --jetzt   # sofort, unabhängig von Uhrzeit und Schalter
# shellcheck disable=SC1111  # deutsche Anführungszeichen sind Absicht
set -uo pipefail

REPO_NAME=Taleward/taleward-server
REPO_ALT=Tinkerworkss/taleward-server  # Name vor dem Umzug; ältere Freigaben tragen ihn
REPO=https://github.com/$REPO_NAME.git
DOWNLOAD=https://github.com/$REPO_NAME/releases/download
ROH=https://raw.githubusercontent.com/$REPO_NAME
# Öffentlicher Freigabe-Schlüssel (gleichlautend in app/freigabe.py und in der Worker-App)
FREIGABE_SCHLUESSEL="ssh-ed25519 AAAAC3NzaC1lZDI1NTE5AAAAIP8G+vT3BsQPQ+D5UlN615BaEd1FCXEYcxO1KEf7u7Sd"

# freigabe.txt und freigabe.txt.sig im Ordner $1 für das Tag $2 prüfen; gibt den freigegebenen Commit aus
freigabe_pruefen() {
  local ordner=$1 tag=$2 text=$1/freigabe.txt commit
  printf 'taleward-freigabe namespaces="taleward-freigabe" %s\n' "$FREIGABE_SCHLUESSEL" > "$ordner/erlaubt"
  ssh-keygen -Y verify -f "$ordner/erlaubt" -I taleward-freigabe -n taleward-freigabe -s "$ordner/freigabe.txt.sig" \
    < "$text" >/dev/null 2>&1 || return 1
  [ "$(sed -n 1p "$text")" = "taleward-freigabe 1" ] || return 1
  for feld in repo tag commit; do
    [ "$(grep -c "^$feld " "$text")" -eq 1 ] || return 1
  done
  # Seit dem Umzug der Repos (13.10.2026) genau zwei Namen: neu und alt
  grep -qxF -e "repo $REPO_NAME" -e "repo $REPO_ALT" "$text" && grep -qxF "tag $tag" "$text" || return 1
  commit=$(sed -n 's/^commit \([0-9a-f]\{40\}\)$/\1/p' "$text")
  [ -n "$commit" ] || return 1
  echo "$commit"
}

# Fassungen eines Repos, neueste zuerst: nur Tags genau der Form refs/tags/vX.Y.Z (höchstens 10)
kandidaten() {
  git ls-remote --tags --refs "$1" 'v*' 2>/dev/null | awk '{print $2}' | sed -n 's#^refs/tags/##p' \
    | grep -E '^v[0-9]+\.[0-9]+\.[0-9]+$' | sort -V -r | head -n 10
}

# Commit, auf den genau refs/tags/<tag> zeigt (bei Tags mit Text der aufgelöste); Branches und Tags, die nur so ähnlich
# heißen (z. B. zz/refs/tags/<tag>), zählen nicht
tag_commit() {  # repo, tag
  local zeilen
  zeilen=$(git ls-remote "$1" "refs/tags/$2" "refs/tags/$2^{}" 2>/dev/null) || return 1
  printf '%s\n' "$zeilen" | awk -v t="refs/tags/$2" '$2 == t "^{}" {print $1; f=1; exit} END {if (!f) exit 1}' \
    || printf '%s\n' "$zeilen" | awk -v t="refs/tags/$2" '$2 == t {print $1; f=1; exit} END {if (!f) exit 1}'
}

case "${1:-}" in  # nur prüfen (Tests)
  --freigabe-pruefen) freigabe_pruefen "$2" "$3"; exit $? ;;  # <ordner> <tag>
  --kandidaten) kandidaten "$2"; exit $? ;;                   # <repo>
  --tag-commit) tag_commit "$2" "$3"; exit $? ;;              # <repo> <tag>
esac

ZIEL=${TALEWARD_ZIEL:-/opt/taleward}
ORDNER="$ZIEL/daten/aktualisierung"
STATUS="$ORDNER/server-status.json"
cd "$ZIEL" || exit 1
mkdir -p "$ORDNER"
BAULOG=$(mktemp /tmp/taleward-bau.XXXXXX) || exit 1
FREIGABE=$(mktemp -d /tmp/taleward-freigabe.XXXXXX) || exit 1
trap 'rm -rf "$BAULOG" "$FREIGABE"' EXIT

jetzt=0
[ "${1:-}" = "--jetzt" ] && jetzt=1
[ -f "$ORDNER/server-jetzt" ] && jetzt=1

if [ "$jetzt" -eq 0 ]; then
  [ "$(cat "$ORDNER/server-auto.txt" 2>/dev/null || echo an)" = "aus" ] && exit 0
  stunde=$(date +%H)
  [ "$stunde" -ge 3 ] && [ "$stunde" -lt 5 ] || exit 0
fi
als_container() {  # Befehl mit den Rechten des Containers (uid 1000) ausführen – nie als root im Ordner des Containers
  command -v setpriv >/dev/null 2>&1 || return 1
  setpriv --reuid=1000 --regid=1000 --clear-groups "$@"
}

# Der Ordner gehört dem Container. Was root dort ändert, könnte ein Verweis woandershin lenken (zwischen Prüfen und
# Schreiben, S21.5) – darum schreibt und löscht dort der Benutzer des Containers.
als_container rm -f "$ORDNER/server-jetzt" 2>/dev/null \
  || { [ -d "$ORDNER" ] && [ ! -L "$ORDNER" ] && rm -f "$ORDNER/server-jetzt"; }

melden() {  # ergebnis, von, nach, meldung
  local inhalt tmp
  inhalt=$(printf '{"zeit": "%s", "ergebnis": "%s", "von": "%s", "nach": "%s", "meldung": "%s"}' \
    "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$1" "$2" "$3" "${4//\"/\'}")
  # shellcheck disable=SC2016  # $1 wird erst in der inneren Shell ausgewertet
  if ! printf '%s\n' "$inhalt" | als_container sh -c 'umask 022; cat > "$1.neu" && mv -f "$1.neu" "$1"' _ "$STATUS" \
      2>/dev/null; then
    # Ohne setpriv (sehr alte Systeme): wie bisher über eine Datei im Ordner von root
    tmp=$(mktemp "$ZIEL/.server-status.XXXXXX") || return 0
    printf '%s\n' "$inhalt" > "$tmp"
    chmod 644 "$tmp"
    chown 1000:1000 "$tmp" 2>/dev/null || true
    if [ -d "$ORDNER" ] && [ ! -L "$ORDNER" ]; then mv -f "$tmp" "$STATUS"; else rm -f "$tmp"; fi
  fi
  echo "[taleward] $1: $2 → $3 $4"
}

gesund() {
  for _ in $(seq 1 60); do
    if docker compose exec -T server python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/api/v1/health', timeout=3)" >/dev/null 2>&1; then
      return 0
    fi
    sleep 3
  done
  return 1
}

wert_setzen() {  # name, wert in .env
  if grep -q "^$1=" .env; then
    sed -i "s/^$1=.*/$1=$2/" .env
  else
    echo "$1=$2" >> .env
  fi
}
version_setzen() {  # tag, commit – gebaut wird der Commit, der Name dient nur dem Bild
  wert_setzen TALEWARD_VERSION "$1"
  wert_setzen TALEWARD_COMMIT "$2"
}

ALT=$(sed -n 's/^TALEWARD_VERSION=//p' .env); ALT=${ALT:-main}
ALT_COMMIT=$(sed -n 's/^TALEWARD_COMMIT=//p' .env)
KANDIDATEN=$(kandidaten "$REPO")
if [ -z "$KANDIDATEN" ]; then
  melden "fehler" "$ALT" "$ALT" "GitHub nicht erreichbar"
  exit 1
fi

command -v ssh-keygen >/dev/null 2>&1 || apt-get install -y -q openssh-client >/dev/null 2>&1 || true
if ! command -v ssh-keygen >/dev/null 2>&1; then
  melden "fehler" "$ALT" "$ALT" "ssh-keygen fehlt (Paket openssh-client) – die Freigabe kann nicht geprüft werden"
  exit 1
fi

# Die neueste freigegebene Fassung, die neuer ist als die laufende. Ein neueres Tag ohne Freigabe wird übergangen
# (es blockiert nichts), auf eine ältere Fassung wird nie gewechselt. Ohne gültige Unterschrift wird nichts geändert.
NEU="" WARTET="" UNGUELTIG=""
for kandidat in $KANDIDATEN; do
  if [ "$ALT" != "main" ]; then
    [ "$kandidat" = "$ALT" ] && break
    [ "$(printf '%s\n%s\n' "$ALT" "$kandidat" | sort -V | tail -1)" = "$kandidat" ] || break
  fi
  rm -f "$FREIGABE/freigabe.txt" "$FREIGABE/freigabe.txt.sig"
  FREIGABE_URL="$DOWNLOAD/$kandidat"
  if ! curl -fsSL --max-filesize 65536 "$FREIGABE_URL/freigabe.txt" -o "$FREIGABE/freigabe.txt" \
     || ! curl -fsSL --max-filesize 4096 "$FREIGABE_URL/freigabe.txt.sig" -o "$FREIGABE/freigabe.txt.sig"; then
    WARTET=${WARTET:-$kandidat}
    continue
  fi
  if COMMIT=$(freigabe_pruefen "$FREIGABE" "$kandidat"); then
    NEU=$kandidat
    break
  fi
  UNGUELTIG=${UNGUELTIG:-$kandidat}
done
if [ -z "$NEU" ]; then
  if [ -n "$UNGUELTIG" ]; then
    melden "fehler" "$ALT" "$UNGUELTIG" "Die Freigabe von $UNGUELTIG ist ungültig – nichts geändert"
    exit 1
  fi
  if [ -n "$WARTET" ]; then
    melden "wartet" "$ALT" "$WARTET" "Fassung $WARTET ist noch nicht freigegeben – es bleibt bei $ALT"
  elif [ -t 1 ]; then
    echo "[taleward] schon aktuell: $ALT"
  fi
  exit 0  # nichts zu tun – letztes Ergebnis bleibt für die Verwaltung stehen
fi
TAG_COMMIT=$(tag_commit "$REPO" "$NEU") || TAG_COMMIT=""
if [ "$TAG_COMMIT" != "$COMMIT" ]; then
  melden "fehler" "$ALT" "$NEU" "Tag $NEU zeigt nicht auf den freigegebenen Stand – nichts geändert"
  exit 1
fi

SICHERUNG=$(docker compose exec -T server chronik sicherung 2>/dev/null | sed -n 's/^Sicherung angelegt: //p' | tr -d '\r')
if [ -z "$SICHERUNG" ]; then
  melden "fehler" "$ALT" "$NEU" "Sicherung vor dem Update fehlgeschlagen – nichts geändert"
  exit 1
fi

version_setzen "$NEU" "$COMMIT"
# Server mit frischem Grundbild; ein eingebauter Worker (COMPOSE_PROFILES) ohne --pull, damit sein mehrere GB großes
# KI-Paket aus dem Zwischenspeicher kommt, solange sich engine-requirements.txt nicht ändert
# Fertige Bilder (Caddy, Ollama) bei der Gelegenheit auch auffrischen – schlägt das fehl, läuft das alte weiter
# Ist Docker Hub gerade nicht erreichbar oder begrenzt (429 Too Many Requests), mit dem vorhandenen Grundbild bauen
grundbild_bauen() {
  docker compose build --pull server >"$BAULOG" 2>&1 && return 0
  echo "--- Grundbild nicht abrufbar, baue mit dem vorhandenen ---" >>"$BAULOG"
  docker compose build server >>"$BAULOG" 2>&1
}
if grundbild_bauen && docker compose build >>"$BAULOG" 2>&1 \
   && { docker compose pull --ignore-buildable --quiet >>"$BAULOG" 2>&1 || true; } \
   && docker compose up -d && gesund; then
  melden "aktualisiert" "$ALT" "$NEU" ""
  # Paketdateien (dieses Skript, docker-compose.yml, Caddyfile) aus genau dem freigegebenen Stand übernehmen –
  # fehlt eine in der Fassung, bleibt die vorhandene (docker-compose.override.yml wird nie angefasst)
  QUELLE="$ROH/$COMMIT/deploy"
  compose=docker-compose.yml
  [ "$(sed -n 's/^TALEWARD_MODUS=//p' .env)" = "heimnetz" ] && compose=docker-compose.heimnetz.yml
  for paar in aktualisieren.sh:aktualisieren.sh "$compose":docker-compose.yml Caddyfile:Caddyfile; do
    quelle=${paar%%:*}; datei=${paar##*:}
    if curl -fsL "$QUELLE/$quelle" -o "$datei.neu"; then mv "$datei.neu" "$datei"; else rm -f "$datei.neu"; fi
  done
  [ -f aktualisieren.sh ] && chmod +x aktualisieren.sh
  docker compose up -d >/dev/null 2>&1
  docker image prune -f >/dev/null 2>&1 || true
  exit 0
fi

# Zurück: alte Fassung (Image ist noch da) und Datenbank von vor dem Update
wert_setzen TALEWARD_VERSION "$ALT"
if [ -n "$ALT_COMMIT" ]; then wert_setzen TALEWARD_COMMIT "$ALT_COMMIT"; else sed -i '/^TALEWARD_COMMIT=/d' .env; fi
docker compose stop server >/dev/null 2>&1
docker compose run --rm server chronik wiederherstellen "$SICHERUNG" >/dev/null 2>&1
docker compose up -d >/dev/null 2>&1
if gesund; then
  melden "zurueckgenommen" "$ALT" "$NEU" "Die neue Fassung startete nicht; alte Fassung und Daten von vor dem Update sind wieder aktiv"
else
  melden "fehler" "$ALT" "$NEU" "Update und Rücknahme fehlgeschlagen – bitte „sudo docker compose logs server“ ansehen"
fi
exit 1
