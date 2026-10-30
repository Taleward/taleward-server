"""Passkeys (WebAuthn: Windows Hello, Fingerabdruck oder Gesicht am Handy, Sicherheitsschlüssel) als zweiter Faktor für
die Verwaltung und die Guthaben-Seite (0.4.143, Entwurf `claude/server-vorschlag-passkey.md`, vom Sicherheits-Chat am
28.10. freigegeben).

- Ein Passkey ersetzt nur den Code-Schritt. Das Passwort bleibt, TOTP bleibt Pflicht als Rückweg: Einen Passkey kann nur
  hinzufügen, wer den zweiten Faktor (Code) eingerichtet hat.
- Herkunft und RP-ID kommen allein aus der eingetragenen öffentlichen Adresse (``angaben.public_url``), nie aus der
  Anfrage. Ohne https-Adresse gibt es keine Passkeys (Knopf fehlt, Server lehnt ab). Einzelplatz/Heimnetz: nicht.
- Challenge: 32 Byte, einmalig, 5 Minuten, serverseitig gespeichert und an Konto und laufenden Schritt gebunden
  (Code-Cookie beim Anmelden, Sitzung beim Einrichten). Nach Gebrauch oder Fehlschlag weg.
- Prüfung durch py_webauthn (``require_user_verification=True``: Hello-PIN bzw. Biometrie); Signaturzähler, der nicht
  steigt (und nicht 0 ist), wird abgelehnt, der Passkey als „auffällig“ markiert und der Betreiber bekommt einen Hinweis.
- In der Datenbank liegen nur Credential-ID, öffentlicher Schlüssel und Zähler (``models.Passkey``); die zufällige
  Nutzerkennung (``user.id`` für WebAuthn, nicht die Konto-ID) in server_meta.
"""
from __future__ import annotations

import base64
import hashlib
import json
import logging
import secrets
import time
from urllib.parse import urlsplit

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.aliasse import zeichen_bereinigen
from app.db import utcnow
from app.einstellungen import angaben, meta_lesen, meta_schreiben
from app.models import Passkey, ServerMeta, User

log = logging.getLogger("passkeys")

HOECHSTENS_JE_KONTO = 10
CHALLENGE_SEKUNDEN = 5 * 60
NAME_ZEICHEN = 40
EINRICHTEN_JE_STUNDE = 10


class PasskeyFehler(Exception):
    """Verständliche Meldung (Schlüssel für die Übersetzung)."""


def _b64(daten: bytes) -> str:
    return base64.urlsafe_b64encode(daten).decode().rstrip("=")


def _unb64(text: str) -> bytes:
    text = (text or "").strip()
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


# ---------------------------------------------------------------- Herkunft
def herkunft(db: Session) -> tuple[str, str] | None:
    """(RP-ID, erwartete Herkunft) aus der eingetragenen öffentlichen Adresse – nur https, sonst None."""
    url = (angaben(db).public_url or "").strip().rstrip("/")
    teile = urlsplit(url)
    if teile.scheme != "https" or not teile.hostname:
        return None
    host = teile.hostname.lower()
    origin = f"https://{host}" + (f":{teile.port}" if teile.port and teile.port != 443 else "")
    return host, origin


def verfuegbar(db: Session) -> bool:
    """Gibt es auf diesem Server Passkeys? Nur mit eingetragener https-Adresse, nie am Einzelplatz."""
    from app.config import get_settings

    return herkunft(db) is not None and not get_settings().taleward_einzelplatz


# ---------------------------------------------------------------- Konto
def _handle(db: Session, user: User) -> bytes:
    """Zufällige WebAuthn-Nutzerkennung je Konto (nicht die Konto-ID), beim ersten Bedarf angelegt."""
    schluessel = f"passkey.handle.{user.id}"
    wert = meta_lesen(db, schluessel)
    if not wert:
        wert = _b64(secrets.token_bytes(32))
        meta_schreiben(db, schluessel, wert)
    return _unb64(wert)


def liste(db: Session, user_id: str) -> list[Passkey]:
    return list(db.scalars(select(Passkey).where(Passkey.user_id == user_id).order_by(Passkey.created_at)))


def vorhanden(db: Session, user_id: str) -> bool:
    return db.scalar(select(Passkey.id).where(Passkey.user_id == user_id).limit(1)) is not None


def passend(db: Session, user_id: str) -> list[Passkey]:
    """Passkeys dieses Kontos, die zur heutigen RP-ID passen (Adresse geändert → bleiben in der Liste, gelten nicht)."""
    h = herkunft(db)
    return [p for p in liste(db, user_id) if h is not None and p.rp_id == h[0]]


def anzeige(db: Session, user_id: str, sprache: str = "de") -> list[dict]:
    """Für die Liste in der Verwaltung und die Datenauskunft: Name, angelegt, zuletzt benutzt – ohne Schlüssel."""
    h = herkunft(db)
    aus = []
    for p in liste(db, user_id):
        aus.append({"id": p.id, "name": p.name, "angelegt": p.created_at, "zuletzt": p.last_used_at,
                    "passt": h is not None and p.rp_id == h[0], "auffaellig": p.auffaellig})
    return aus


# ---------------------------------------------------------------- Challenge
def _challenge_schluessel(user_id: str, art: str, bindung: str) -> str:
    return f"passkey.challenge.{art}.{hashlib.sha256(f'{user_id}:{bindung}'.encode()).hexdigest()[:32]}"


def _challenge_merken(db: Session, user: User, art: str, bindung: str) -> bytes:
    """Neue Challenge für genau diesen Schritt; eine ältere für denselben Schritt gilt damit nicht mehr."""
    challenge = secrets.token_bytes(32)
    meta_schreiben(db, _challenge_schluessel(user.id, art, bindung),
                   json.dumps({"c": _b64(challenge), "u": user.id, "bis": time.time() + CHALLENGE_SEKUNDEN}))
    return challenge


def _challenge_verbrauchen(db: Session, user: User, art: str, bindung: str) -> bytes | None:
    """Challenge holen und sofort löschen – gilt nur einmal, auch bei einem Fehlschlag danach."""
    schluessel = _challenge_schluessel(user.id, art, bindung)
    wert = meta_lesen(db, schluessel)
    row = db.get(ServerMeta, schluessel)
    if row is not None:
        db.delete(row)
        db.flush()
    try:
        d = json.loads(wert) if wert else None
    except ValueError:
        return None
    if not d or d.get("u") != user.id or d.get("bis", 0) < time.time():
        return None
    return _unb64(d["c"])


def aufraeumen(db: Session) -> int:
    """Wartung: abgelaufene Challenges weg."""
    n = 0
    for row in db.scalars(select(ServerMeta).where(ServerMeta.key.like("passkey.challenge.%"))):
        try:
            if json.loads(row.value or "{}").get("bis", 0) < time.time():
                db.delete(row)
                n += 1
        except ValueError:
            db.delete(row)
            n += 1
    return n


# ---------------------------------------------------------------- Einrichten
def registrierung_beginnen(db: Session, user: User, bindung: str) -> str:
    """Optionen für ``navigator.credentials.create`` als JSON. ``bindung``: Kennung der laufenden Sitzung."""
    from webauthn import generate_registration_options, options_to_json
    from webauthn.helpers.cose import COSEAlgorithmIdentifier
    from webauthn.helpers.structs import (AttestationConveyancePreference, AuthenticatorSelectionCriteria,
                                          PublicKeyCredentialDescriptor, ResidentKeyRequirement,
                                          UserVerificationRequirement)

    h = herkunft(db)
    if h is None or not verfuegbar(db):
        raise PasskeyFehler("passkey_keine_adresse")
    if len(liste(db, user.id)) >= HOECHSTENS_JE_KONTO:
        raise PasskeyFehler("passkey_zu_viele")
    rp_id, _origin = h
    optionen = generate_registration_options(
        rp_id=rp_id, rp_name=(angaben(db).server_name or "Taleward")[:64], user_id=_handle(db, user),
        user_name=user.username, user_display_name=(user.display_name or user.username)[:64],
        challenge=_challenge_merken(db, user, "create", bindung), timeout=120_000,
        attestation=AttestationConveyancePreference.NONE,
        authenticator_selection=AuthenticatorSelectionCriteria(
            resident_key=ResidentKeyRequirement.DISCOURAGED, user_verification=UserVerificationRequirement.REQUIRED),
        exclude_credentials=[PublicKeyCredentialDescriptor(id=_unb64(p.id)) for p in liste(db, user.id)],
        supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256, COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256])
    return options_to_json(optionen)


def registrierung_abschliessen(db: Session, user: User, bindung: str, antwort: str, name: str,
                               ua: str = "") -> Passkey:
    """Antwort des Browsers prüfen und den Passkey speichern. Wirft PasskeyFehler mit Übersetzungsschlüssel."""
    from webauthn import verify_registration_response
    from webauthn.helpers.cose import COSEAlgorithmIdentifier
    from webauthn.helpers.exceptions import WebAuthnException

    h = herkunft(db)
    challenge = _challenge_verbrauchen(db, user, "create", bindung)
    if h is None or not verfuegbar(db):
        raise PasskeyFehler("passkey_keine_adresse")
    if challenge is None:
        raise PasskeyFehler("passkey_abgelaufen")
    if len(liste(db, user.id)) >= HOECHSTENS_JE_KONTO:
        raise PasskeyFehler("passkey_zu_viele")
    rp_id, origin = h
    try:
        daten = json.loads(antwort) if isinstance(antwort, str) else antwort
        _client_data_pruefen(daten, "webauthn.create", origin)
        geprueft = verify_registration_response(
            credential=daten, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
            require_user_verification=True,
            supported_pub_key_algs=[COSEAlgorithmIdentifier.ECDSA_SHA_256,
                                    COSEAlgorithmIdentifier.RSASSA_PKCS1_v1_5_SHA_256])
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        log.info("Passkey-Einrichten abgelehnt: %s", type(e).__name__)
        raise PasskeyFehler("passkey_ungueltig") from None
    cid = _b64(geprueft.credential_id)
    if db.get(Passkey, cid) is not None:  # auch bei einem anderen Konto: datenbankweit eindeutig
        raise PasskeyFehler("passkey_ungueltig")
    transports = []
    try:
        transports = list((daten.get("response") or {}).get("transports") or [])
    except AttributeError:
        pass
    p = Passkey(id=cid, user_id=user.id, public_key=_b64(geprueft.credential_public_key),
                sign_count=int(geprueft.sign_count or 0), transports=json.dumps([str(t)[:20] for t in transports][:6]),
                rp_id=rp_id, name=name_bereinigen(name) or namensvorschlag(ua))
    db.add(p)
    db.flush()
    from app import konto_mail

    konto_mail.vormerken(db, user, "passkey_an")
    return p


def _client_data_pruefen(daten: dict, art: str, origin: str) -> None:
    """Pflicht laut Sicherheits-Chat, zusätzlich zur Bibliothek: type, origin, crossOrigin."""
    roh = (daten.get("response") or {}).get("clientDataJSON")
    client = json.loads(_unb64(roh).decode("utf-8"))
    if client.get("type") != art or client.get("origin") != origin or client.get("crossOrigin") is True:
        raise ValueError("clientData")


def name_bereinigen(name: str | None) -> str:
    return zeichen_bereinigen(name or "")[:NAME_ZEICHEN].strip()


def namensvorschlag(ua: str) -> str:
    """Grobe Bezeichnung aus dem User-Agent – nur als Vorschlag, der User-Agent selbst wird nicht gespeichert."""
    ua = ua or ""
    system = ("Windows" if "Windows" in ua else "Android" if "Android" in ua else "iPhone" if "iPhone" in ua
              else "iPad" if "iPad" in ua else "Mac" if "Mac OS" in ua or "Macintosh" in ua
              else "Linux" if "Linux" in ua else "")
    browser = ("Edge" if "Edg/" in ua else "Firefox" if "Firefox/" in ua else "Chrome" if "Chrome/" in ua
               else "Safari" if "Safari/" in ua else "")
    return " – ".join(x for x in (system, browser) if x) or "Passkey"


# ---------------------------------------------------------------- Anmelden
def anmeldung_beginnen(db: Session, user: User, bindung: str) -> str | None:
    """Optionen für ``navigator.credentials.get`` – nur nach richtigem Passwort (``allowCredentials`` verrät sonst,
    ob es Passkeys gibt). None, wenn das Konto keinen passenden Passkey hat."""
    from webauthn import generate_authentication_options, options_to_json
    from webauthn.helpers.structs import PublicKeyCredentialDescriptor, UserVerificationRequirement

    h = herkunft(db)
    schluessel = passend(db, user.id) if verfuegbar(db) else []
    if h is None or not schluessel:
        return None
    optionen = generate_authentication_options(
        rp_id=h[0], challenge=_challenge_merken(db, user, "get", bindung), timeout=120_000,
        allow_credentials=[PublicKeyCredentialDescriptor(id=_unb64(p.id)) for p in schluessel],
        user_verification=UserVerificationRequirement.REQUIRED)
    return options_to_json(optionen)


def anmeldung_pruefen(db: Session, user: User, bindung: str, antwort: str) -> bool:
    """Antwort des Browsers prüfen. True → Passkey gilt (zuletzt benutzt und Zähler nachgezogen)."""
    from webauthn import verify_authentication_response
    from webauthn.helpers.exceptions import WebAuthnException

    h = herkunft(db)
    challenge = _challenge_verbrauchen(db, user, "get", bindung)
    if h is None or challenge is None or not verfuegbar(db):
        return False
    rp_id, origin = h
    try:
        daten = json.loads(antwort) if isinstance(antwort, str) else antwort
        cid = _b64(_unb64(daten.get("rawId") or daten.get("id") or ""))
    except (ValueError, TypeError, AttributeError):
        return False
    p = db.get(Passkey, cid)
    if p is None or p.user_id != user.id or p.rp_id != rp_id:
        return False  # fremder oder unbekannter Passkey: wie ein falscher Code
    try:
        _client_data_pruefen(daten, "webauthn.get", origin)
        geprueft = verify_authentication_response(
            credential=daten, expected_challenge=challenge, expected_rp_id=rp_id, expected_origin=origin,
            credential_public_key=_unb64(p.public_key), credential_current_sign_count=p.sign_count,
            require_user_verification=True)
    except (WebAuthnException, ValueError, KeyError, TypeError) as e:
        log.info("Passkey-Anmeldung abgelehnt: %s", type(e).__name__)
        if type(e).__name__ == "InvalidAuthenticationResponse" and "sign count" in str(e).lower():
            _auffaellig(db, user, p)
        return False
    neu = int(geprueft.new_sign_count or 0)
    if p.sign_count and neu and neu <= p.sign_count:  # Zähler ging nicht weiter: geklonter Schlüssel?
        _auffaellig(db, user, p)
        return False
    p.sign_count = neu
    p.last_used_at = utcnow()
    return True


def _auffaellig(db: Session, user: User, p: Passkey) -> None:
    """Signaturzähler zurück: ablehnen, nicht löschen, in der Liste zeigen, Betreiber benachrichtigen."""
    from app import benachrichtigung

    if not p.auffaellig:
        p.auffaellig = True
        db.flush()
        try:
            benachrichtigung.senden(
                db, "Auffälliger Passkey",
                f"Beim Konto „{user.username}“ wurde ein Passkey („{p.name}“) mit zurückgesetztem Signaturzähler "
                "benutzt und abgelehnt. Das kann ein geklonter Schlüssel sein. Bitte mit der Person sprechen und den "
                "Passkey in der Verwaltung unter Anmeldung entfernen.")
        except Exception:  # noqa: BLE001 – ein Hinweis darf das Anmelden nie stören
            log.warning("Hinweis zum auffälligen Passkey nicht verschickt", exc_info=True)


# ---------------------------------------------------------------- Entfernen
def entfernen(db: Session, user: User, passkey_id: str) -> bool:
    p = db.get(Passkey, passkey_id)
    if p is None or p.user_id != user.id:
        return False
    db.delete(p)
    db.flush()
    from app import konto_mail
    from app.verwaltung import zweiter_faktor

    zweiter_faktor.faktor_fassung_erhoehen(db, user.id)  # S44: vertraute Geräte werden ungültig
    konto_mail.vormerken(db, user, "passkey_aus")
    return True


def alle_entfernen(db: Session, user_id: str) -> int:
    """„Zweiter Faktor aus“ und Konto löschen: alle Passkeys und die Nutzerkennung weg."""
    n = db.execute(delete(Passkey).where(Passkey.user_id == user_id)).rowcount
    for schluessel in (f"passkey.handle.{user_id}",):
        row = db.get(ServerMeta, schluessel)
        if row is not None:
            db.delete(row)
    return n
