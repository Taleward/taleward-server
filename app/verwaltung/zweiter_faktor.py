"""Zweiter Faktor für die Verwaltung (TOTP nach RFC 6238, 0.4.84).

- Jeder Verwalter kann ihn unter „Konten“ für sich einschalten: Schlüssel als QR-Code in eine Authenticator-App
  übernehmen, mit einem Code bestätigen, dann zehn Wiederherstellungscodes notieren (werden nur einmal angezeigt).
- Ist er an, verlangt die Anmeldung nach dem Passwort einen Code (6 Ziffern) oder einen Wiederherstellungscode. Ein
  Code gilt nur einmal (auch nicht im nächsten Zeitfenster noch einmal); Fehlversuche zählen je Konto und Adresse.
- Ausschalten nur mit Passwort und gültigem Code. Gerät verloren und keine Codes mehr: auf dem Server
  `chronik zweiter-faktor-aus -u <name>`.

Gespeichert in server_meta unter ``verwaltung.totp.<user_id>`` (JSON): Schlüssel (Base32), zuletzt benutztes
Zeitfenster, Hashes der Wiederherstellungscodes, seit wann. Ein angefangenes Einrichten liegt unter
``verwaltung.totp_neu.<user_id>`` und verfällt nach 15 Minuten.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import secrets
import struct
import time
from urllib.parse import quote

from sqlalchemy.orm import Session

from app.einstellungen import meta_lesen, meta_schreiben
from app.models import ServerMeta

SCHRITT = 30
STELLEN = 6
FENSTER = 1  # ein Zeitfenster davor und danach (Uhren gehen nicht genau)
ANZAHL_CODES = 10
NEU_GUELTIG = 15 * 60
_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # ohne 0/O, 1/I


def _schluessel(user_id: str) -> str:
    return f"verwaltung.totp.{user_id}"


def _neu_schluessel(user_id: str) -> str:
    return f"verwaltung.totp_neu.{user_id}"


def code(geheim_b32: str, zeitfenster: int) -> str:
    roh = base64.b32decode(geheim_b32 + "=" * (-len(geheim_b32) % 8), casefold=True)
    h = hmac.new(roh, struct.pack(">Q", zeitfenster), hashlib.sha1).digest()
    o = h[-1] & 0x0F
    zahl = (struct.unpack(">I", h[o:o + 4])[0] & 0x7FFFFFFF) % 10 ** STELLEN
    return f"{zahl:0{STELLEN}d}"


def _passendes_fenster(geheim: str, eingabe: str, jetzt: float | None = None) -> int | None:
    eingabe = "".join(eingabe.split())
    if len(eingabe) != STELLEN or not eingabe.isdigit():
        return None
    t = int((time.time() if jetzt is None else jetzt) // SCHRITT)
    for f in range(t - FENSTER, t + FENSTER + 1):
        if hmac.compare_digest(code(geheim, f), eingabe):
            return f
    return None


def _hash(wiederherstellungscode: str) -> str:
    norm = "".join(c for c in wiederherstellungscode.upper() if c.isalnum())
    return hashlib.sha256(b"taleward-wiederherstellung:" + norm.encode()).hexdigest()


def _neue_codes() -> list[str]:
    return ["-".join("".join(secrets.choice(_ALPHABET) for _ in range(5)) for _ in range(2))
            for _ in range(ANZAHL_CODES)]


def _zu(geheim: str) -> str:
    """Unter Windows (Taleward für Windows) liegt das Geheimnis per DPAPI geschützt in der Datenbank, sonst unverändert.
    Nur dieses Feld – das bedingte Update beim Verbrauchen vergleicht den ganzen gespeicherten Text."""
    from app import dpapi

    return dpapi.text_schuetzen(geheim)


def _auf(geheim: str | None) -> str | None:
    """None, wenn sich ein geschütztes Geheimnis hier nicht öffnen lässt (anderer PC oder Nutzer) – dann gilt kein Code
    aus der Authenticator-App mehr, nur noch Wiederherstellungscodes; Notweg: `chronik zweiter-faktor-aus -u <name>`."""
    from app import dpapi

    return dpapi.text_oeffnen(geheim)


def stand(db: Session, user_id: str) -> dict | None:
    wert = meta_lesen(db, _schluessel(user_id))
    try:
        return json.loads(wert) if wert else None
    except ValueError:
        return None


def aktiv(db: Session, user_id: str) -> bool:
    return stand(db, user_id) is not None


def restliche_codes(db: Session, user_id: str) -> int:
    s = stand(db, user_id)
    return len(s.get("codes") or []) if s else 0


# ---------------------------------------------------------------- Einrichten
def beginnen(db: Session, user_id: str) -> str:
    """Neuen Schlüssel für das Einrichten anlegen (noch nicht aktiv). Liefert ihn als Base32."""
    geheim = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    meta_schreiben(db, _neu_schluessel(user_id), json.dumps({"geheim": _zu(geheim), "bis": time.time() + NEU_GUELTIG}))
    return geheim


def angefangen(db: Session, user_id: str) -> str | None:
    wert = meta_lesen(db, _neu_schluessel(user_id))
    try:
        d = json.loads(wert) if wert else None
    except ValueError:
        return None
    return _auf(d["geheim"]) if d and d.get("bis", 0) > time.time() else None


def uri(geheim: str, konto: str, aussteller: str) -> str:
    """otpauth-Adresse für den QR-Code (Authenticator-Apps)."""
    a = aussteller.replace(":", " ").strip() or "Taleward"
    return (f"otpauth://totp/{quote(a)}:{quote(konto)}?secret={geheim}&issuer={quote(a)}"
            f"&algorithm=SHA1&digits={STELLEN}&period={SCHRITT}")


def bestaetigen(db: Session, user_id: str, eingabe: str) -> list[str] | None:
    """Einrichten abschließen, wenn der Code zum angefangenen Schlüssel passt. Liefert die Wiederherstellungscodes
    (nur dieses eine Mal im Klartext) oder None."""
    geheim = angefangen(db, user_id)
    if geheim is None:
        return None
    fenster = _passendes_fenster(geheim, eingabe)
    if fenster is None:
        return None
    codes = _neue_codes()
    meta_schreiben(db, _schluessel(user_id), json.dumps({
        "geheim": _zu(geheim), "zuletzt": fenster, "codes": [_hash(c) for c in codes], "seit": int(time.time())}))
    _loeschen(db, _neu_schluessel(user_id))
    faktor_fassung_erhoehen(db, user_id)  # S44: neuer Faktor, neue Wiederherstellungscodes → vertraute Geräte ungültig
    from app import konto_mail
    from app.models import User

    konto_mail.vormerken(db, db.get(User, user_id), "faktor_an")
    return codes


def ausschalten(db: Session, user_id: str) -> None:
    war_an = aktiv(db, user_id)
    _loeschen(db, _schluessel(user_id))
    _loeschen(db, _neu_schluessel(user_id))
    from app import passkeys

    passkeys.alle_entfernen(db, user_id)  # 0.4.143: ohne Code kein Passkey (TOTP ist der Rückweg)
    faktor_fassung_erhoehen(db, user_id)  # S44: vertraute Geräte werden ungültig
    frisch_vergessen(db, user_id)
    if war_an:
        from app import konto_mail
        from app.models import User

        konto_mail.vormerken(db, db.get(User, user_id), "faktor_aus")


def _loeschen(db: Session, schluessel: str) -> None:
    row = db.get(ServerMeta, schluessel)
    if row is not None:
        db.delete(row)


# ---------------------------------------------------------------- Prüfen
def _verbrauchen(db: Session, user_id: str, alt: str, neu: dict) -> bool:
    """Stand nur ersetzen, wenn er seit dem Lesen unverändert ist (bedingtes Update in der Datenbank). Zwei
    gleichzeitige Anfragen mit demselben Code: Die zweite findet den alten Stand nicht mehr und gilt nicht."""
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError

    try:
        erg = db.execute(update(ServerMeta).where(ServerMeta.key == _schluessel(user_id), ServerMeta.value == alt)
                         .values(value=json.dumps({**neu, "geheim": _zu(neu["geheim"])}))  # ältere Klartexte nachziehen
                         .execution_options(synchronize_session="fetch"))
    except OperationalError:  # SQLite: der andere hat gerade geschrieben – dieser Versuch gilt nicht
        db.rollback()
        return False
    return erg.rowcount == 1


def pruefen(db: Session, user_id: str, eingabe: str, jetzt: float | None = None) -> bool:
    """Code oder Wiederherstellungscode prüfen und verbrauchen. False bei falschem oder schon benutztem Code."""
    alt = meta_lesen(db, _schluessel(user_id))
    s = stand(db, user_id)
    if s is None or alt is None:
        return False
    eingabe = (eingabe or "").strip()
    # Lässt sich das Geheimnis hier nicht öffnen (Datenbank auf einen anderen PC gebracht), gilt kein Code aus der
    # Authenticator-App – die Wiederherstellungscodes (nur als Hash gespeichert) gelten weiter.
    geheim = _auf(s.get("geheim"))
    fenster = _passendes_fenster(geheim, eingabe, jetzt) if geheim else None
    if fenster is not None:
        if fenster <= int(s.get("zuletzt") or 0):
            return False  # derselbe (oder ein älterer) Code noch einmal: abgelehnt
        return _verbrauchen(db, user_id, alt, {**s, "zuletzt": fenster})
    h = _hash(eingabe)
    codes = list(s.get("codes") or [])
    for i, c in enumerate(codes):
        if hmac.compare_digest(c, h):
            del codes[i]
            return _verbrauchen(db, user_id, alt, {**s, "codes": codes})
    return False


# ---------------------------------------------------------------- Faktor-Fassung und frischer Faktor (0.4.143, S44)
FRISCH_SEKUNDEN = 5 * 60


def faktor_fassung(db: Session, user_id: str) -> int:
    """Zähler, der steigt, wenn sich am zweiten Faktor etwas ändert (Faktor aus/neu, Passkey entfernt). Vertraute
    Geräte (app/vertraut.py) sind an ihn gebunden."""
    wert = meta_lesen(db, f"verwaltung.faktor_fassung.{user_id}")
    return int(wert) if wert and wert.isdigit() else 0


def faktor_fassung_erhoehen(db: Session, user_id: str) -> int:
    neu = faktor_fassung(db, user_id) + 1
    meta_schreiben(db, f"verwaltung.faktor_fassung.{user_id}", str(neu))
    return neu


def frisch_merken(db: Session, user_id: str, bereich: str = "verwaltung") -> None:
    """Der zweite Faktor (Code oder Passkey) wurde gerade gezeigt – Schritte, die ihn frisch verlangen, gehen jetzt
    FRISCH_SEKUNDEN lang. Auf einem vertrauten Gerät wird das beim Anmelden nicht gesetzt."""
    meta_schreiben(db, f"verwaltung.faktor_frisch.{bereich}.{user_id}", str(int(time.time())))


def frisch(db: Session, user_id: str, bereich: str = "verwaltung") -> bool:
    """Gilt der zweite Faktor als frisch? Ohne eingerichteten zweiten Faktor immer (es gibt nichts zu zeigen)."""
    if not aktiv(db, user_id):
        return True
    wert = meta_lesen(db, f"verwaltung.faktor_frisch.{bereich}.{user_id}")
    return bool(wert and wert.isdigit() and int(wert) + FRISCH_SEKUNDEN > time.time())


def frisch_vergessen(db: Session, user_id: str) -> None:
    for bereich in ("verwaltung", "guthaben"):
        _loeschen(db, f"verwaltung.faktor_frisch.{bereich}.{user_id}")


def konto_entfernt(db: Session, user_id: str) -> None:
    """Konto gelöscht: alles zum zweiten Faktor aus server_meta."""
    from app import passkeys

    passkeys.alle_entfernen(db, user_id)
    for schluessel in (_schluessel(user_id), _neu_schluessel(user_id), f"verwaltung.faktor_fassung.{user_id}"):
        _loeschen(db, schluessel)
    frisch_vergessen(db, user_id)
