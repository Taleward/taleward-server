"""Sicherung mit Passwort (ab 0.4.100): eine gewöhnliche Sicherung (ZIP), verschlüsselt zum Mitnehmen.

Für Taleward für Windows: „Sicherung speichern“ legt die Datei dort ab, wo der Nutzer sie haben will (USB-Stick,
Cloud-Ordner) – deshalb immer mit Passwort. Aus dem Passwort wird mit Argon2id ein Schlüssel abgeleitet; der Inhalt
ist in Blöcken mit AES-256-GCM verschlüsselt. Jeder Block trägt seine Nummer und ein Endkennzeichen in der Nonce, der
Kopf ist in jedem Block mitgeprüft: Vertauschte, fehlende oder angehängte Blöcke fallen auf.

Aufbau: MAGIE (8) · Salz (16) · Zeitkosten (4) · Speicher in KiB (4) · Parallelität (1) · Nonce-Anfang (7)
        danach Blöcke: Länge (4) · Geheimtext mit Prüfwert (Länge)
Nonce je Block: Nonce-Anfang (7) · Blocknummer (4) · 1 beim letzten Block, sonst 0 (1)

Zugänge, die unter Windows geschützt abgelegt sind (siehe app/dpapi.py), lassen sich nur auf demselben PC wieder
lesen; auf einem anderen PC werden sie nach dem Zurückspielen neu eingetragen.
"""
from __future__ import annotations

import os
import struct
import tempfile
from pathlib import Path
from typing import BinaryIO

MAGIE = b"TWSICH01"
ENDUNG = ".taleward"
MINDESTLAENGE = 8
BLOCK = 1024 * 1024
_KOPF = struct.Struct(">16sIIB7s")
_LAENGE = struct.Struct(">I")
# Standard für einen gewöhnlichen PC: knapp eine Sekunde
ZEITKOSTEN, SPEICHER_KIB, PARALLEL = 3, 64 * 1024, 2
# Beim Lesen höchstens das (eine fremde Datei soll den PC nicht lahmlegen)
_HOECHSTENS = (10, 1024 * 1024, 8)


class PasswortFehler(Exception):
    """Passwort falsch oder Datei beschädigt (lässt sich nicht unterscheiden)."""


class FormatFehler(Exception):
    pass


def _schluessel(passwort: str, salz: bytes, zeit: int, speicher: int, parallel: int) -> bytes:
    from argon2.low_level import Type, hash_secret_raw

    return hash_secret_raw(passwort.encode("utf-8"), salz, time_cost=zeit, memory_cost=speicher,
                           parallelism=parallel, hash_len=32, type=Type.ID)


def _nonce(anfang: bytes, nummer: int, letzter: bool) -> bytes:
    return anfang + struct.pack(">IB", nummer, 1 if letzter else 0)


def verschluesseln(quelle: BinaryIO, ziel: BinaryIO, passwort: str, *, zeit: int | None = None,
                   speicher: int | None = None, parallel: int | None = None) -> None:
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    zeit, speicher, parallel = zeit or ZEITKOSTEN, speicher or SPEICHER_KIB, parallel or PARALLEL
    if len(passwort) < MINDESTLAENGE:
        raise ValueError(f"Das Passwort braucht mindestens {MINDESTLAENGE} Zeichen.")
    salz, anfang = os.urandom(16), os.urandom(7)
    kopf = MAGIE + _KOPF.pack(salz, zeit, speicher, parallel, anfang)
    aes = AESGCM(_schluessel(passwort, salz, zeit, speicher, parallel))
    ziel.write(kopf)
    nummer, block = 0, quelle.read(BLOCK)
    while True:
        naechster = quelle.read(BLOCK)
        letzter = not naechster
        teil = aes.encrypt(_nonce(anfang, nummer, letzter), block, kopf)
        ziel.write(_LAENGE.pack(len(teil)) + teil)
        if letzter:
            return
        nummer, block = nummer + 1, naechster


def entschluesseln(quelle: BinaryIO, ziel: BinaryIO, passwort: str) -> None:
    from cryptography.exceptions import InvalidTag
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    kopf = quelle.read(len(MAGIE) + _KOPF.size)
    if len(kopf) < len(MAGIE) + _KOPF.size or not kopf.startswith(MAGIE):
        raise FormatFehler("Das ist keine Taleward-Sicherung mit Passwort.")
    salz, zeit, speicher, parallel, anfang = _KOPF.unpack(kopf[len(MAGIE):])
    if not (1 <= zeit <= _HOECHSTENS[0] and 8 <= speicher <= _HOECHSTENS[1] and 1 <= parallel <= _HOECHSTENS[2]):
        raise FormatFehler("Die Sicherung hat ungewöhnliche Einstellungen und wird nicht geöffnet.")
    aes = AESGCM(_schluessel(passwort, salz, zeit, speicher, parallel))
    nummer = 0
    while True:
        roh = quelle.read(_LAENGE.size)
        if len(roh) < _LAENGE.size:
            raise PasswortFehler("Die Sicherung ist unvollständig.")
        (laenge,) = _LAENGE.unpack(roh)
        if not 16 <= laenge <= BLOCK + 16:
            raise PasswortFehler("Die Sicherung ist beschädigt.")
        teil = quelle.read(laenge)
        if len(teil) < laenge:
            raise PasswortFehler("Die Sicherung ist unvollständig.")
        for letzter in (False, True):
            try:
                klar = aes.decrypt(_nonce(anfang, nummer, letzter), teil, kopf)
                break
            except InvalidTag:
                continue
        else:
            raise PasswortFehler("Das Passwort stimmt nicht, oder die Datei ist beschädigt.")
        ziel.write(klar)
        if letzter:
            if quelle.read(1):
                raise PasswortFehler("Die Sicherung ist beschädigt.")
            return
        nummer += 1


def exportieren(ziel: Path, passwort: str) -> Path:
    """Vollständige Sicherung anlegen und verschlüsselt nach `ziel` schreiben (läuft auch bei laufendem Server)."""
    from app import sicherung

    if len(passwort) < MINDESTLAENGE:
        raise ValueError(f"Das Passwort braucht mindestens {MINDESTLAENGE} Zeichen.")
    ziel = ziel.expanduser()
    if ziel.suffix.lower() != ENDUNG:
        ziel = ziel.with_name(ziel.name + ENDUNG)
    with tempfile.TemporaryDirectory(dir=sicherung.ablage().parent) as tmp:
        klar = sicherung.erstellen("export", ordner=Path(tmp))
        teil = ziel.with_name(ziel.name + ".part")
        try:
            with open(klar, "rb") as q, open(teil, "wb") as z:
                verschluesseln(q, z, passwort)
            teil.replace(ziel)
        finally:
            teil.unlink(missing_ok=True)
    return ziel


def importieren(datei: Path, passwort: str) -> tuple[dict, Path | None]:
    """Nur bei gestopptem Server. Entschlüsselt, prüft und spielt zurück; der jetzige Stand wird vorher gesichert.
    Liefert (Angaben aus der Sicherung, Pfad der Vorher-Sicherung)."""
    from app import sicherung
    from app.config import get_settings

    s = get_settings()
    s.data_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=s.data_dir) as tmp:
        klar = Path(tmp) / "sicherung.zip"
        with open(datei.expanduser(), "rb") as q, open(klar, "wb") as z:
            entschluesseln(q, z, passwort)
        info = sicherung.pruefen(klar)
        return info, sicherung.wiederherstellen(klar)
