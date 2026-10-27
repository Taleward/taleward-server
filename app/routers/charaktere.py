"""Charaktere (Schnittstelle 0.4.7): eigener Charakter, mitgebrachte Welt, Chronik, Prüfliste und Hinweise der SL.
Logik in app/charaktere.py."""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import charakterauswertung, charaktere, errors, schemas
from app.access import current_user, membership, require_member
from app.db import get_db
from app.errors import sprache
from app.models import GmNotice, Member, User
from app.routers.pruefung import proposal_out
from app.services import member_out

router = APIRouter(tags=["Charaktere"])


@router.put("/campaigns/{campaignId}/members/me/character", response_model=schemas.MemberOut,
            response_model_exclude_unset=True)
def put_character(campaignId: str, body: schemas.CharacterIn, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    charaktere.setzen(db, me, body)
    db.commit()
    db.refresh(me)
    return member_out(me, me)


@router.delete("/campaigns/{campaignId}/members/me/character", status_code=204)
def delete_character(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    charaktere.loesen(db, me)
    db.commit()
    return Response(status_code=204)


@router.get("/campaigns/{campaignId}/members/me/world", response_model=schemas.WorldOut)
def get_world(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    return schemas.WorldOut(entries=charaktere.stand(db, me))


@router.post("/campaigns/{campaignId}/members/me/world", response_model=schemas.WorldOut)
def post_world(campaignId: str, body: schemas.WorldIn, user: User = Depends(current_user),
               db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    aus = charaktere.einreichen(db, me, body.entries)
    db.commit()
    return schemas.WorldOut(entries=aus)


@router.get("/campaigns/{campaignId}/members/me/chronicle")
def get_chronicle(campaignId: str, request: Request, user: User = Depends(current_user),
                  db: Session = Depends(get_db)):
    """Auch nach dem Verlassen, solange das Konto besteht – deshalb ohne die Prüfung auf aktive Mitgliedschaft."""
    me = db.scalar(select(Member).where(Member.campaign_id == campaignId, Member.user_id == user.id))
    if me is None:
        raise errors.not_found("campaign")
    return charaktere.chronik(db, me, request)


@router.get("/campaigns/{campaignId}/character-proposals", tags=["Vorschläge"],
            response_model=list[schemas.ProposalOut], response_model_exclude_unset=True)
def character_proposals(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    if me.role != "gm":
        raise errors.not_found("campaign")  # Spieler: nicht einmal die Existenz der Liste
    return [proposal_out(p) for p in charaktere.vorschlaege(db, campaignId)]


# ---------- 0.4.18: Charakterbögen und Hintergründe auswerten (Logik in app/charakterauswertung.py)
@router.get("/campaigns/{campaignId}/character-analyses", response_model=list[schemas.CharacterAnalysisOut])
def character_analyses(campaignId: str, request: Request, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    """SL alle, Spieler nur eigene (ohne proposalCount). Fremd → 404."""
    me = require_member(db, campaignId, user)
    sl = me.role == "gm"
    return [charakterauswertung.out(db, a, sl, sprache(request), user.id)
            for a in charakterauswertung.liste(db, me.campaign, me)]


@router.post("/campaigns/{campaignId}/members/me/backstory-analysis", status_code=201,
             response_model=schemas.CharacterAnalysisOut)
def backstory_analysis(campaignId: str, request: Request, user: User = Depends(current_user),
                       db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    a = charakterauswertung.hintergrund_abgeben(db, me)
    db.commit()
    return charakterauswertung.out(db, a, me.role == "gm", sprache(request), user.id)


@router.post("/character-analyses/{analysisId}/decision", response_model=schemas.CharacterAnalysisOut)
def analysis_decision(analysisId: str, body: schemas.AnalysisDecisionIn, request: Request,
                      user: User = Depends(current_user), db: Session = Depends(get_db)):
    """Nur die SL; Spieler und Fremde → 404."""
    a, me = charakterauswertung.zu(db, analysisId, lambda cid: membership(db, cid, user))
    if me.role != "gm":
        raise errors.not_found("analysis")
    charakterauswertung.entscheiden(db, a, body.approve, user.id)
    db.commit()
    return charakterauswertung.out(db, a, True, sprache(request), user.id)


@router.delete("/campaigns/{campaignId}/gm-notices/{noticeId}", tags=["Kampagnen"], status_code=204)
def delete_notice(campaignId: str, noticeId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    me = require_member(db, campaignId, user)
    n = db.get(GmNotice, noticeId)
    if me.role != "gm" or n is None or n.campaign_id != campaignId:
        raise errors.not_found("notice")
    db.delete(n)
    db.commit()
    return Response(status_code=204)
