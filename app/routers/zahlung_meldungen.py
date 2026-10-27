"""Meldungen der Zahlungsanbieter (Webhooks, 0.4.133, S28 G6). Nur diese zwei Wege, ohne weitere Funktionen.

- Abrechnung aus oder Anbieter nicht eingerichtet → 404.
- Signatur falsch oder fehlt → 400 ohne Einzelheiten, nichts gebucht.
- Sonst 200, auch für Ereignisse, die nichts bewirken (sonst wiederholt der Anbieter sie tagelang).
"""
from __future__ import annotations

import json

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app import zahlung
from app.db import get_db
from app.einstellungen import meta_lesen

router = APIRouter(prefix="/zahlung", include_in_schema=False)
HOECHSTENS_BYTES = 256 * 1024


async def _roh(request: Request) -> bytes | None:
    roh = await request.body()
    return roh if len(roh) <= HOECHSTENS_BYTES else None


@router.post("/stripe")
async def stripe(request: Request, db: Session = Depends(get_db)):
    if "stripe" not in zahlung.bereit(db):
        return Response(status_code=404)
    roh = await _roh(request)
    geheimnis = meta_lesen(db, "zahlung.stripe_webhook") or ""
    if roh is None or not zahlung.stripe_signatur_gueltig(roh, request.headers.get("stripe-signature", ""), geheimnis):
        return Response(status_code=400)
    try:
        ereignis = json.loads(roh)
    except ValueError:
        return Response(status_code=400)
    zahlung.stripe_meldung(db, ereignis)
    db.commit()
    return Response(status_code=200)


@router.post("/paypal")
async def paypal(request: Request, db: Session = Depends(get_db)):
    if "paypal" not in zahlung.bereit(db):
        return Response(status_code=404)
    roh = await _roh(request)
    try:
        ereignis = json.loads(roh) if roh is not None else None
    except ValueError:
        ereignis = None
    if not isinstance(ereignis, dict) or not zahlung.paypal_signatur_gueltig(
            db, {k.lower(): v for k, v in request.headers.items()}, ereignis):
        return Response(status_code=400)
    zahlung.paypal_meldung(db, ereignis)
    db.commit()
    return Response(status_code=200)
