"""Seite „Updates“: neue Fassungen von Server, App und Worker; Freigabe automatisch oder von Hand."""
from __future__ import annotations

import os

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import aktualisierung
from app.db import get_db
from app.einstellungen import angaben, meta_lesen
from app.models import User, Worker
from app.verwaltung.router import _seite, _zurueck, csrf_pruefen, verwalter

router = APIRouter(prefix="/verwaltung", include_in_schema=False)


@router.get("/updates", response_class=HTMLResponse)
def seite(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    from app.config import get_settings

    a = angaben(db)
    return _seite(request, "updates.html", user, db,
                  eigene=aktualisierung.eigene_fassung(), server_neu=aktualisierung.server_update(db),
                  server_stand=aktualisierung.stand(db, "server"), docker=bool(os.environ.get("TALEWARD_DOCKER")),
                  einzelplatz=get_settings().taleward_einzelplatz,
                  modus=aktualisierung.modus(db), zeilen=aktualisierung.uebersicht(db),
                  geprueft=_zeitpunkt(meta_lesen(db, "update.geprueft")), fehler_pruefung=meta_lesen(db, "update.fehler"),
                  pruefung_an=get_settings().update_check,
                  app_von_hand=bool(a.app_latest_version or a.app_download_url),
                  server_auto=aktualisierung.server_auto(), server_status=aktualisierung.server_status(),
                  server_jetzt=aktualisierung.server_jetzt_angefordert(),
                  status_zeit=_zeitpunkt((aktualisierung.server_status() or {}).get("zeit")),
                  hat_worker=db.scalar(select(func.count()).select_from(Worker)) > 0)


def _zeitpunkt(wert: str | None):
    from datetime import datetime, timezone

    if not wert:
        return None
    z = datetime.fromisoformat(wert)
    return z if z.tzinfo else z.replace(tzinfo=timezone.utc)


@router.post("/updates/pruefen", dependencies=[Depends(csrf_pruefen)])
def pruefen(user: User = Depends(verwalter), db: Session = Depends(get_db)):
    aktualisierung.pruefen(db)
    return _zurueck("/updates", "updates_geprueft")


def _nicht_im_einzelplatz() -> None:
    """Einzelplatz: Server und Worker aktualisiert das Programm selbst (gemeinsam, mit Rückweg) – nie diese Seite."""
    from fastapi import HTTPException

    from app.config import get_settings

    if get_settings().taleward_einzelplatz:
        raise HTTPException(404)


@router.post("/updates/server-auto", dependencies=[Depends(csrf_pruefen), Depends(_nicht_im_einzelplatz)])
async def server_auto(request: Request, user: User = Depends(verwalter)):
    form = await request.form()
    aktualisierung.server_auto_setzen(form.get("an") == "an")
    return _zurueck("/updates", "gespeichert")


@router.post("/updates/server-jetzt", dependencies=[Depends(csrf_pruefen), Depends(_nicht_im_einzelplatz)])
def server_jetzt(user: User = Depends(verwalter)):
    aktualisierung.server_jetzt()
    return _zurueck("/updates", "server_jetzt")


@router.post("/updates/modus", dependencies=[Depends(csrf_pruefen)])
async def modus(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    form = await request.form()
    aktualisierung.modus_setzen(db, str(form.get("modus", "automatisch")))
    return _zurueck("/updates", "gespeichert")


@router.post("/updates/freigeben", dependencies=[Depends(csrf_pruefen)])
async def freigeben(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    form = await request.form()
    art, version = str(form.get("art", "")), str(form.get("version", ""))
    if art not in aktualisierung.ARTEN or not aktualisierung.freigeben(db, art, version):
        return _zurueck("/updates")
    return _zurueck("/updates", "freigegeben")
