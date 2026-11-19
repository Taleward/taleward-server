"""Meldungen der Zahlungsanbieter (Webhooks, 0.4.133). Nur diese zwei Wege, ohne weitere Funktionen.

- Abrechnung aus oder Anbieter nicht eingerichtet → 404.
- Signatur falsch oder fehlt → 400 ohne Einzelheiten, nichts gebucht.
- PayPal: höchstens 60 Meldungen je Minute (sonst 429, ohne Anfrage bei PayPal).
- Sonst 200, auch für Ereignisse, die nichts bewirken (sonst wiederholt der Anbieter sie tagelang).
"""
from __future__ import annotations

import json

from fastapi.concurrency import run_in_threadpool
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app import zahlung
from app.db import get_db
from app.einstellungen import meta_lesen

router = APIRouter(prefix="/zahlung", include_in_schema=False)
HOECHSTENS_BYTES = 256 * 1024
# 0.4.136: Jede Meldung an /zahlung/paypal kostet zwei Anfragen bei PayPal – serverweit begrenzt
PAYPAL_JE_MINUTE = 60


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
    await run_in_threadpool(_buchen, zahlung.stripe_meldung, db, ereignis)
    return Response(status_code=200)


def _buchen(meldung, db: Session, ereignis: dict) -> None:
    meldung(db, ereignis)
    db.commit()


def _paypal_verarbeiten(db: Session, kopf: dict, ereignis: dict) -> bool:
    """Prüfung bei PayPal (Netz, bis 20 s) und Buchung – in einem eigenen Faden, nie im Ereignis-Takt (0.4.170)."""
    if not zahlung.paypal_signatur_gueltig(db, kopf, ereignis):
        return False
    zahlung.paypal_meldung(db, ereignis)
    db.commit()
    return True


@router.post("/paypal")
async def paypal(request: Request, db: Session = Depends(get_db)):
    if "paypal" not in zahlung.bereit(db):
        return Response(status_code=404)
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch("zahlung-paypal", PAYPAL_JE_MINUTE, 60):
        return Response(status_code=429)
    roh = await _roh(request)
    try:
        ereignis = json.loads(roh) if roh is not None else None
    except ValueError:
        ereignis = None
    if not isinstance(ereignis, dict) or not await run_in_threadpool(
            _paypal_verarbeiten, db, {k.lower(): v for k, v in request.headers.items()}, ereignis):
        return Response(status_code=400)
    return Response(status_code=200)
