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
    meta_schreiben(db, _neu_schluessel(user_id), json.dumps({"geheim": geheim, "bis": time.time() + NEU_GUELTIG}))
    return geheim


def angefangen(db: Session, user_id: str) -> str | None:
    wert = meta_lesen(db, _neu_schluessel(user_id))
    try:
        d = json.loads(wert) if wert else None
    except ValueError:
        return None
    return d["geheim"] if d and d.get("bis", 0) > time.time() else None


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
        "geheim": geheim, "zuletzt": fenster, "codes": [_hash(c) for c in codes], "seit": int(time.time())}))
    _loeschen(db, _neu_schluessel(user_id))
    return codes


def ausschalten(db: Session, user_id: str) -> None:
    _loeschen(db, _schluessel(user_id))
    _loeschen(db, _neu_schluessel(user_id))


def _loeschen(db: Session, schluessel: str) -> None:
    row = db.get(ServerMeta, schluessel)
    if row is not None:
        db.delete(row)


# ---------------------------------------------------------------- Prüfen
def _verbrauchen(db: Session, user_id: str, alt: str, neu: dict) -> bool:
    """S30.2: Stand nur ersetzen, wenn er seit dem Lesen unverändert ist (bedingtes Update in der Datenbank). Zwei
    gleichzeitige Anfragen mit demselben Code: Die zweite findet den alten Stand nicht mehr und gilt nicht."""
    from sqlalchemy import update
    from sqlalchemy.exc import OperationalError

    try:
        erg = db.execute(update(ServerMeta).where(ServerMeta.key == _schluessel(user_id), ServerMeta.value == alt)
                         .values(value=json.dumps(neu)).execution_options(synchronize_session="fetch"))
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
    fenster = _passendes_fenster(s["geheim"], eingabe, jetzt)
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
