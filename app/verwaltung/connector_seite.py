"""Verwaltung → Einstellungen → Connector (0.4.168): Schalter, Zustimmung zur Verbindung eines Assistenten, Freigabe
für Geld, Konten und Schlüssel, verbundene Assistenten, Protokoll. Regeln in app/connector.py."""
from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy.orm import Session

from app import connector, errors
from app.db import get_db
from app.models import User
from app.verwaltung.router import (NichtAngemeldet, _seite, _sitzungs_bindung, _zurueck, csrf_pruefen, faktor_frisch,
                                   sitzung, tr, verwalter)

router = APIRouter(prefix="/verwaltung", include_in_schema=False)


def nur_mensch(request: Request, db: Session = Depends(get_db)) -> None:
    """Diese Seiten gibt es erst nach dem Freischalten auf dem Server, und sie bedient nie der Connector selbst (er
    könnte sich sonst selbst freigeben)."""
    if not connector.sichtbar(db):
        raise errors.not_found()
    if connector.ist_connector_sitzung(_sitzungs_bindung(request)):
        raise errors.forbidden()


def _faktor(db: Session, user: User) -> bool:
    from app.verwaltung import zweiter_faktor

    return zweiter_faktor.aktiv(db, user.id)


def _zeit(sekunden: int | None) -> datetime | None:
    return datetime.fromtimestamp(sekunden, timezone.utc) if sekunden else None


def _zeigen(request: Request, user: User, db: Session, status: int = 200, **ctx) -> HTMLResponse:
    bis = connector.freigabe_bis(db)
    return _seite(request, "connector.html", user, db, status_code=status, an=connector.an(db),
                  hindernis=connector.hindernis(db), faktor=_faktor(db, user),
                  adresse=connector.mcp_adresse(db) if connector.basis(db) else None,
                  freigabe_bis=_zeit(bis), minuten=connector.FREIGABE_MINUTEN, netze=connector.netze(db),
                  zugaenge=[{**z, "seit": _zeit(z["seit"]), "zuletzt": _zeit(z["zuletzt"])}
                            for z in connector.zugaenge(db)],
                  protokoll=[{**p, "zeit": _zeit(p["zeit"])} for p in connector.protokoll_zeilen(db)], **ctx)


@router.get("/connector", response_class=HTMLResponse, dependencies=[Depends(nur_mensch)])
def seite(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    return _zeigen(request, user, db)


@router.post("/connector/schalter", dependencies=[Depends(csrf_pruefen), Depends(nur_mensch)])
def schalter(request: Request, an: str = Form(""), user: User = Depends(verwalter), db: Session = Depends(get_db)):
    if an == "1":  # Einschalten nur mit zweitem Faktor, frisch gezeigt; Ausschalten geht immer (Notbremse)
        if not _faktor(db, user) or connector.hindernis(db):
            return _zeigen(request, user, db, 403, fehler=tr(request)(
                "Der Connector lässt sich hier gerade nicht einschalten."))
        faktor_frisch(request, user, db)
        connector.einschalten(db)
    else:
        connector.ausschalten(db)
    db.commit()
    return _zurueck("/connector", "gespeichert")


@router.post("/connector/freigabe", dependencies=[Depends(csrf_pruefen), Depends(nur_mensch)])
def freigabe(request: Request, an: str = Form(""), user: User = Depends(verwalter), db: Session = Depends(get_db)):
    if an == "1":
        if not _faktor(db, user) or not connector.an(db):
            raise errors.forbidden()
        faktor_frisch(request, user, db)
        connector.freigeben(db, user)
    else:
        connector.freigabe_beenden(db)
    db.commit()
    return _zurueck("/connector#freigabe", "gespeichert")


@router.post("/connector/netze", dependencies=[Depends(csrf_pruefen), Depends(nur_mensch)])
def netze(request: Request, netze: str = Form(""), user: User = Depends(faktor_frisch), db: Session = Depends(get_db)):
    wert = connector.netze_pruefen(netze)
    if wert is None:
        return _zeigen(request, user, db, 400, fehler=tr(request)(
            "Eines der Netze stimmt nicht. Beispiel: 160.79.104.0/21"))
    connector.netze_schreiben(db, wert)
    db.commit()
    return _zurueck("/connector#netze", "gespeichert")


@router.post("/connector/trennen", dependencies=[Depends(csrf_pruefen), Depends(nur_mensch)])
def trennen(zugang: str = Form(""), user: User = Depends(verwalter), db: Session = Depends(get_db)):
    connector.trennen(db, zugang[:32])
    db.commit()
    return _zurueck("/connector#zugaenge", "gespeichert")


# ---------------------------------------------------------------- Zustimmung (der Assistent schickt den Browser her)
def _hinweis(request: Request, db: Session, user: User | None, grund: str, status: int = 400) -> HTMLResponse:
    return _seite(request, "connector_zustimmen.html", user, db, status_code=status, grund=grund, anfrage=None,
                  faktor=True)


@router.get("/connector/zustimmen", response_class=HTMLResponse, dependencies=[Depends(nur_mensch)])
def zustimmen_seite(request: Request, db: Session = Depends(get_db)):
    if not connector.an(db):
        raise errors.not_found()
    try:
        user = sitzung(request, db)
    except NichtAngemeldet:
        return _hinweis(request, db, None, "anmelden", 401)
    if user.setup_account:
        return _hinweis(request, db, None, "anmelden", 401)
    if request.query_params.get("client_id"):
        try:
            connector.offen_merken(db, user.id, connector.anfrage_pruefen(db, request.query_params))
            db.commit()
        except connector.Fehler as e:
            return _hinweis(request, db, user, e.kennung)
    anfrage = connector.offen(db, user.id)
    if anfrage is None:
        return _hinweis(request, db, user, "abgelaufen")
    from urllib.parse import urlsplit

    from app.verwaltung import zweiter_faktor

    return _seite(request, "connector_zustimmen.html", user, db, grund=None, anfrage=anfrage,
                  ziel=urlsplit(anfrage["uri"]).hostname, faktor=_faktor(db, user), minuten=connector.FREIGABE_MINUTEN,
                  frisch=zweiter_faktor.frisch(db, user.id, sitzung=_sitzungs_bindung(request)))


@router.post("/connector/zustimmen", response_class=HTMLResponse,
             dependencies=[Depends(csrf_pruefen), Depends(nur_mensch)])
def zustimmen(request: Request, antwort: str = Form(""), user: User = Depends(verwalter),
              db: Session = Depends(get_db)):
    anfrage = connector.offen(db, user.id) if connector.an(db) else None
    if anfrage is None:
        return _hinweis(request, db, user, "abgelaufen")
    if antwort != "ja":
        connector.offen_vergessen(db, user.id)
        db.commit()
        weiter = connector.rueckweg(anfrage, error="access_denied")
    else:
        if not _faktor(db, user):
            raise errors.forbidden()
        faktor_frisch(request, user, db)
        weiter = connector.zustimmen(db, user, anfrage)
        db.commit()
    # Kein Umleiten nach einem Formular (form-action 'self'): eine Seite, die von selbst weitergeht, mit Knopf
    return _seite(request, "connector_weiter.html", user, db, weiter=weiter, zugestimmt=antwort == "ja")
