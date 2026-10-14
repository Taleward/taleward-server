"""Geheimnisse unter Windows mit dem Windows-Datenschutz (DPAPI, Benutzerbereich) ablegen (ab 0.4.100).

Gilt für den Server auf einem Windows-PC (Taleward für Windows): Anmelde-Geheimnis (`geheimnis.txt`), Schlüssel des
eigenen Workers (`EINZELPLATZ_WORKER_DATEI`) und die Zugänge in der Datenbank (Cloud, Hugging Face, Benachrichtigungen,
Anmeldedienste). Lesen kann sie nur derselbe Windows-Nutzer auf demselben PC – eine kopierte Datenbank oder Sicherung
nützt anderswo nichts (die Zugänge sind dort leer und werden neu eingetragen).

Gespeichert wird als Text `dpapi:<Base64>`; ältere Klartexte werden weiter gelesen und beim nächsten Schreiben bzw.
Start geschützt. Außerhalb von Windows ändert sich nichts.
"""
from __future__ import annotations

import base64
import logging
import sys

log = logging.getLogger("dpapi")
PRAEFIX = "dpapi:"
_ENTROPIE = b"Taleward"


def verfuegbar() -> bool:
    return sys.platform == "win32"


def schuetzen(daten: bytes) -> bytes:
    return _aufruf(daten, schuetzen=True)


def oeffnen(daten: bytes) -> bytes:
    return _aufruf(daten, schuetzen=False)


def _aufruf(daten: bytes, schuetzen: bool) -> bytes:  # pragma: no cover – nur unter Windows
    import ctypes
    from ctypes import wintypes

    class Blob(ctypes.Structure):
        _fields_ = [("cbData", wintypes.DWORD), ("pbData", ctypes.POINTER(ctypes.c_char))]

    puffer = []  # bis zum Ende des Aufrufs festhalten, sonst zeigte der Blob ins Leere

    def blob(b: bytes) -> Blob:
        puffer.append(ctypes.create_string_buffer(b, len(b)))
        return Blob(len(b), ctypes.cast(puffer[-1], ctypes.POINTER(ctypes.c_char)))

    crypt32, kernel32 = ctypes.windll.crypt32, ctypes.windll.kernel32  # type: ignore[attr-defined]
    ein, entropie, aus = blob(daten), blob(_ENTROPIE), Blob()
    funktion = crypt32.CryptProtectData if schuetzen else crypt32.CryptUnprotectData
    # CRYPTPROTECT_UI_FORBIDDEN – nie eine Rückfrage anzeigen
    if not funktion(ctypes.byref(ein), None, ctypes.byref(entropie), None, None, 0x1, ctypes.byref(aus)):
        raise OSError(ctypes.GetLastError() or "DPAPI")  # type: ignore[attr-defined]
    try:
        return ctypes.string_at(aus.pbData, aus.cbData)
    finally:
        kernel32.LocalFree(aus.pbData)


def text_schuetzen(wert: str) -> str:
    """Nur unter Windows; sonst unverändert."""
    if not verfuegbar() or not wert or wert.startswith(PRAEFIX):
        return wert
    try:
        return PRAEFIX + base64.b64encode(schuetzen(wert.encode("utf-8"))).decode("ascii")
    except (OSError, AttributeError, ValueError):
        # Lieber weiter wie bisher (nur für den Nutzer lesbar) als gar nicht speichern können
        log.warning("Windows-Datenschutz nicht verfügbar – Wert ungeschützt abgelegt")
        return wert


def text_oeffnen(wert: str | None) -> str | None:
    """Klartext unverändert; geschützter Wert, der sich hier nicht öffnen lässt (anderer PC/Nutzer) → None."""
    if not wert or not wert.startswith(PRAEFIX):
        return wert
    if not verfuegbar():
        return None
    try:
        return oeffnen(base64.b64decode(wert[len(PRAEFIX):])).decode("utf-8")
    except (OSError, ValueError):
        log.warning("Ein geschützter Wert lässt sich auf diesem PC nicht öffnen (von einem anderen PC oder Nutzer?)")
        return None


# Zugänge in server_meta, die geschützt abgelegt werden
GEHEIME_SCHLUESSEL = ("extern.api_key", "llm.api_key", "hf.token", "melden.ntfy_token", "melden.smtp_pass")


def geheim(schluessel: str) -> bool:
    return schluessel in GEHEIME_SCHLUESSEL or (
        schluessel.startswith("oidc.") and schluessel.endswith((".client_secret", ".private_key")))


def nachziehen(db) -> int:
    """Beim Start (Windows): Klartext-Zugänge aus älteren Fassungen nachträglich schützen. Gibt die Anzahl zurück."""
    if not verfuegbar():
        return 0
    from sqlalchemy import select

    from app.models import ServerMeta

    anzahl = 0
    for zeile in db.scalars(select(ServerMeta)):
        if geheim(zeile.key) and zeile.value and not zeile.value.startswith(PRAEFIX):
            zeile.value = text_schuetzen(zeile.value)
            anzahl += 1
    if anzahl:
        db.commit()
    return anzahl
