"""Bekannte Geräte (0.4.141): Die Hinweis-Mail „Neue Anmeldung bei deinem Konto“ kommt nur noch, wenn ein Browser zum
ersten Mal auftaucht (Benjamin 29.10.). Nach einer gelungenen Anmeldung in der Verwaltung oder auf der Guthaben-Seite
bekommt der Browser ein Cookie mit einem Zufallswert; der Server speichert nur dessen SHA-256 am Konto. Meldet sich
dasselbe Konto später mit diesem Cookie an, gibt es keine Mail. Ein fremdes Konto mit demselben Cookie zählt als neues
Gerät (der Wert gehört zum Konto). Höchstens MAX_JE_KONTO Geräte je Konto (das älteste fällt weg), nach GERAET_TAGE
Tagen ohne Anmeldung vergessen (Wartung). Die App bekommt diesen Weg erst mit einer Gerätekennung in der
Schnittstelle (App-Chat); bis dahin gilt dort höchstens eine Mail je 15 Minuten (app/konto_mail.py).
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import BekanntesGeraet, User

COOKIE = "tw_geraet"
GERAET_TAGE = 180
MAX_JE_KONTO = 20


def _hash(wert: str) -> str:
    return hashlib.sha256(wert.encode()).hexdigest()


def bekannt(request: Request, db: Session, user: User) -> bool:
    """Trägt der Browser ein Cookie, das zu diesem Konto gehört? Dann den Zeitpunkt nachziehen."""
    wert = request.cookies.get(COOKIE) or ""
    if not (16 <= len(wert) <= 200):
        return False
    g = db.get(BekanntesGeraet, _hash(wert))
    if g is None or g.user_id != user.id:
        return False
    g.last_seen_at = utcnow()
    return True


def merken(antwort: Response, request: Request, db: Session, user: User, ort: str) -> None:
    """Neues Gerät: Cookie setzen (nur dieser Browser, nur über https außerhalb des Heimnetzes) und am Konto vermerken."""
    wert = secrets.token_urlsafe(32)
    db.add(BekanntesGeraet(id=_hash(wert), user_id=user.id, ort=ort))
    bekannte = list(db.scalars(select(BekanntesGeraet).where(BekanntesGeraet.user_id == user.id)
                               .order_by(BekanntesGeraet.last_seen_at.desc())))
    for alt in bekannte[MAX_JE_KONTO - 1:]:  # Platz für das neue lassen
        db.delete(alt)
    antwort.set_cookie(COOKIE, wert, max_age=GERAET_TAGE * 86400, httponly=True, samesite="strict",
                       secure=request.url.scheme == "https", path="/")


def anmeldung(antwort: Response, request: Request, db: Session, user: User, ort: str) -> None:
    """Nach einer gelungenen Browser-Anmeldung: bekanntes Gerät → keine Mail; sonst Mail vormerken und Gerät merken."""
    from app import konto_mail

    if bekannt(request, db, user):
        return
    konto_mail.vormerken(db, user, "anmeldung", ort=ort)
    merken(antwort, request, db, user, ort)


_GERAET_KENNUNG = __import__("re").compile(r"^[A-Za-z0-9_-]{16,64}$")


def app_anmeldung(db: Session, user: User, device_id: str | None, ort: str | None, dienst: str | None = None) -> None:
    """Schnittstelle 0.4.27: Anmeldung aus der App mit freiwilliger Gerätekennung (deviceId). Gespeichert wird nur der
    SHA-256-Wert, gebunden an das Konto. Bekannt → keine Mail; neu → Mail vormerken und merken. Ohne oder mit ungültiger
    Kennung bleibt es beim bisherigen Weg (höchstens eine Mail je 15 Minuten, app/konto_mail.py)."""
    from app import konto_mail

    kennung = (device_id or "").strip()
    if not _GERAET_KENNUNG.match(kennung):
        konto_mail.vormerken(db, user, "anmeldung", ort=ort, dienst=dienst)
        return
    schluessel = _hash(f"app:{user.id}:{kennung}")
    g = db.get(BekanntesGeraet, schluessel)
    if g is not None and g.user_id == user.id:
        g.last_seen_at = utcnow()
        return
    konto_mail.vormerken(db, user, "anmeldung", ort=ort, dienst=dienst)
    db.add(BekanntesGeraet(id=schluessel, user_id=user.id, ort="app"))
    db.flush()
    bekannte = list(db.scalars(select(BekanntesGeraet).where(BekanntesGeraet.user_id == user.id)
                               .order_by(BekanntesGeraet.last_seen_at.desc())))
    for alt in bekannte[MAX_JE_KONTO:]:
        db.delete(alt)


def vergessen(db: Session, user_id: str) -> int:
    """Alle bekannten Geräte eines Kontos vergessen (z. B. nach einem Passwortwechsel oder auf Wunsch)."""
    return db.execute(delete(BekanntesGeraet).where(BekanntesGeraet.user_id == user_id)).rowcount


def aufraeumen(db: Session) -> int:
    """Wartung: Geräte ohne Anmeldung seit GERAET_TAGE Tagen vergessen."""
    grenze = utcnow() - timedelta(days=GERAET_TAGE)
    n = db.execute(delete(BekanntesGeraet).where(BekanntesGeraet.last_seen_at < grenze)).rowcount
    db.commit()
    return n
