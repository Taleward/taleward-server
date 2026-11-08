"""Verwaltung → Discord-Bot (0.4.32): Schalter und Bot-Schlüssel; dazu die öffentlichen Seiten der Links vom Bot
(/discord/<token>, /zustimmen/<token>), gebaut wie die Einladungsseite. Regeln in app/discord_bot.py."""
from __future__ import annotations

from urllib.parse import quote

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import discord_bot, errors
from app.db import get_db
from app.models import BotKey, User
from app.verwaltung.router import _seite, _zurueck, csrf_pruefen, faktor_frisch, verwalter

router = APIRouter(prefix="/verwaltung", include_in_schema=False)
seiten = APIRouter(include_in_schema=False)


def _faktor(db: Session, user: User) -> bool:
    from app.verwaltung import zweiter_faktor

    return zweiter_faktor.aktiv(db, user.id)


def _seite_discord(request: Request, user: User, db: Session, status: int = 200, **ctx) -> HTMLResponse:
    from app import spuren

    antwort = _seite(request, "discord.html", user, db, an=discord_bot.an(db),
                     schluessel=discord_bot.schluessel_liste(db), faktor=_faktor(db, user), neu=None,
                     spurschluessel=spuren.stand(db), **ctx)
    antwort.status_code = status
    return antwort


def _ohne_faktor(request: Request, user: User, db: Session) -> HTMLResponse:
    from app.verwaltung.router import tr

    return _seite_discord(request, user, db, 403, fehler=tr(request)(
        "Das geht nur mit eingeschaltetem zweiten Faktor (Anmeldung → Dein zweiter Faktor)."))


@router.get("/discord", response_class=HTMLResponse)
def seite(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    return _seite_discord(request, user, db)


@router.post("/discord/schalter", dependencies=[Depends(csrf_pruefen)])
def schalter(request: Request, an: str = Form(""), user: User = Depends(verwalter), db: Session = Depends(get_db)):
    if an == "1":  # Einschalten mit frischem zweiten Faktor; Ausschalten geht immer (Notbremse)
        if not _faktor(db, user):
            return _ohne_faktor(request, user, db)
        faktor_frisch(request, user, db)
    discord_bot.schalten(db, an == "1")
    db.commit()
    return _zurueck("/discord", "gespeichert")


@router.post("/discord/schluessel", dependencies=[Depends(csrf_pruefen)], response_class=HTMLResponse)
def schluessel_neu(request: Request, name: str = Form(""), user: User = Depends(faktor_frisch),
                   db: Session = Depends(get_db)):
    if not _faktor(db, user):
        return _ohne_faktor(request, user, db)
    klartext, k = discord_bot.schluessel_erzeugen(db, name, user.id)
    db.commit()
    return _seite_discord(request, user, db, neu_klartext=klartext, neu_name=k.name)


@router.post("/discord/schluessel/{key_id}/widerrufen", dependencies=[Depends(csrf_pruefen)])
def schluessel_weg(key_id: str, request: Request, user: User = Depends(faktor_frisch), db: Session = Depends(get_db)):
    if not _faktor(db, user):
        return _ohne_faktor(request, user, db)
    k = db.get(BotKey, key_id[:64])
    if k is None:
        raise errors.not_found()
    discord_bot.schluessel_widerrufen(db, k)
    db.commit()
    return _zurueck("/discord", "gespeichert")


@router.post("/discord/spurschluessel", dependencies=[Depends(csrf_pruefen)])
def spurschluessel_neu(request: Request, user: User = Depends(faktor_frisch), db: Session = Depends(get_db)):
    """0.4.159 (S65.3): Schlüssel für die Spuren des Bots erneuern – nur mit frischem zweitem Faktor."""
    from app import spuren

    if not _faktor(db, user):
        return _ohne_faktor(request, user, db)
    try:
        spuren.erneuern(db)
    except spuren.NichtLesbar:
        raise errors.ApiError(503, "track_key_unavailable") from None
    db.commit()
    return _zurueck("/discord", "gespeichert")


def _landeseite(request: Request, db: Session, art: str, token: str) -> HTMLResponse:
    from app import webapp
    from app.einstellungen import oeffentliche_adresse

    gueltig = discord_bot.an(db) and len(token) <= 128 and token.replace("-", "").replace("_", "").isalnum()
    basis = oeffentliche_adresse(db, request).rstrip("/")
    pfad = "discord" if art == "verbinden" else "zustimmen"
    link = f"{basis}/{pfad}/{token}"
    browser = None
    if gueltig:  # Web-App: #/link?url=<Link> (App-Chat 07.11.), die App nimmt das Token sofort aus der Adresse
        roh = webapp.browser_link(db, basis, link)
        browser = roh.replace("#/verbinden?invite=", "#/link?url=") if roh else None
    antwort = _seite(request, "discord_link.html", None, db, art=art, gueltig=gueltig,
                     app_link=f"taleward://{pfad}?url=" + quote(link, safe=""), browser_link=browser)
    if not gueltig:
        antwort.status_code = 404
    return antwort


@seiten.get("/discord/{token}", response_class=HTMLResponse)
def discord_landeseite(token: str, request: Request, db: Session = Depends(get_db)):
    """Verbindungslink vom Bot: führt nur in die App (dort entscheidet die Vorschau). Sagt nichts über den Link."""
    return _landeseite(request, db, "verbinden", token)


@seiten.get("/zustimmen/{token}", response_class=HTMLResponse)
def zustimmen_landeseite(token: str, request: Request, db: Session = Depends(get_db)):
    """Zustimmungslink vom Bot: in die App oder für Gäste ohne App in die Web-App. Sagt nichts über den Link."""
    return _landeseite(request, db, "zustimmen", token)
