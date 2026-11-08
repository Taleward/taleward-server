"""Verschlüsselte Spuren des Discord-Bots (Schnittstelle 0.4.34, S65 mit K1–K7 und S65.1–S65.4).

Der Bot legt Tonspuren bis zum Hochladen nur verschlüsselt ab, mit einem öffentlichen Schlüssel dieses Servers. Nur der
Server kann sie öffnen. Format „TWSE1“ wie in der YAML (Upload) bzw. im Bot-Repo `docs/spuren-verschluesselt.md`.

Verbindlich:
- Eigenes X25519-Schlüsselpaar nur dafür (nicht der Signierschlüssel für „Kapitel teilen“). Der private Teil steht
  geschützt in server_meta (`bot.spurschluessel`, siehe dpapi.GEHEIME_SCHLUESSEL), geht nie in eine Antwort und nie mit
  dem Umzug. Der öffentliche Teil geht nur an den Bot (Antwort beim Anlegen einer Bot-Runde).
- Erneuern nur in der Verwaltung mit frischem zweitem Faktor. Ältere private Teile bleiben, bis keine offene Bot-Runde
  sie mehr braucht (höchstens 30 Stunden nach ihrem Start); danach löscht die Wartung sie.
- HPKE nur über die Bibliothek `cryptography` (feste Fassung im uv.lock); selbst zusammengesetzt wird nur das Format.
- Öffnen beim Abschluss des Uploads in ein Zwischenverzeichnis im Datenordner; bei jedem Fehler sofort löschen, erst
  nach dem letzten geprüften Abschnitt an den üblichen Ort (S65.1). Kopfwerte werden streng geprüft, bevor sie benutzt
  werden (S65.4); die keyId dient nur zum Nachschlagen, nie als Pfad (S65.3).
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import math
import os
import re
import shutil
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.bindings._rust import openssl as _rust
from cryptography.hazmat.primitives import hpke, serialization
from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import errors
from app.db import utcnow

MIME = "application/vnd.taleward.spur-verschluesselt"
ENDUNG = ".twse"
META = "bot.spurschluessel"  # JSON {"aktuell": <privat b64>, "alt": [{"privat": …, "bis": iso}]}, geschützt
KENNUNG = b"TWSE1"
INFO = b"taleward-spur-1"
HALTEN_STUNDEN = 30  # wie OHNE_TON_STUNDEN im Bot-Teil: danach wird eine offene Bot-Runde ohnehin beendet
ABSCHNITT_MIN, ABSCHNITT_MAX = 1024, 1048576
TAG = 16
KOPF_WERT = re.compile(r"^[A-Za-z0-9_-]{1,64}$")  # S65.4: wie die IDs des Servers (UUID), nur ASCII
_SUITE = hpke.Suite(hpke.KEM.X25519, hpke.KDF.HKDF_SHA256, hpke.AEAD.AES_256_GCM)


class NichtLesbar(Exception):
    """Ein Schlüssel ist gespeichert, lässt sich aber gerade nicht öffnen (Windows-Datenschutz unter einem anderen
    Nutzer). Dann wird nichts ersetzt; Bot-Runden gehen vorübergehend nicht (503)."""


class Ungueltig(Exception):
    """Diese Spur lässt sich nicht öffnen (fremder oder abgelaufener Schlüssel, verändert, gekürzt, vertauscht)."""


# ---------------------------------------------------------------- HPKE (nur über die Bibliothek)
def hpke_oeffnen(privat: X25519PrivateKey, enc: bytes, geheimtext: bytes, aad: bytes, info: bytes = INFO) -> bytes:
    """HPKE Basismodus, Einmal-Öffnen (RFC 9180): DHKEM(X25519, HKDF-SHA256) / HKDF-SHA256 / AES-256-GCM.
    `cryptography` erwartet enc ‖ Geheimtext; die AAD geht über die (feste) Hilfsfunktion der Bibliothek."""
    return _rust.hpke._decrypt_with_aad(_SUITE, enc + geheimtext, privat, info=info, aad=aad)


def hpke_versiegeln(oeffentlich, klartext: bytes, aad: bytes, info: bytes = INFO) -> tuple[bytes, bytes]:
    """Gegenstück (nur für Tests): gibt (enc, Geheimtext) zurück."""
    roh = _rust.hpke._encrypt_with_aad(_SUITE, klartext, oeffentlich, info=info, aad=aad)
    return roh[:32], roh[32:]


# ---------------------------------------------------------------- Schlüssel des Servers (K1, S65.3)
def _roh_oeffentlich(privat: X25519PrivateKey) -> bytes:
    return privat.public_key().public_bytes(serialization.Encoding.Raw, serialization.PublicFormat.Raw)


def _roh_privat(privat: X25519PrivateKey) -> bytes:
    return privat.private_bytes(serialization.Encoding.Raw, serialization.PrivateFormat.Raw,
                                serialization.NoEncryption())


def key_id(oeffentlich_roh: bytes) -> str:
    return hashlib.sha256(oeffentlich_roh).hexdigest()


def _privat_aus(text: str) -> X25519PrivateKey:
    roh = base64.b64decode(text, validate=True)
    if len(roh) != 32:
        raise ValueError("falsche Länge")
    return X25519PrivateKey.from_private_bytes(roh)


def _utc(t: datetime) -> datetime:
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


@dataclass
class Bund:
    aktuell: X25519PrivateKey
    alt: list[tuple[X25519PrivateKey, datetime]]


def _laden(db: Session) -> Bund | None:
    from app.einstellungen import meta_lesen
    from app.models import ServerMeta

    roh = db.get(ServerMeta, META)
    if roh is None or not roh.value:
        return None
    try:
        d = json.loads(meta_lesen(db, META) or "")
        aktuell = _privat_aus(d["aktuell"])
        alt = [(_privat_aus(x["privat"]), _utc(datetime.fromisoformat(x["bis"]))) for x in d.get("alt", [])]
    except (ValueError, TypeError, KeyError, AttributeError):
        raise NichtLesbar() from None
    return Bund(aktuell, alt)


def _speichern(db: Session, b: Bund) -> None:
    from app.einstellungen import meta_schreiben

    d = {"aktuell": base64.b64encode(_roh_privat(b.aktuell)).decode("ascii"),
         "alt": [{"privat": base64.b64encode(_roh_privat(p)).decode("ascii"), "bis": bis.isoformat()}
                 for p, bis in b.alt]}
    meta_schreiben(db, META, json.dumps(d))


def _bund(db: Session) -> Bund:
    """Den Schlüssel holen, beim ersten Mal anlegen. Wie beim Teilen (S61): ein gespeicherter, der sich nicht öffnen
    lässt, bleibt stehen; legen zwei Anfragen zugleich an, gewinnt eine und die andere liest deren Schlüssel."""
    from sqlalchemy.exc import IntegrityError

    from app import dpapi
    from app.models import ServerMeta

    b = _laden(db)
    if b is not None:
        return b
    b = Bund(X25519PrivateKey.generate(), [])
    d = {"aktuell": base64.b64encode(_roh_privat(b.aktuell)).decode("ascii"), "alt": []}
    try:
        with db.begin_nested():
            db.add(ServerMeta(key=META, value=dpapi.text_schuetzen(json.dumps(d))))
    except IntegrityError:
        db.expire_all()
        b = _laden(db)
        if b is None:
            raise NichtLesbar() from None
    return b


def track_key(db: Session) -> dict:
    """TrackKey für die Antwort an den Bot (nur dort). Nicht lesbar → 503."""
    try:
        b = _bund(db)
    except NichtLesbar:
        raise errors.ApiError(503, "track_key_unavailable") from None
    roh = _roh_oeffentlich(b.aktuell)
    return {"keyId": key_id(roh), "publicKey": base64.b64encode(roh).decode("ascii")}


def _letzte_offene_bot_runde(db: Session) -> datetime | None:
    from app.models import GameSession

    t = db.scalar(select(func.max(GameSession.created_at)).where(GameSession.bot_key_id.is_not(None),
                                                                 GameSession.state.in_(("recording", "uploading"))))
    return _utc(t) if t else None


def erneuern(db: Session) -> str:
    """Neues Paar; der bisherige private Teil bleibt, solange eine offene Bot-Runde ihn brauchen kann (höchstens
    30 Stunden nach deren Start). Gibt die neue keyId zurück. Nur aus der Verwaltung mit frischem zweitem Faktor."""
    b = _bund(db)
    jetzt = utcnow()
    letzte = _letzte_offene_bot_runde(db)
    alt = [(p, bis) for p, bis in b.alt if bis > jetzt]
    if letzte is not None and letzte + timedelta(hours=HALTEN_STUNDEN) > jetzt:
        alt.append((b.aktuell, letzte + timedelta(hours=HALTEN_STUNDEN)))
    neu = Bund(X25519PrivateKey.generate(), alt)
    _speichern(db, neu)
    return key_id(_roh_oeffentlich(neu.aktuell))


def stand(db: Session) -> dict | None:
    """Für die Verwaltung: Kennung (gekürzt) und Anzahl aufbewahrter älterer Schlüssel. Ohne Schlüssel: None."""
    try:
        b = _laden(db)
    except NichtLesbar:
        return {"lesbar": False, "kennung": "", "alt": 0}
    if b is None:
        return None
    jetzt = utcnow()
    return {"lesbar": True, "kennung": key_id(_roh_oeffentlich(b.aktuell))[:12],
            "alt": sum(1 for _p, bis in b.alt if bis > jetzt)}


def wartung(db: Session) -> int:
    """Abgelaufene ältere private Teile löschen (S65.3). Gibt die Anzahl zurück."""
    try:
        b = _laden(db)
    except NichtLesbar:
        return 0
    if b is None:
        return 0
    jetzt = utcnow()
    bleiben = [(p, bis) for p, bis in b.alt if bis > jetzt]
    weg = len(b.alt) - len(bleiben)
    if weg:
        _speichern(db, Bund(b.aktuell, bleiben))
    return weg


def _schluessel_zu(db: Session, kennung: bytes) -> X25519PrivateKey | None:
    """keyId aus dem Kopf nur als Nachschlagewert (S65.3)."""
    try:
        b = _laden(db)
    except NichtLesbar:
        return None
    if b is None:
        return None
    jetzt = utcnow()
    for p in [b.aktuell] + [p for p, bis in b.alt if bis > jetzt]:
        if hmac.compare_digest(hashlib.sha256(_roh_oeffentlich(p)).digest(), kennung):
            return p
    return None


# ---------------------------------------------------------------- Öffnen (K4, S65.1, S65.4)
class _Leser:
    """Liest die Teile einer Datei der Reihe nach wie eine Datei. Hält höchstens einen Abschnitt im Speicher."""

    def __init__(self, pfade: list[Path]):
        self._pfade = iter(pfade)
        self._datei = None
        self._vorrat = b""

    def lesen(self, n: int) -> bytes:
        teile = [self._vorrat[:n]]
        self._vorrat = self._vorrat[n:]
        rest = n - len(teile[0])
        while rest > 0:
            if self._datei is None:
                p = next(self._pfade, None)
                if p is None:
                    break
                self._datei = open(p, "rb")
            b = self._datei.read(rest)
            if not b:
                self._datei.close()
                self._datei = None
                continue
            teile.append(b)
            rest -= len(b)
        return b"".join(teile)

    def genau(self, n: int) -> bytes:
        b = self.lesen(n)
        if len(b) != n:
            raise Ungueltig("gekürzt")
        return b

    def am_ende(self) -> bool:
        if self._vorrat:
            return False
        self._vorrat = self.lesen(1)
        return not self._vorrat

    def schliessen(self) -> None:
        if self._datei is not None:
            self._datei.close()
            self._datei = None


def _kopfwert(leser: _Leser) -> bytes:
    n = leser.genau(1)[0]
    roh = leser.genau(n)
    try:
        text = roh.decode("ascii")
    except UnicodeDecodeError:
        raise Ungueltig("Kopf") from None
    if not KOPF_WERT.match(text):
        raise Ungueltig("Kopf")
    return roh


def oeffnen(db: Session, teile: list[Path], ziel: Path, teilgroesse: int, session_id: str,
            attendee_id: str) -> int:
    """Eine Spur öffnen und den Klartext in Teilen der Größe `teilgroesse` nach `ziel` (ein noch nicht vorhandenes
    Zwischenverzeichnis) schreiben. Gibt die Klartextgröße zurück. Bei jedem Fehler: Ungueltig, `ziel` ist dann weg."""
    leser = _Leser(teile)
    ziel.mkdir(mode=0o700)
    try:
        return _oeffnen(db, leser, ziel, teilgroesse, session_id.encode("ascii"), attendee_id.encode("ascii"))
    except Exception:
        shutil.rmtree(ziel, ignore_errors=True)
        raise
    finally:
        leser.schliessen()


def _oeffnen(db: Session, leser: _Leser, ziel: Path, teilgroesse: int, sid_erwartet: bytes,
             aid_erwartet: bytes) -> int:
    if leser.genau(5) != KENNUNG:
        raise Ungueltig("Kennung")
    kennung = leser.genau(32)
    enc = leser.genau(32)
    versiegelt = leser.genau(48)
    abschnitt = int.from_bytes(leser.genau(4), "big")
    if not ABSCHNITT_MIN <= abschnitt <= ABSCHNITT_MAX:
        raise Ungueltig("Abschnittsgröße")
    sid = _kopfwert(leser)
    aid = _kopfwert(leser)
    if not (hmac.compare_digest(sid, sid_erwartet) and hmac.compare_digest(aid, aid_erwartet)):
        raise Ungueltig("andere Runde oder Person")
    privat = _schluessel_zu(db, kennung)
    if privat is None:
        raise Ungueltig("unbekannter Schlüssel")
    try:
        inhalt = hpke_oeffnen(privat, enc, versiegelt, KENNUNG + kennung + sid + aid)
    except Exception:  # die Bibliothek meldet verschiedene Fehlerarten
        raise Ungueltig("Inhaltsschlüssel") from None
    if len(inhalt) != 32:
        raise Ungueltig("Inhaltsschlüssel")
    aes = AESGCM(inhalt)
    nummer = 0
    groesse = 0
    teil_nr = 0
    puffer = bytearray()
    while True:
        n = int.from_bytes(leser.genau(4), "big")
        if not TAG <= n <= abschnitt + TAG:  # Länge prüfen, bevor gelesen wird
            raise Ungueltig("Abschnittslänge")
        geheim = leser.genau(n)
        letzter = leser.am_ende()
        klar_n = n - TAG
        if not letzter and klar_n != abschnitt:
            raise Ungueltig("Abschnittslänge")
        aad = (KENNUNG + sid + aid + nummer.to_bytes(8, "big") + (b"\x01" if letzter else b"\x00")
               + klar_n.to_bytes(4, "big"))
        try:
            klar = aes.decrypt(b"\x00\x00\x00\x00" + nummer.to_bytes(8, "big"), geheim, aad)
        except InvalidTag:
            raise Ungueltig("Abschnitt") from None
        puffer += klar
        groesse += len(klar)
        while len(puffer) >= teilgroesse:
            _teil(ziel, teil_nr, bytes(puffer[:teilgroesse]))
            del puffer[:teilgroesse]
            teil_nr += 1
        nummer += 1
        if letzter:
            break
    if puffer:
        _teil(ziel, teil_nr, bytes(puffer))
    if groesse == 0:
        raise Ungueltig("leer")
    return groesse


def _teil(ziel: Path, nr: int, daten: bytes) -> None:
    with open(ziel / f"{nr:05d}.part", "wb") as aus:  # gleiche Namen wie storage.chunk_path
        aus.write(daten)


# ---------------------------------------------------------------- Beim Abschluss des Uploads
def braucht_oeffnen(up) -> bool:
    return any(f.mime_type == MIME for f in up.files)


def alle_oeffnen(db: Session, up, s) -> None:
    """Beim Abschluss (Upload steht auf „measuring“): alle verschlüsselten Spuren öffnen. Ungültige Spuren werden samt
    Teilen verworfen (Verlauf track_discarded) und 400 track_invalid gemeldet; der Upload bleibt dann offen. Waren alle
    ungültig, ist der Upload verworfen und die Runde no_recording. Geöffnet an den üblichen Ort kommt nur, wenn alle
    Spuren gültig sind – Klartext liegt vorher nur im Zwischenverzeichnis."""
    from app import discord_bot, storage
    from app.models import Attendee

    fertig: list[tuple[object, Path, int]] = []
    ungueltig = []
    try:
        for f in sorted(up.files, key=lambda x: x.position):
            if f.mime_type != MIME:
                continue  # schon geöffnet (Abschluss nach abgebrochener Messung)
            ordner = storage.upload_dir(up.id) / f.id
            zwischen = storage.upload_dir(up.id) / f"{f.id}.offen"
            shutil.rmtree(zwischen, ignore_errors=True)
            teile = [storage.chunk_path(up.id, f.id, i) for i in range(f.chunk_count)]
            try:
                if not f.track_attendee_id or not ordner.is_dir():
                    raise Ungueltig("ohne Anwesenden")
                groesse = oeffnen(db, teile, zwischen, up.chunk_size, s.id, f.track_attendee_id)
            except (Ungueltig, OSError):
                ungueltig.append(f)
                continue
            fertig.append((f, zwischen, groesse))
        if ungueltig:
            for _f, zwischen, _g in fertig:
                shutil.rmtree(zwischen, ignore_errors=True)
            fertig = []
            ids = [f.track_attendee_id for f in ungueltig if f.track_attendee_id]
            for f in ungueltig:
                a = db.get(Attendee, f.track_attendee_id) if f.track_attendee_id else None
                if a is not None and a.session_id == s.id:
                    discord_bot.spur_verwerfen(db, s, a, "spur_verworfen")
                else:
                    storage.delete_upload_file(up.id, f.id)
                    db.delete(f)
                    db.flush()
            db.refresh(up)
            if not up.files:
                storage.delete_upload_files(up.id)
                up.state = "aborted"
                discord_bot.runde_beenden(db, s)
            db.commit()
            raise errors.ApiError(400, "track_invalid", details={"attendeeIds": ids})
        for f, zwischen, groesse in fertig:
            ordner = storage.upload_dir(up.id) / f.id
            alt = storage.upload_dir(up.id) / f"{f.id}.alt"
            shutil.rmtree(alt, ignore_errors=True)
            os.replace(ordner, alt)
            os.replace(zwischen, ordner)
            shutil.rmtree(alt, ignore_errors=True)
            f.size_bytes = groesse
            f.chunk_count = math.ceil(groesse / up.chunk_size)
            f.mime_type = "audio/ogg"
            name = f.file_name[:-len(ENDUNG)] if f.file_name.lower().endswith(ENDUNG) else f.file_name
            f.file_name = (name + ".ogg")[-300:]
            db.commit()  # je Spur sofort, damit Ablage und Datenbank zusammenpassen
    finally:
        for _f, zwischen, _g in fertig:
            shutil.rmtree(zwischen, ignore_errors=True)
