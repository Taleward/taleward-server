"""Discord-Bot (Schnittstelle 0.4.32): Wege der App (/discord-links, /campaigns/{id}/discord, /consent-links) und des
Bots (/bot/…). Regeln in app/discord_bot.py."""
from datetime import datetime

from fastapi import APIRouter, Depends, Request, Response
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import ConfigDict, Field
from pydantic.alias_generators import to_camel
from sqlalchemy.orm import Session

from app import begrenzung, errors
from app import discord_bot as db_
from app.access import current_user, membership
from app.db import get_db
from app.models import BotKey, Campaign, User
from app.schemas import ApiModel

router = APIRouter()
_bearer = HTTPBearer(auto_error=False)
SNOW = r"^[0-9]{15,21}$"


class _Streng(ApiModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


class LinkGeheimnis(_Streng):
    token: str | None = Field(default=None, pattern=r"^[A-Za-z0-9_-]{20,128}$")
    code: str | None = Field(default=None, max_length=64)


class LinkEinloesen(LinkGeheimnis):
    campaign_id: str = Field(max_length=64)


class BotLinkIn(_Streng):
    guild_id: str = Field(pattern=SNOW)
    discord_user_id: str = Field(pattern=SNOW)
    guild_name: str = Field(max_length=100)


class BereitIn(_Streng):
    discord_user_ids: list[str] = Field(min_length=1, max_length=25)
    session_id: str | None = Field(default=None, max_length=64)


class RundeIn(_Streng):
    guild_id: str = Field(pattern=SNOW)
    discord_user_id: str = Field(pattern=SNOW)
    started_at: datetime


class PersonIn(_Streng):
    discord_user_id: str = Field(pattern=SNOW)


class ZustimmungslinkIn(PersonIn):
    display_name: str | None = Field(default=None, max_length=40)


class EreignisIn(_Streng):
    type: str = Field(pattern=r"^(pause|resume|interrupted|stopped|discarded)$")
    at: datetime


class MerkenIn(_Streng):
    discord_user_id: str = Field(pattern=SNOW)
    at: datetime
    note: str | None = Field(default=None, max_length=280)


class ZustimmenIn(_Streng):
    consent: bool
    guest_name: str | None = Field(default=None, max_length=40)
    character_name: str | None = Field(default=None, max_length=60)


def _an(db: Session) -> None:
    if not db_.an(db):
        raise errors.not_found()


def bot_schluessel(request: Request, db: Session = Depends(get_db)) -> BotKey:
    """Nur mit X-Taleward-Bot; ein Nutzer-Token auf /bot/… → 401 (B1). Schalter aus → 404."""
    _an(db)
    roh = request.headers.get("x-taleward-bot")
    if not roh or request.headers.get("authorization"):
        raise errors.UNAUTHENTICATED
    k = db_.schluessel_pruefen(db, roh)
    if k is None:
        raise errors.UNAUTHENTICATED
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch(f"bot-schluessel:{k.id}", db_.SCHLUESSEL_JE_MINUTE, 60):
        raise errors.ApiError(429, "too_many_requests")
    return k


def _basis(request: Request, db: Session) -> str:
    from app.einstellungen import oeffentliche_adresse

    return oeffentliche_adresse(db, request)


def _attendee_out(a) -> dict:
    from app.services import attendee_out

    d = attendee_out(a).model_dump(mode="json", by_alias=True)
    return {k: v for k, v in d.items() if v is not None or k in ("consentAt", "characterName")}


# ---------------------------------------------------------------- App: Verbinden
@router.post("/discord-links/preview", tags=["Discord"])
def discord_link_preview(body: LinkGeheimnis, request: Request, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    _an(db)
    db_.link_versuch(request, user)
    antwort = db_.link_vorschau(db, user, body.token, body.code)
    db.commit()
    return antwort


@router.post("/discord-links/redeem", tags=["Discord"])
def discord_link_redeem(body: LinkEinloesen, request: Request, user: User = Depends(current_user),
                        db: Session = Depends(get_db)):
    _an(db)
    db_.link_versuch(request, user)
    v = db_.link_einloesen(db, user, body.token, body.code, body.campaign_id)
    db.commit()
    return db_.verbindung_out(db, v)


def _sl(db: Session, campaign_id: str, user: User) -> Campaign:
    me = membership(db, campaign_id, user)
    if me is None or me.role != "gm":
        raise errors.not_found("campaign")
    return db.get(Campaign, campaign_id)


@router.get("/campaigns/{campaignId}/discord", tags=["Discord"])
def campaign_discord(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _sl(db, campaignId, user)
    v = db_.verbindung(db, c)
    db.commit()
    return {"connection": db_.verbindung_out(db, v) if v else None}


@router.get("/campaigns/{campaignId}/discord/log", tags=["Discord"])
def campaign_discord_log(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """0.4.33: Verlauf der Discord-Anbindung, nur SL (sonst 404); auch bei ausgeschaltetem Bot lesbar."""
    c = _sl(db, campaignId, user)
    return {"entries": db_.verlauf(db, c)}


@router.delete("/campaigns/{campaignId}/discord", tags=["Discord"], status_code=204)
def campaign_discord_delete(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c = _sl(db, campaignId, user)
    from app.models import DiscordConnection

    v = db.get(DiscordConnection, c.id)
    if v is not None:
        db_.loesen(db, v)
    db.commit()
    return Response(status_code=204)


# ---------------------------------------------------------------- Zustimmungslinks (auch ohne Anmeldung)
def _adresse_begrenzen(request: Request) -> None:
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch(f"zustimmungslink:{begrenzung.adresse(request)}", 30, 900):
        raise errors.ApiError(429, "too_many_requests")


@router.get("/consent-links/{token}", tags=["Discord"])
def consent_link_get(token: str, request: Request, db: Session = Depends(get_db)):
    _an(db)
    _adresse_begrenzen(request)
    antwort = db_.zustimmungslink_ansehen(db, token[:128])
    db.commit()
    return antwort


@router.post("/consent-links/{token}", tags=["Discord"])
def consent_link_post(token: str, body: ZustimmenIn, request: Request,
                      creds: HTTPAuthorizationCredentials | None = Depends(_bearer), db: Session = Depends(get_db)):
    _an(db)
    _adresse_begrenzen(request)
    if body.consent is not True:
        raise errors.bad_request("validation_error")
    user = None
    if creds is not None:
        user = current_user(request, creds, db)  # ungültiges Token → 401 wie überall
    a = db_.zustimmen(db, token[:128], user, body.guest_name, body.character_name)
    db.commit()
    db.refresh(a)
    return _attendee_out(a)


# ---------------------------------------------------------------- Bot
@router.post("/bot/links", tags=["Discord"], status_code=201)
def bot_links(body: BotLinkIn, request: Request, k: BotKey = Depends(bot_schluessel), db: Session = Depends(get_db)):
    antwort = db_.link_anlegen(db, k, body.guild_id, body.discord_user_id, body.guild_name, _basis(request, db))
    db.commit()
    return antwort


@router.get("/bot/guilds/{guildId}/campaigns", tags=["Discord"])
def bot_guild_campaigns(guildId: str, discordUserId: str, k: BotKey = Depends(bot_schluessel),
                        db: Session = Depends(get_db)):
    antwort = db_.kampagnen_der_gilde(db, k, guildId, discordUserId)
    db.commit()
    return {"campaigns": antwort}


@router.delete("/bot/guilds/{guildId}/campaigns/{campaignId}", tags=["Discord"], status_code=204)
def bot_guild_unlink(guildId: str, campaignId: str, discordUserId: str, k: BotKey = Depends(bot_schluessel),
                     db: Session = Depends(get_db)):
    db_.bot_loesen(db, k, guildId, campaignId, discordUserId)
    db.commit()
    return Response(status_code=204)


@router.post("/bot/campaigns/{campaignId}/readiness", tags=["Discord"])
def bot_readiness(campaignId: str, body: BereitIn, k: BotKey = Depends(bot_schluessel), db: Session = Depends(get_db)):
    antwort = db_.bereitschaft(db, k, campaignId, body.discord_user_ids, body.session_id)
    db.commit()
    return antwort


@router.post("/bot/campaigns/{campaignId}/sessions", tags=["Discord"], status_code=201)
def bot_session(campaignId: str, body: RundeIn, k: BotKey = Depends(bot_schluessel), db: Session = Depends(get_db)):
    from app import spuren

    schluessel = spuren.track_key(db)  # 0.4.34 (S65): vorher, damit ohne lesbaren Schlüssel keine Runde entsteht
    s = db_.runde_anlegen(db, k, campaignId, body.guild_id, body.discord_user_id, body.started_at)
    db.commit()
    return {"sessionId": s.id, "number": s.number, "trackKey": schluessel}


@router.post("/bot/sessions/{sessionId}/attendees", tags=["Discord"])
def bot_attendee(sessionId: str, body: PersonIn, k: BotKey = Depends(bot_schluessel), db: Session = Depends(get_db)):
    a = db_.aufnehmen(db, k, sessionId, body.discord_user_id)
    db.commit()
    return {"attendeeId": a.id}


@router.post("/bot/sessions/{sessionId}/consent-links", tags=["Discord"], status_code=201)
def bot_consent_link(sessionId: str, body: ZustimmungslinkIn, request: Request, k: BotKey = Depends(bot_schluessel),
                     db: Session = Depends(get_db)):
    antwort = db_.zustimmungslink(db, k, sessionId, body.discord_user_id, body.display_name, _basis(request, db))
    db.commit()
    return antwort


@router.post("/bot/sessions/{sessionId}/events", tags=["Discord"], status_code=204)
def bot_event(sessionId: str, body: EreignisIn, k: BotKey = Depends(bot_schluessel), db: Session = Depends(get_db)):
    db_.ereignis(db, k, sessionId, body.type, body.at)
    db.commit()
    return Response(status_code=204)


@router.post("/bot/sessions/{sessionId}/bookmarks", tags=["Discord"], status_code=201)
def bot_bookmark(sessionId: str, body: MerkenIn, k: BotKey = Depends(bot_schluessel), db: Session = Depends(get_db)):
    """0.4.35: `/taleward merken` – Stelle für ein Mitglied der laufenden Bot-Runde (Regeln in discord_bot.merken)."""
    antwort = db_.merken(db, k, sessionId, body.discord_user_id, body.at, body.note)
    db.commit()
    return antwort


@router.delete("/bot/sessions/{sessionId}/tracks/{attendeeId}", tags=["Discord"], status_code=204)
def bot_track_delete(sessionId: str, attendeeId: str, k: BotKey = Depends(bot_schluessel),
                     db: Session = Depends(get_db)):
    db_.bot_spur_verwerfen(db, k, sessionId, attendeeId)
    db.commit()
    return Response(status_code=204)
