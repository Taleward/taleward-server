"""Läuft gerade etwas, das ein Neustart unterbrechen würde? (Updates im Einzelplatz-Betrieb, ab 0.4.97)

Das Taleward-Programm hält den Server für ein Update kurz an. Vorher fragt es hier nach (`GET /worker/v1/busy`, mit
dem Schlüssel seines Workers). Gezählt wird nur, was ein Neustart wirklich stört:
- ein Auftrag in Arbeit (Worker oder Cloud), dessen Frist noch läuft;
- ein Hochladen, an dem sich in der letzten halben Stunde etwas getan hat (verlassene Uploads blockieren nicht);
- eine Runde mitten in der Verarbeitung (Stand höchstens drei Stunden alt – hängengebliebene zählen nicht).
Wartende Aufträge stören nicht: Sie liegen in der Datenbank und werden nach dem Neustart abgeholt.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import storage
from app.db import utcnow
from app.models import GameSession, Job, Upload

HOCHLADEN_RUHIG_NACH = timedelta(minutes=30)
VERARBEITUNG_HOECHSTENS = timedelta(hours=3)
IN_ARBEIT = ("transcribing", "summarizing", "processing")


def _utc(t: datetime | None) -> datetime | None:
    if t is None:
        return None
    return t if t.tzinfo else t.replace(tzinfo=timezone.utc)


def _zuletzt(up: Upload) -> datetime:
    """Letzte Bewegung eines Uploads: das jüngste Teil auf der Platte, sonst der Beginn."""
    letzte = _utc(up.created_at) or utcnow()
    try:
        for datei in storage.upload_dir(up.id).rglob("*"):
            if datei.is_file():
                letzte = max(letzte, datetime.fromtimestamp(datei.stat().st_mtime, timezone.utc))
    except OSError:
        pass
    return letzte


def grund(db: Session) -> str | None:
    """None, wenn ein Neustart niemanden stört; sonst "job", "upload" oder "processing"."""
    jetzt = utcnow()
    if db.scalar(select(Job.id).where(Job.state == "leased", Job.lease_expires_at > jetzt).limit(1)):
        return "job"
    for up in db.scalars(select(Upload).where(Upload.state.in_(("open", "measuring")))):
        if jetzt - _zuletzt(up) < HOCHLADEN_RUHIG_NACH:
            return "upload"
    if db.scalar(select(GameSession.id).where(GameSession.state.in_(IN_ARBEIT),
                                              GameSession.state_updated_at > jetzt - VERARBEITUNG_HOECHSTENS).limit(1)):
        return "processing"
    return None
