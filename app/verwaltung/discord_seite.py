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


# ---------------------------------------------------------------- Rechtstexte des Bots (0.4.162)
def _bot_rechtstexte_seite(request: Request, user: User, db: Session, status: int = 200, entwurf: dict | None = None,
                           vorlage: str = "", **ctx) -> HTMLResponse:
    from app import rechtstexte, rechtstexte_bot

    texte = {}
    for art in rechtstexte_bot.ARTEN:
        for s in rechtstexte.SPRACHEN:
            if entwurf and art in entwurf:
                texte[(art, s)] = entwurf[art][s]
            elif vorlage == art:
                texte[(art, s)] = rechtstexte.vorlage(db, art, s)
            else:
                texte[(art, s)] = rechtstexte.text_oder_vorlage(db, art, s)
    antwort = _seite(request, "discord_rechtstexte.html", user, db, texte=texte,
                     stand={a: rechtstexte.stand_anzeige(rechtstexte.stand(db, a), "de") for a in rechtstexte_bot.ARTEN},
                     veroeffentlicht={a: rechtstexte.veroeffentlicht(db, a) for a in rechtstexte_bot.ARTEN},
                     pfade=rechtstexte_bot.PFADE, faktor=_faktor(db, user), **ctx)
    antwort.status_code = status
    return antwort


@router.get("/discord/rechtstexte", response_class=HTMLResponse)
def bot_rechtstexte(request: Request, vorlage: str = "", user: User = Depends(verwalter),
                    db: Session = Depends(get_db)):
    return _bot_rechtstexte_seite(request, user, db, vorlage=vorlage)


@router.post("/discord/rechtstexte", dependencies=[Depends(csrf_pruefen)])
def bot_rechtstexte_speichern(request: Request, art: str = Form(""), aktion: str = Form(""), text_de: str = Form(""),
                              text_en: str = Form(""), user: User = Depends(faktor_frisch),
                              db: Session = Depends(get_db)):
    """Veröffentlichen bzw. Zurückziehen nur mit zweitem Faktor (frisch) und CSRF. Mit offener [Klammer] geht nichts."""
    from app import rechtstexte, rechtstexte_bot
    from app.verwaltung.router import tr

    _ = tr(request)
    if not _faktor(db, user):
        return _ohne_faktor(request, user, db)
    if art not in rechtstexte_bot.ARTEN:
        return _bot_rechtstexte_seite(request, user, db, 400, fehler=_("Bitte einen der beiden Texte wählen."))
    if aktion == "zurueckziehen":
        rechtstexte.zurueckziehen(db, art)
        db.commit()
        return _zurueck(f"/discord/rechtstexte#{art}", "gespeichert")
    entwurf = {art: {"de": text_de, "en": text_en}}
    for s, inhalt in (("de", text_de), ("en", text_en)):
        sprache = _("Deutsch") if s == "de" else _("Englisch")
        if len(inhalt.strip()) < rechtstexte.MIN_TEXT:
            return _bot_rechtstexte_seite(request, user, db, 400, entwurf=entwurf, fehler_art=art,
                                          fehler=_("Der Text auf {sprache} fehlt oder ist zu kurz.", sprache=sprache))
        offen = rechtstexte.offene_stellen(inhalt)
        if offen:
            return _bot_rechtstexte_seite(request, user, db, 400, entwurf=entwurf, fehler_art=art, fehler=_(
                "Im Text auf {sprache} sind noch {n} Stellen in eckigen Klammern offen. Bitte ausfüllen oder entfernen.",
                sprache=sprache, n=offen))
    rechtstexte.speichern(db, art, text_de, text_en)
    db.commit()
    return _zurueck(f"/discord/rechtstexte#{art}", "gespeichert")


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
