#!/bin/bash
# Optional, einmalig: eine statische Website neben dem Server über denselben Caddy ausliefern
# (docs/BETRIEB.md, „Website mitliefern“). Legt den Nutzer „website“ an, der per SSH genau zweierlei darf:
# Dateien in den Website-Ordner hochladen (rsync, nur dorthin) und „neu-laden“ (Caddy-Regeln prüfen und übernehmen).
# Aufruf auf dem Server:  sudo bash /opt/taleward/website-zugang.sh
# Erneut aufrufen ersetzt den Schlüssel (z. B. nach einem Wechsel).
set -euo pipefail
ZIEL=${TALEWARD_ZIEL:-/opt/taleward}
NUTZER=website
EMPFANG=/usr/local/bin/taleward-website-empfang
NEULADEN=/usr/local/sbin/taleward-website-neu-laden

abbruch() { echo "Abbruch: $*" >&2; exit 1; }
[ "$(id -u)" = 0 ] || abbruch "bitte mit sudo starten."
[ -f "$ZIEL/.env" ] && [ -f "$ZIEL/website-neu-laden.sh" ] || abbruch "kein Taleward-Server in $ZIEL (erst install.sh)."

ALT=$(sed -n 's/^WEBSITE_ORDNER=//p' "$ZIEL/.env" | tail -1)
read -r -p "Ordner für die Website [${ALT:-/srv/website}]: " EINGABE </dev/tty || true
ORDNER=${EINGABE:-${ALT:-/srv/website}}
[[ "$ORDNER" =~ ^/(srv|var/www)/[A-Za-z0-9._-]+$ ]] && [[ "$ORDNER" != *..* ]] \
  || abbruch "bitte einen Ordner direkt unter /srv oder /var/www, z. B. /srv/website."

echo "Öffentlicher SSH-Schlüssel für das Hochladen (eine Zeile, beginnt mit „ssh-ed25519“):"
read -r SCHLUESSEL </dev/tty
[[ "$SCHLUESSEL" =~ ^ssh-ed25519\ [A-Za-z0-9+/]+=*(\ [A-Za-z0-9@._-]*)?$ ]] \
  || abbruch "das sieht nicht nach einem öffentlichen ssh-ed25519-Schlüssel aus."

command -v rsync >/dev/null 2>&1 || apt-get install -y -q rsync >/dev/null
[ -x /usr/bin/rrsync ] || abbruch "rrsync fehlt (gehört ab Ubuntu 24.04 zum Paket rsync)."

id "$NUTZER" >/dev/null 2>&1 || useradd --system --create-home --shell /bin/sh "$NUTZER"
passwd -l "$NUTZER" >/dev/null
mkdir -p "$ORDNER"
chown "$NUTZER:$NUTZER" "$ORDNER"
chmod 755 "$ORDNER"

install -m 755 -o root -g root "$ZIEL/website-neu-laden.sh" "$NEULADEN"
cat > "$EMPFANG" <<SKRIPT
#!/bin/sh
# Einziger Befehl für den Nutzer „$NUTZER“ per SSH (authorized_keys: command=…): hochladen oder neu laden
case "\${SSH_ORIGINAL_COMMAND:-}" in
  "rsync --server "*) exec /usr/bin/rrsync -wo $ORDNER ;;
  neu-laden) exec sudo -n $NEULADEN ;;
  *) echo "Nicht erlaubt – nur rsync und „neu-laden“." >&2; exit 1 ;;
esac
SKRIPT
chown root:root "$EMPFANG"
chmod 755 "$EMPFANG"

REGEL=$(mktemp)
echo "$NUTZER ALL=(root) NOPASSWD: $NEULADEN" > "$REGEL"
visudo -cqf "$REGEL" || abbruch "sudo-Regel ungültig."
install -m 440 -o root -g root "$REGEL" /etc/sudoers.d/taleward-website
rm -f "$REGEL"

HEIM=$(getent passwd "$NUTZER" | cut -d: -f6)
install -d -m 755 -o root -g root "$HEIM/.ssh"
printf 'command="%s",restrict %s\n' "$EMPFANG" "$SCHLUESSEL" > "$HEIM/.ssh/authorized_keys"
chown root:root "$HEIM/.ssh/authorized_keys"
chmod 644 "$HEIM/.ssh/authorized_keys"

if grep -q '^WEBSITE_ORDNER=' "$ZIEL/.env"; then
  sed -i "s#^WEBSITE_ORDNER=.*#WEBSITE_ORDNER=$ORDNER#" "$ZIEL/.env"
else
  echo "WEBSITE_ORDNER=$ORDNER" >> "$ZIEL/.env"
fi
mkdir -p "$ZIEL/website-caddy"
(cd "$ZIEL" && docker compose up -d caddy >/dev/null)

IP=$(ip -4 route get 1.1.1.1 2>/dev/null | awk '{for (i = 1; i < NF; i++) if ($i == "src") print $(i + 1)}')
echo
echo "Fertig. Für das Hochladen (z. B. als Secrets im Website-Repository):"
echo "  Adresse:        ${IP:-<IP dieses Servers>}"
echo "  Nutzer:         $NUTZER"
echo "  known_hosts:    ${IP:-<IP>} $(cut -d' ' -f1,2 /etc/ssh/ssh_host_ed25519_key.pub)"
echo "Hochladen:  rsync -rlz --delete --delay-updates dist/ $NUTZER@${IP:-<IP>}:"
echo "Neu laden:  ssh $NUTZER@${IP:-<IP>} neu-laden"
