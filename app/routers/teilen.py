"""Kapitel teilen als verschlüsselte Datei (Schnittstelle 0.4.30). Regeln in app/teilen.py."""
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app import errors, schemas, teilen
from app.access import current_user, membership
from app.db import get_db
from app.models import Campaign, GameSession, Recap, User

router = APIRouter()


@router.post("/campaigns/{campaignId}/shares", tags=["Chronik"], response_model=schemas.ShareOut)
def create_share(campaignId: str, body: schemas.ShareIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    me = membership(db, campaignId, user)
    if me is None or me.role != "gm":
        raise errors.not_found("campaign")  # Spielende sehen den Weg gar nicht
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch(f"teilen:{campaignId}", teilen.JE_STUNDE, 3600):
        raise errors.ApiError(429, "too_many_requests")
    s = db.get(GameSession, body.session_id)
    if s is None or s.campaign_id != campaignId:
        raise errors.not_found("session")
    if s.state != "published" or db.get(Recap, s.id) is None:
        raise errors.conflict("not_published")
    antwort = teilen.teilen(db, db.get(Campaign, campaignId), s)
    db.commit()  # nur aufgeräumte Schlüssel früherer Mitgliedschaften; die Datei selbst wird nicht gespeichert
    return antwort
