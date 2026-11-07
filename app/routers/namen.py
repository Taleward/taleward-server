"""Namen einmal berichtigen (Schnittstelle 0.4.31). Regeln in app/namen_berichtigen.py."""
from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app import errors
from app import namen_berichtigen as nb
from app.access import current_user, membership
from app.db import get_db
from app.models import Campaign, NameFix, User
from app.schemas import ApiModel

router = APIRouter()


class NameFixIn(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")
    from_: str = Field(alias="from", max_length=200)
    to: str = Field(max_length=200)


class NameFixAusfuehren(NameFixIn):
    preview_version: str = Field(max_length=100)
    skip_hit_ids: list[str] = Field(default=[], max_length=5000)
    rule: bool = True


def _sl(db: Session, campaign_id: str, user: User) -> tuple[Campaign, object]:
    me = membership(db, campaign_id, user)
    if me is None or me.role != "gm":
        raise errors.not_found("campaign")
    return db.get(Campaign, campaign_id), me


def _fix_fuer_sl(db: Session, fix_id: str, user: User) -> tuple[Campaign, NameFix]:
    fix = db.get(NameFix, fix_id[:64])
    if fix is None:
        raise errors.not_found()
    me = membership(db, fix.campaign_id, user)
    if me is None or me.role != "gm":
        raise errors.not_found()
    return db.get(Campaign, fix.campaign_id), fix


def _lang(request: Request) -> str:
    return errors.sprache(request)


@router.post("/campaigns/{campaignId}/name-fixes/preview", tags=["Namen"])
def name_fix_preview(campaignId: str, body: NameFixIn, request: Request, user: User = Depends(current_user),
                     db: Session = Depends(get_db)):
    c, me = _sl(db, campaignId, user)
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch(f"namen-vorschau:{user.id}", nb.VORSCHAU_JE_STUNDE, 3600):
        raise errors.ApiError(429, "too_many_requests")
    ansicht, _orte = nb.vorschau(db, c, me, body.from_, body.to, _lang(request))
    db.rollback()  # die Vorschau ändert nichts
    return ansicht


@router.get("/campaigns/{campaignId}/name-fixes", tags=["Namen"])
def name_fix_list(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c, _me = _sl(db, campaignId, user)
    return {"fixes": [nb.ausgeben(f) for f in nb.liste(db, c)]}


@router.post("/campaigns/{campaignId}/name-fixes", tags=["Namen"], status_code=201)
def name_fix_create(campaignId: str, body: NameFixAusfuehren, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    c, me = _sl(db, campaignId, user)
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch(f"namen-berichtigen:{campaignId}", nb.BERICHTIGEN_JE_STUNDE, 3600):
        raise errors.ApiError(429, "too_many_requests")
    fix = nb.berichtigen(db, c, me, body.from_, body.to, body.preview_version, body.skip_hit_ids, body.rule)
    db.commit()
    return JSONResponse(status_code=201, content=nb.ausgeben(fix))


@router.delete("/name-fixes/{fixId}", tags=["Namen"], status_code=204)
def name_fix_delete(fixId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    _c, fix = _fix_fuer_sl(db, fixId, user)
    fix.rule = False
    db.commit()


@router.post("/name-fixes/{fixId}/undo", tags=["Namen"])
def name_fix_undo(fixId: str, request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c, fix = _fix_fuer_sl(db, fixId, user)
    antwort = nb.rueckgaengig(db, c, fix, _lang(request))
    db.commit()
    return antwort
