"""Spielende tragen bei (Schnittstelle 0.4.28): Vorschläge für die Bibel, Merken, Zähler. Regeln in app/spielende.py."""
from typing import Literal

from fastapi import APIRouter, Depends, Query, Response
from sqlalchemy.orm import Session

from app import schemas, spielende
from app.access import current_user, require_member
from app.db import get_db
from app.models import Campaign, User

router = APIRouter()


@router.get("/campaigns/{campaignId}/player-proposals", tags=["Vorschläge"],
            response_model=list[schemas.PlayerProposalOut])
def list_player_proposals(campaignId: str, state: Literal["open", "all"] = Query(default="all"),
                          user: User = Depends(current_user), db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    return spielende.liste(db, db.get(Campaign, campaignId), me, state == "open")


@router.post("/campaigns/{campaignId}/player-proposals", tags=["Vorschläge"], status_code=201,
             response_model=schemas.PlayerProposalOut)
def create_player_proposal(campaignId: str, body: schemas.PlayerProposalIn, user: User = Depends(current_user),
                           db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    p = spielende.anlegen(db, db.get(Campaign, campaignId), me, body)
    db.commit()
    return spielende.vorschlag_out(db, p, False)


@router.delete("/player-proposals/{proposalId}", tags=["Vorschläge"], status_code=204)
def withdraw_player_proposal(proposalId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, me = spielende.laden(db, proposalId, user)
    spielende.zurueckziehen(db, p, me)
    db.commit()
    return Response(status_code=204)


@router.post("/player-proposals/{proposalId}/decision", tags=["Vorschläge"],
             response_model=schemas.PlayerProposalOut)
def decide_player_proposal(proposalId: str, body: schemas.PlayerProposalDecision,
                           user: User = Depends(current_user), db: Session = Depends(get_db)):
    p, me = spielende.laden(db, proposalId, user)
    spielende.entscheiden(db, p, me, body)
    db.commit()
    return spielende.vorschlag_out(db, p, True)


@router.get("/sessions/{sessionId}/bookmarks", tags=["Chronik"], response_model=list[schemas.BookmarkOut])
def list_bookmarks(sessionId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    s, me = spielende.runde_laden(db, sessionId, user, schreiben=False)
    return spielende.merkstellen(db, s, me)


@router.post("/sessions/{sessionId}/bookmarks", tags=["Chronik"], status_code=201, response_model=schemas.BookmarkOut)
def create_bookmark(sessionId: str, body: schemas.BookmarkIn | None = None, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    s, me = spielende.runde_laden(db, sessionId, user, schreiben=True)
    b = spielende.merken(db, s, me, body or schemas.BookmarkIn())
    db.commit()
    return next(x for x in spielende.merkstellen(db, s, me) if x.id == b.id)


@router.delete("/bookmarks/{bookmarkId}", tags=["Chronik"], status_code=204)
def delete_bookmark(bookmarkId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    spielende.merkstelle_loeschen(db, bookmarkId, user)
    db.commit()
    return Response(status_code=204)


@router.put("/campaigns/{campaignId}/members/me/counters", tags=["Charaktere"],
            response_model=list[schemas.CounterValue])
def put_counters(campaignId: str, body: schemas.CountersIn, user: User = Depends(current_user),
                 db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    aus = spielende.staende_setzen(db, db.get(Campaign, campaignId), me, body.values)
    db.commit()
    return aus
