"""Aufladen des Guthabens über Stripe und PayPal (0.4.133, S28 G4, G6, G9, G11).

Ablauf:
1. Die SL wählt auf /guthaben einen der festen Beträge und den Zahlweg. Der Server legt einen Bezahlvorgang an
   (Guthaben, Betrag, Gebühr) und leitet auf die Bezahlseite des Anbieters weiter. Kartendaten tippt niemand in
   Taleward ein; an den Anbieter gehen nur Betrag und die Kennung des Vorgangs.
2. Gutgeschrieben wird nur, was der Anbieter bestätigt: bei Stripe über die signierte Meldung (Webhook), bei PayPal
   beim Rücksprung über die Abbuchung durch den Server bzw. die geprüfte Meldung. Betrag und Währung müssen genau zum
   Vorgang passen; sonst wird nichts gebucht und der Betreiber bekommt einen Hinweis.
3. Jeder Vorgang wird höchstens einmal gebucht (`ereignis_id` = „zahlung:<Vorgang>“, eindeutig in der Datenbank).
   Erstattungen und Rückbuchungen erzeugen Gegenbuchungen; bei einem Streitfall wird das Guthaben gesperrt, bis der
   Betreiber entscheidet.

Gebühr: wird aufgeschlagen und rückwärts gerechnet, damit genau der gewählte Betrag ankommt. Vor der Zahlung zeigt die
Seite beides. Kleine Abweichungen durch die tatsächliche Gebühr des Anbieters sind hinnehmbar.

Echtes Geld ist noch nicht freigegeben: Stripe nimmt nur Test-Schlüssel, PayPal läuft in der Sandbox, solange
`zahlung.live` nicht gesetzt ist (in der Verwaltung nicht einstellbar).
"""
from __future__ import annotations

import hashlib
import hmac
import json
import math
import time
from dataclasses import dataclass
from datetime import timedelta

import httpx
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app import abrechnung
from app.db import utcnow
from app.einstellungen import meta_lesen, meta_schreiben
from app.models import Bezahlvorgang, Buchung, Guthaben

ANBIETER = ("stripe", "paypal")
MINDEST_CENT, HOECHST_CENT = 1000, 50_000  # je Aufladung (Entscheidung Benjamin 27.10.: mindestens 10 €)
STANDARD_BETRAEGE = (1000, 2000, 5000)
JE_TAG_VORGAENGE, JE_TAG_CENT = 5, 30_000  # G11: je Konto und Tag (begonnen, nicht nur bezahlt)
STRIPE_API = "https://api.stripe.com"
PAYPAL_API = {"sandbox": "https://api-m.sandbox.paypal.com", "live": "https://api-m.paypal.com"}
STRIPE_TOLERANZ = 300  # Sekunden: so alt darf eine signierte Meldung sein

_client: httpx.Client | None = None  # in Tests ersetzt


def _c() -> httpx.Client:
    return _client or httpx.Client(timeout=httpx.Timeout(20.0, connect=5.0))


class ZahlungFehler(Exception):
    """Anbieter nicht erreichbar oder lehnt ab – für die Seite ein fester Satz, Einzelheiten nur im Protokoll."""


# ---------------------------------------------------------------- Einstellungen des Betreibers
@dataclass
class Gebuehren:
    """Gebühr je Anbieter: Prozent in Hundertstel (150 = 1,50 %) plus fester Betrag in Cent."""

    stripe_bp: int = 150
    stripe_fest: int = 25
    paypal_bp: int = 299
    paypal_fest: int = 39


def gebuehren(db: Session) -> Gebuehren:
    try:
        roh = json.loads(meta_lesen(db, "zahlung.gebuehren") or "{}")
    except ValueError:
        roh = {}
    g = Gebuehren()
    for name in ("stripe_bp", "stripe_fest", "paypal_bp", "paypal_fest"):
        try:
            setattr(g, name, max(0, min(2000 if name.endswith("bp") else 500, int(roh.get(name, getattr(g, name))))))
        except (TypeError, ValueError):
            pass
    return g


def betraege(db: Session) -> list[int]:
    """Feste Beträge zum Aufladen in Cent (Entscheidung Benjamin: Knöpfe, kein freies Feld)."""
    try:
        werte = [int(x) for x in (meta_lesen(db, "zahlung.betraege") or "").split(",") if x.strip()]
    except ValueError:
        werte = []
    werte = sorted({w for w in werte if MINDEST_CENT <= w <= HOECHST_CENT})
    return werte[:4] or list(STANDARD_BETRAEGE)


def live(db: Session) -> bool:
    from app.config import get_settings

    if get_settings().taleward_testinstanz:  # 0.4.137: Testinstanz nie mit echtem Geld
        return False
    return meta_lesen(db, "zahlung.live") == "1"


def stripe_schluessel_erlaubt(schluessel: str, db: Session) -> bool:
    if schluessel.startswith(("sk_test_", "rk_test_")):
        return True
    return live(db) and schluessel.startswith(("sk_live_", "rk_live_"))


def basis(db: Session) -> str | None:
    """Rücksprung-Adressen nur aus der eingetragenen öffentlichen Adresse (https, im Heimnetz auch http)."""
    from app.einstellungen import mail_adresse
    from app.routers.auth import oeffentliche_herkunft

    adresse = mail_adresse(db)
    return adresse if adresse and oeffentliche_herkunft(adresse) else None


def bereit(db: Session) -> list[str]:
    """Zahlwege, die eingerichtet sind (leer = Aufladen gibt es auf diesem Server nicht)."""
    if not abrechnung.an(db) or basis(db) is None:
        return []
    aus = []
    s = meta_lesen(db, "zahlung.stripe_schluessel") or ""
    if s and meta_lesen(db, "zahlung.stripe_webhook") and stripe_schluessel_erlaubt(s, db):
        aus.append("stripe")
    if meta_lesen(db, "zahlung.paypal_id") and meta_lesen(db, "zahlung.paypal_geheim") and \
            meta_lesen(db, "zahlung.paypal_webhook_id"):
        aus.append("paypal")
    return aus


def brutto(db: Session, anbieter: str, netto: int) -> int:
    """Rückwärts gerechnet: brutto − Gebühr(brutto) ≥ netto."""
    g = gebuehren(db)
    bp, fest = (g.stripe_bp, g.stripe_fest) if anbieter == "stripe" else (g.paypal_bp, g.paypal_fest)
    return int(math.ceil((netto + fest) * 10_000 / (10_000 - bp) - 1e-9))


def _euro_text(cent: int) -> str:
    return f"{cent // 100}.{cent % 100:02d}"


# ---------------------------------------------------------------- Starten
def heute(db: Session, user_id: str) -> tuple[int, int]:
    seit = utcnow() - timedelta(days=1)
    anzahl, summe = db.execute(select(func.count(), func.coalesce(func.sum(Bezahlvorgang.netto_cent), 0)).where(
        Bezahlvorgang.von == user_id, Bezahlvorgang.angelegt >= seit)).one()
    return int(anzahl or 0), int(summe or 0)


def starten(db: Session, g: Guthaben, user_id: str, anbieter: str, netto: int, sprache: str = "de", *,
            zugestimmt: bool = False) -> str:
    """Bezahlvorgang anlegen und die Adresse der Bezahlseite des Anbieters zurückgeben. ValueError bei unzulässigem
    Betrag oder Zahlweg, ohne Zustimmung zum sofortigen Beginn, ohne veröffentlichte Bedingungen (nur bei echtem Geld)
    bzw. über der Tagesgrenze, ZahlungFehler, wenn der Anbieter nicht will."""
    from app import rechtstexte

    if anbieter not in bereit(db) or netto not in betraege(db):
        raise ValueError("eingabe")
    if not zugestimmt:  # 0.4.138: Häkchen „sofort nutzbar“ (Widerruf) ist Pflicht
        raise ValueError("zustimmung")
    fassung = rechtstexte.fassung(db)
    if fassung is None and live(db):  # echtes Geld nur mit veröffentlichten Bedingungen und Widerrufsbelehrung
        raise ValueError("bedingungen")
    if g.gesperrt_at is not None:
        raise ValueError("gesperrt")
    anzahl, summe = heute(db, user_id)
    if anzahl >= JE_TAG_VORGAENGE or summe + netto > JE_TAG_CENT:
        raise ValueError("grenze")
    v = Bezahlvorgang(guthaben_id=g.id, von=user_id, anbieter=anbieter, netto_cent=netto,
                      brutto_cent=brutto(db, anbieter, netto), zustand="offen", zustimmung_at=utcnow(),
                      zustimmung_fassung=fassung or "unveroeffentlicht (Testmodus)")
    db.add(v)
    db.flush()
    zurueck = basis(db)
    try:
        if anbieter == "stripe":
            v.anbieter_ref, url = _stripe_sitzung(db, v, zurueck, sprache)
        else:
            v.anbieter_ref, url = _paypal_auftrag(db, v, zurueck, sprache)
    except (httpx.HTTPError, KeyError, ValueError, TypeError) as e:
        v.zustand = "fehlgeschlagen"
        abrechnung.protokoll(db, user_id, "zahlung_start_fehler", g.id, vorgang=v.id, anbieter=anbieter,
                             fehler=type(e).__name__)
        db.commit()
        raise ZahlungFehler(anbieter) from None
    abrechnung.protokoll(db, user_id, "zahlung_start", g.id, vorgang=v.id, anbieter=anbieter, netto_cent=netto,
                         brutto_cent=v.brutto_cent)
    db.commit()
    return url


def _stripe_kopf(db: Session, **mehr) -> dict:
    return {"Authorization": f"Bearer {meta_lesen(db, 'zahlung.stripe_schluessel')}", **mehr}


def _stripe_sitzung(db: Session, v: Bezahlvorgang, zurueck: str, sprache: str) -> tuple[str, str]:
    name = ("Taleward-Guthaben " if sprache == "de" else "Taleward credit ") + abrechnung.euro(v.netto_cent)
    daten = {
        "mode": "payment", "client_reference_id": v.id, "metadata[vorgang]": v.id,
        "payment_intent_data[metadata][vorgang]": v.id,
        "line_items[0][quantity]": "1", "line_items[0][price_data][currency]": "eur",
        "line_items[0][price_data][unit_amount]": str(v.brutto_cent),
        "line_items[0][price_data][product_data][name]": name,
        "success_url": f"{zurueck}/guthaben/aufladen/fertig?v={v.id}",
        "cancel_url": f"{zurueck}/guthaben/aufladen/abbruch?v={v.id}",
        "locale": "de" if sprache == "de" else "en",
        "expires_at": str(int(time.time()) + 60 * 60),
    }
    r = _c().post(f"{STRIPE_API}/v1/checkout/sessions", data=daten,
                  headers=_stripe_kopf(db, **{"Idempotency-Key": v.id}))
    r.raise_for_status()
    antwort = r.json()
    url = antwort["url"]
    if not str(url).startswith("https://checkout.stripe.com/"):
        raise ValueError("url")
    return antwort["id"], url


def _paypal_basis(db: Session) -> str:
    return PAYPAL_API["live" if live(db) else "sandbox"]


# 0.4.136 (S54): Zugangstoken von PayPal bis kurz vor Ablauf im Speicher (je Adresse und Zugang)
_TOKEN: dict[str, tuple[str, float]] = {}
TOKEN_PUFFER = 120  # Sekunden vor Ablauf neu holen


def _paypal_token(db: Session) -> str:
    kennung, geheim = meta_lesen(db, "zahlung.paypal_id") or "", meta_lesen(db, "zahlung.paypal_geheim") or ""
    schluessel = hashlib.sha256(f"{_paypal_basis(db)}|{kennung}|{geheim}".encode()).hexdigest()
    gemerkt = _TOKEN.get(schluessel)
    if gemerkt is not None and gemerkt[1] > time.monotonic():
        return gemerkt[0]
    r = _c().post(f"{_paypal_basis(db)}/v1/oauth2/token", data={"grant_type": "client_credentials"},
                  auth=(kennung, geheim))
    r.raise_for_status()
    daten = r.json()
    token = daten["access_token"]
    try:
        dauer = int(daten.get("expires_in") or 0)
    except (TypeError, ValueError):
        dauer = 0
    _TOKEN.clear()  # höchstens ein Zugang zugleich
    if dauer > TOKEN_PUFFER:
        _TOKEN[schluessel] = (token, time.monotonic() + dauer - TOKEN_PUFFER)
    return token


def _paypal_auftrag(db: Session, v: Bezahlvorgang, zurueck: str, sprache: str) -> tuple[str, str]:
    daten = {"intent": "CAPTURE", "purchase_units": [{
        "reference_id": v.id, "custom_id": v.id,
        "description": ("Taleward-Guthaben " if sprache == "de" else "Taleward credit ") + abrechnung.euro(v.netto_cent),
        "amount": {"currency_code": "EUR", "value": _euro_text(v.brutto_cent)}}],
        "payment_source": {"paypal": {"experience_context": {
            "brand_name": "Taleward", "shipping_preference": "NO_SHIPPING", "user_action": "PAY_NOW",
            "locale": "de-DE" if sprache == "de" else "en-GB",
            "return_url": f"{zurueck}/guthaben/aufladen/paypal?v={v.id}",
            "cancel_url": f"{zurueck}/guthaben/aufladen/abbruch?v={v.id}"}}}}
    r = _c().post(f"{_paypal_basis(db)}/v2/checkout/orders", json=daten,
                  headers={"Authorization": f"Bearer {_paypal_token(db)}", "PayPal-Request-Id": v.id})
    r.raise_for_status()
    antwort = r.json()
    url = next(x["href"] for x in antwort["links"] if x.get("rel") in ("payer-action", "approve"))
    if not str(url).startswith(("https://www.paypal.com/", "https://www.sandbox.paypal.com/")):
        raise ValueError("url")
    return antwort["id"], url


# ---------------------------------------------------------------- Gutschreiben
def _hinweis(db: Session, art: str, v: Bezahlvorgang | None, **daten) -> None:
    """Abweichung oder Streitfall: Protokoll und Benachrichtigung an den Betreiber (ohne Beträge in Push)."""
    abrechnung.protokoll(db, "system", art, v.guthaben_id if v else None, vorgang=v.id if v else None, **daten)
    try:
        from app import benachrichtigung

        benachrichtigung.melden(db, art, wichtig=True)
    except Exception:  # noqa: BLE001 – eine fehlende Benachrichtigung darf die Buchung nicht verhindern
        pass


def bezahlt(db: Session, v: Bezahlvorgang, brutto_cent: int, waehrung: str, zahlung_ref: str | None) -> bool:
    """Zahlung bestätigt: genau einmal den gewählten Betrag gutschreiben. False, wenn nichts gebucht wurde."""
    if waehrung.upper() != "EUR" or brutto_cent != v.brutto_cent:
        if v.zustand == "offen":
            v.zustand = "abweichung"
        _hinweis(db, "zahlung_abweichung", v, erwartet=v.brutto_cent, gemeldet=brutto_cent, waehrung=waehrung)
        return False
    if v.zustand == "bezahlt":
        return False
    g = db.get(Guthaben, v.guthaben_id)
    try:
        with db.begin_nested():
            b = abrechnung.buchen(db, g, "zahlung", v.netto_cent, ereignis_id=f"zahlung:{v.id}")
    except IntegrityError:
        return False  # schon gebucht (gleichzeitige Meldung)
    v.zustand, v.zahlung_ref, v.buchung_id, v.abgeschlossen = "bezahlt", zahlung_ref or v.zahlung_ref, b.id, utcnow()
    abrechnung.protokoll(db, "system", "zahlung", g.id, vorgang=v.id, anbieter=v.anbieter, netto_cent=v.netto_cent)
    return True


def _anteil(v: Bezahlvorgang, brutto_cent: int) -> int:
    """Anteil am gutgeschriebenen Betrag für einen Teil der Zahlung (Erstattung, Rückbuchung)."""
    return min(v.netto_cent, int(round(brutto_cent * v.netto_cent / max(1, v.brutto_cent))))


def _gegenbuchen(db: Session, v: Bezahlvorgang, art: str, brutto_cent: int, ereignis: str) -> bool:
    if v.zustand != "bezahlt" or brutto_cent <= 0:
        return False
    g = db.get(Guthaben, v.guthaben_id)
    try:
        with db.begin_nested():
            abrechnung.buchen(db, g, art, -_anteil(v, brutto_cent), ereignis_id=ereignis)
    except IntegrityError:
        return False
    abrechnung.protokoll(db, "system", art, g.id, vorgang=v.id, brutto_cent=brutto_cent)
    return True


def streitfall(db: Session, v: Bezahlvorgang, brutto_cent: int, ereignis: str) -> None:
    """Rückbuchung: Gegenbuchung und Sperre, bis der Betreiber entscheidet."""
    _gegenbuchen(db, v, "rueckbuchung", brutto_cent, ereignis)
    g = db.get(Guthaben, v.guthaben_id)
    if g.gesperrt_at is None:
        g.gesperrt_at = utcnow()
    _hinweis(db, "zahlung_streit", v)


# ---------------------------------------------------------------- Stripe
def stripe_signatur_gueltig(roh: bytes, kopf: str, geheimnis: str, jetzt: float | None = None) -> bool:
    teile: dict[str, list[str]] = {}
    for stueck in (kopf or "").split(","):
        k, _, w = stueck.strip().partition("=")
        teile.setdefault(k, []).append(w)
    try:
        t = int(teile.get("t", [""])[0])
    except ValueError:
        return False
    if abs((jetzt or time.time()) - t) > STRIPE_TOLERANZ:
        return False
    erwartet = hmac.new(geheimnis.encode(), f"{t}.".encode() + roh, hashlib.sha256).hexdigest()
    return any(hmac.compare_digest(erwartet, s) for s in teile.get("v1", []))


def _vorgang(db: Session, vid: str | None, anbieter: str) -> Bezahlvorgang | None:
    v = db.get(Bezahlvorgang, str(vid or "")[:64]) if vid else None
    return v if v is not None and v.anbieter == anbieter else None


def stripe_meldung(db: Session, ereignis: dict) -> None:
    art = ereignis.get("type", "")
    obj = (ereignis.get("data") or {}).get("object") or {}
    if art in ("checkout.session.completed", "checkout.session.async_payment_succeeded"):
        v = _vorgang(db, (obj.get("metadata") or {}).get("vorgang") or obj.get("client_reference_id"), "stripe")
        if v is None or obj.get("id") != v.anbieter_ref:
            _hinweis(db, "zahlung_abweichung", v, grund="unbekannter Vorgang")
            return
        if obj.get("payment_status") != "paid":
            return  # z. B. Lastschrift: kommt später als async_payment_succeeded
        bezahlt(db, v, int(obj.get("amount_total") or 0), str(obj.get("currency") or ""), obj.get("payment_intent"))
    elif art in ("checkout.session.expired", "checkout.session.async_payment_failed"):
        v = _vorgang(db, (obj.get("metadata") or {}).get("vorgang"), "stripe")
        if v is not None and v.zustand == "offen" and obj.get("id") == v.anbieter_ref:
            v.zustand = "abgebrochen" if art.endswith("expired") else "fehlgeschlagen"
    elif art == "charge.refunded":
        pi = obj.get("payment_intent")
        v = db.scalar(select(Bezahlvorgang).where(Bezahlvorgang.anbieter == "stripe",
                                                  Bezahlvorgang.zahlung_ref == pi)) if pi else None
        if v is None:
            return
        neu = int(obj.get("amount_refunded") or 0) - (v.erstattet_cent or 0)
        if _gegenbuchen(db, v, "erstattung", neu, f"stripe:{ereignis.get('id')}"):
            v.erstattet_cent = int(obj.get("amount_refunded") or 0)
    elif art == "charge.dispute.created":
        pi = obj.get("payment_intent")
        v = db.scalar(select(Bezahlvorgang).where(Bezahlvorgang.anbieter == "stripe",
                                                  Bezahlvorgang.zahlung_ref == pi)) if pi else None
        if v is not None:
            streitfall(db, v, int(obj.get("amount") or 0), f"stripe:{obj.get('id')}")


# ---------------------------------------------------------------- PayPal
def _capture_lesen(antwort: dict) -> dict | None:
    for pu in antwort.get("purchase_units") or []:
        for cap in ((pu.get("payments") or {}).get("captures") or []):
            return cap
    return None


def paypal_zurueck(db: Session, v: Bezahlvorgang, auftrag: str) -> str:
    """Rücksprung von PayPal: Der Server bucht die Zahlung selbst ab und schreibt nur gut, was PayPal bestätigt."""
    if v.zustand != "offen" or auftrag != v.anbieter_ref:
        return v.zustand
    kopf = {"Authorization": f"Bearer {_paypal_token(db)}", "PayPal-Request-Id": f"capture-{v.id}"}
    r = _c().post(f"{_paypal_basis(db)}/v2/checkout/orders/{v.anbieter_ref}/capture", json={}, headers=kopf)
    if r.status_code == 422:  # schon abgebucht – Stand nachlesen
        r = _c().get(f"{_paypal_basis(db)}/v2/checkout/orders/{v.anbieter_ref}", headers=kopf)
    r.raise_for_status()
    cap = _capture_lesen(r.json())
    if cap is None or cap.get("custom_id") not in (None, v.id):
        return v.zustand
    if cap.get("status") == "COMPLETED":
        betrag = cap.get("amount") or {}
        bezahlt(db, v, _cent(betrag.get("value")), str(betrag.get("currency_code") or ""), cap.get("id"))
    elif cap.get("status") in ("PENDING",):
        v.zahlung_ref = cap.get("id")
    elif cap.get("status") in ("DECLINED", "FAILED"):
        v.zustand = "fehlgeschlagen"
    return v.zustand


def _cent(wert) -> int:
    try:
        euro, _, cent = str(wert).partition(".")
        return int(euro) * 100 + int((cent + "00")[:2])
    except ValueError:
        return -1


def paypal_signatur_gueltig(db: Session, kopf: dict, ereignis: dict) -> bool:
    daten = {"auth_algo": kopf.get("paypal-auth-algo"), "cert_url": kopf.get("paypal-cert-url"),
             "transmission_id": kopf.get("paypal-transmission-id"),
             "transmission_sig": kopf.get("paypal-transmission-sig"),
             "transmission_time": kopf.get("paypal-transmission-time"),
             "webhook_id": meta_lesen(db, "zahlung.paypal_webhook_id"), "webhook_event": ereignis}
    if not all(daten.values()):
        return False
    try:
        r = _c().post(f"{_paypal_basis(db)}/v1/notifications/verify-webhook-signature", json=daten,
                      headers={"Authorization": f"Bearer {_paypal_token(db)}"})
        return r.status_code == 200 and r.json().get("verification_status") == "SUCCESS"
    except (httpx.HTTPError, ValueError, KeyError):
        return False


def _capture_id(res: dict) -> str | None:
    for link in res.get("links") or []:
        if link.get("rel") == "up" and "/captures/" in str(link.get("href")):
            return str(link["href"]).rstrip("/").rsplit("/", 1)[-1]
    return None


def paypal_meldung(db: Session, ereignis: dict) -> None:
    art = ereignis.get("event_type", "")
    res = ereignis.get("resource") or {}
    if art == "PAYMENT.CAPTURE.COMPLETED":
        v = _vorgang(db, res.get("custom_id"), "paypal")
        if v is None:
            return
        # 0.4.136 (S54): die Zahlung muss zum eigenen Auftrag bei PayPal gehören
        auftrag = ((res.get("supplementary_data") or {}).get("related_ids") or {}).get("order_id")
        if not v.anbieter_ref or auftrag != v.anbieter_ref:
            _hinweis(db, "zahlung_abweichung", v, grund="fremder Auftrag")
            return
        betrag = res.get("amount") or {}
        bezahlt(db, v, _cent(betrag.get("value")), str(betrag.get("currency_code") or ""), res.get("id"))
    elif art in ("PAYMENT.CAPTURE.REFUNDED", "PAYMENT.CAPTURE.REVERSED"):
        cap = _capture_id(res) or res.get("id")
        v = db.scalar(select(Bezahlvorgang).where(Bezahlvorgang.anbieter == "paypal",
                                                  Bezahlvorgang.zahlung_ref == cap)) if cap else None
        if v is None:
            return
        betrag = _cent((res.get("amount") or {}).get("value"))
        if art.endswith("REVERSED"):
            streitfall(db, v, betrag, f"paypal:{ereignis.get('id')}")
        else:
            _gegenbuchen(db, v, "erstattung", betrag, f"paypal:{ereignis.get('id')}")
    elif art == "CUSTOMER.DISPUTE.CREATED":
        for t in res.get("disputed_transactions") or []:
            cap = t.get("seller_transaction_id")
            v = db.scalar(select(Bezahlvorgang).where(Bezahlvorgang.anbieter == "paypal",
                                                      Bezahlvorgang.zahlung_ref == cap)) if cap else None
            if v is not None:
                betrag = _cent((res.get("dispute_amount") or {}).get("value"))
                streitfall(db, v, betrag if betrag > 0 else v.brutto_cent, f"paypal:{ereignis.get('id')}")


# ---------------------------------------------------------------- Für Seiten
def offene(db: Session, g: Guthaben) -> list[Bezahlvorgang]:
    """Begonnene Zahlungen der letzten Stunde, die noch nicht bestätigt sind (Hinweis auf /guthaben)."""
    seit = utcnow() - timedelta(hours=1)
    return list(db.scalars(select(Bezahlvorgang).where(Bezahlvorgang.guthaben_id == g.id,
                                                       Bezahlvorgang.zustand == "offen",
                                                       Bezahlvorgang.anbieter_ref.is_not(None),
                                                       Bezahlvorgang.angelegt >= seit)))


def einstellungen_schreiben(db: Session, von: str, **werte) -> None:
    """Werte aus der Verwaltung. Geheimes nur, wenn neu eingetragen (leer = unverändert)."""
    alt = {"betraege": betraege(db), "gebuehren": gebuehren(db).__dict__}
    for name in ("stripe_schluessel", "stripe_webhook", "paypal_geheim"):
        if werte.get(name):
            meta_schreiben(db, f"zahlung.{name}", werte[name])
    for name in ("paypal_id", "paypal_webhook_id"):
        if name in werte and werte[name] is not None:
            meta_schreiben(db, f"zahlung.{name}", werte[name])
    if werte.get("betraege") is not None:
        meta_schreiben(db, "zahlung.betraege", ",".join(str(b) for b in werte["betraege"]))
    if werte.get("gebuehren") is not None:
        meta_schreiben(db, "zahlung.gebuehren", json.dumps(werte["gebuehren"].__dict__))
    abrechnung.protokoll(db, von, "zahlung_einstellungen", alt=alt,
                         neu={"betraege": betraege(db), "gebuehren": gebuehren(db).__dict__,
                              "geheimes_neu": [n for n in ("stripe_schluessel", "stripe_webhook", "paypal_geheim")
                                               if werte.get(n)]})


def entsperren(db: Session, g: Guthaben, von: str) -> None:
    if g.gesperrt_at is not None:
        g.gesperrt_at = None
        abrechnung.protokoll(db, von, "sperre_aufgehoben", g.id)


def gebucht(db: Session, v: Bezahlvorgang) -> Buchung | None:
    return db.get(Buchung, v.buchung_id) if v.buchung_id else None
