"""Erstattung von Restguthaben (0.4.153, Weg zum ersten echten Geld, Punkte 3 und 4).

Regeln (Entscheidungen Benjamin 28.10.2026, `claude/plan-abrechnung-guthaben.md`):
- Verbrauch zieht zuerst geschenktes Guthaben (Gutschrift, Gutschein, positive Korrektur), dann bezahltes. Erstattbar
  ist nur der Rest aus Zahlungen, voll und ohne Abzug; Gutschriften und Gutscheine werden nie ausgezahlt.
- Erstattet wird auf die ursprünglichen Zahlungen, die neueste zuerst. Die Verwaltung zeigt je Fall, welcher Betrag auf
  welche Zahlung zurückgeht; der Betreiber erstattet im Dashboard des Anbieters, dessen Meldung bucht die Gegenbuchung
  wie bisher. Geht das nicht mehr (Frist des Anbieters, Karte erloschen): per Mail nach einer Kontoverbindung fragen und
  von Hand überweisen – die kommt nie auf den Server.
- Kontolöschung: das Konto geht sofort; bleibt Erstattbares, entsteht eine Zeile „Erstattung offen“ ohne Namen mit
  Betrag, Zahlungsbezügen und der E-Mail-Adresse. Die Adresse wird gelöscht, sobald erstattet ist, spätestens nach
  AUFBEWAHRUNG_TAGE (Wartung).
"""
from __future__ import annotations

import json
import math
import re
from dataclasses import dataclass, field
from datetime import timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Bezahlvorgang, Buchung, Guthaben, OffeneErstattung

AUFBEWAHRUNG_TAGE = 90
ANBIETER_FRIST_TAGE = 180  # danach erstatten Stripe und PayPal auf die Zahlung meist nicht mehr
GESCHENKT = ("gutschrift", "gutschein")
BEZAHLT = ("zahlung", "erstattung", "rueckbuchung")


def _summe(db: Session, g: Guthaben, arten: tuple[str, ...]) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(Buchung.betrag_cent), 0))
                         .where(Buchung.guthaben_id == g.id, Buchung.art.in_(arten))) or 0)


def erstattbar(db: Session, g: Guthaben | None) -> int:
    """Rest aus Zahlungen in Cent. Weil Verbrauch zuerst Geschenktes zieht, ist das min(Saldo, Zahlungen netto)."""
    from app import abrechnung

    if g is None or g.id is None:  # noch nicht angelegt (vorläufiges Guthaben)
        return 0
    netto_bezahlt = _summe(db, g, BEZAHLT)
    return max(0, min(abrechnung.saldo(db, g.id), netto_bezahlt))


@dataclass
class Teil:
    vorgang_id: str
    anbieter: str
    zahlung_ref: str
    bezahlt_am: object
    credit_cent: int  # so viel Guthaben geht damit zurück
    beim_anbieter_cent: int  # so viel im Dashboard des Anbieters erstatten (mit anteiliger Gebühr)
    frist_vorbei: bool


@dataclass
class Aufteilung:
    betrag_cent: int
    teile: list[Teil] = field(default_factory=list)
    von_hand_cent: int = 0  # deckt keine Zahlung mehr – per Überweisung

    @property
    def ueberweisung_noetig(self) -> bool:
        return self.von_hand_cent > 0 or any(t.frist_vorbei for t in self.teile)


def _rest(v: Bezahlvorgang) -> tuple[int, int]:
    """Noch nicht erstatteter Teil einer Zahlung: (Guthaben-Anteil, beim Anbieter)."""
    brutto_rest = max(0, v.brutto_cent - (v.erstattet_cent or 0))
    credit_rest = min(v.netto_cent, int(brutto_rest * v.netto_cent / max(1, v.brutto_cent)))
    return credit_rest, brutto_rest


def aufteilen(db: Session, g: Guthaben, betrag_cent: int | None = None) -> Aufteilung:
    """Betrag (Standard: alles Erstattbare) auf die bezahlten Vorgänge verteilen, die neueste zuerst."""
    betrag = erstattbar(db, g) if betrag_cent is None else max(0, int(betrag_cent))
    a = Aufteilung(betrag_cent=betrag)
    offen = betrag
    grenze = utcnow() - timedelta(days=ANBIETER_FRIST_TAGE)
    for v in db.scalars(select(Bezahlvorgang).where(Bezahlvorgang.guthaben_id == g.id,
                                                    Bezahlvorgang.zustand == "bezahlt")
                        .order_by(Bezahlvorgang.abgeschlossen.desc(), Bezahlvorgang.angelegt.desc())):
        if offen <= 0:
            break
        credit_rest, brutto_rest = _rest(v)
        if credit_rest <= 0:
            continue
        anteil = min(offen, credit_rest)
        beim_anbieter = min(brutto_rest, math.ceil(anteil * v.brutto_cent / max(1, v.netto_cent)))
        am = v.abgeschlossen or v.angelegt
        if am is not None and am.tzinfo is None:
            from datetime import timezone

            am = am.replace(tzinfo=timezone.utc)
        a.teile.append(Teil(v.id, v.anbieter, v.zahlung_ref or v.anbieter_ref or "", am, anteil, beim_anbieter,
                            bool(am and am < grenze)))
        offen -= anteil
    a.von_hand_cent = max(0, offen)
    return a


# ---------------------------------------------------------------- Kontolöschung
def bei_kontoloeschung(db: Session, user) -> OffeneErstattung | None:
    """Vor dem Löschen des Kontos aufrufen: bleibt Erstattbares, eine Zeile ohne Namen anlegen."""
    from app import abrechnung

    g = abrechnung.guthaben_konto(db, user.id, anlegen=False)
    betrag = erstattbar(db, g)
    if g is None or g.id is None or betrag <= 0:
        return None
    a = aufteilen(db, g, betrag)
    zeile = OffeneErstattung(
        guthaben_id=g.id, betrag_cent=betrag,
        zahlungen=json.dumps([{"anbieter": t.anbieter, "ref": t.zahlung_ref, "cent": t.credit_cent,
                               "beim_anbieter_cent": t.beim_anbieter_cent} for t in a.teile]),
        email=(user.email if user.email and user.email_verified_at else None))
    db.add(zeile)
    abrechnung.protokoll(db, "system", "erstattung_offen", g.id, betrag_cent=betrag)
    return zeile


def offene(db: Session) -> list[OffeneErstattung]:
    return list(db.scalars(select(OffeneErstattung).where(OffeneErstattung.erledigt_at.is_(None))
                           .order_by(OffeneErstattung.angelegt)))


def erledigen(db: Session, zeile: OffeneErstattung, wie: str, von: str) -> None:
    """wie: erstattet (vom Anbieter gemeldet) | von_hand (Überweisung, vom Betreiber bestätigt)."""
    from app import abrechnung

    zeile.erledigt_at, zeile.erledigt_wie, zeile.email = utcnow(), wie, None
    abrechnung.protokoll(db, von, "erstattung_erledigt", zeile.guthaben_id, wie=wie, betrag_cent=zeile.betrag_cent)


def wartung(db: Session) -> int:
    """Erstattete Fälle schließen, E-Mail-Adressen nach AUFBEWAHRUNG_TAGE löschen. Gibt die Zahl der Änderungen."""
    n = 0
    grenze = utcnow() - timedelta(days=AUFBEWAHRUNG_TAGE)
    for z in offene(db):
        g = db.get(Guthaben, z.guthaben_id)
        if erstattbar(db, g) <= 0:
            erledigen(db, z, "erstattet", "system")
            n += 1
            continue
        angelegt = z.angelegt if z.angelegt.tzinfo else z.angelegt.replace(tzinfo=grenze.tzinfo)
        if z.email and angelegt < grenze:
            z.email = None
            n += 1
    return n


# ---------------------------------------------------------------- Knopf „Erstatten“ (0.4.157)
STRIPE_REF = re.compile(r"^pi_[A-Za-z0-9]{8,64}$")
PAYPAL_REF = re.compile(r"^[A-Z0-9]{10,30}$")


def _beim_anbieter(db: Session, v: Bezahlvorgang, cent: int) -> None:
    """Eine Erstattung beim Anbieter anstoßen. Gebucht wird erst mit dessen Meldung (wie bei einer Erstattung im
    Dashboard). Der Schlüssel gegen Doppelklicks hängt am bisher erstatteten Betrag: ein zweiter Klick, bevor die
    Meldung da ist, schickt denselben Auftrag noch einmal, und der Anbieter führt ihn nur einmal aus."""
    from app import zahlung

    kennung = f"erstattung-{v.id}-{v.erstattet_cent or 0}-{cent}"
    ref = v.zahlung_ref or ""
    if v.anbieter == "stripe":
        if not STRIPE_REF.match(ref):
            raise ValueError("ref")
        r = zahlung._c().post(f"{zahlung.STRIPE_API}/v1/refunds",
                              data={"payment_intent": ref, "amount": str(cent), "metadata[vorgang]": v.id},
                              headers=zahlung._stripe_kopf(db, **{"Idempotency-Key": kennung}))
    elif v.anbieter == "paypal":
        if not PAYPAL_REF.match(ref):
            raise ValueError("ref")
        r = zahlung._c().post(f"{zahlung._paypal_basis(db)}/v2/payments/captures/{ref}/refund",
                              json={"amount": {"value": zahlung._euro_text(cent), "currency_code": "EUR"}},
                              headers={"Authorization": f"Bearer {zahlung._paypal_token(db)}",
                                       "PayPal-Request-Id": kennung})
    else:
        raise ValueError("anbieter")
    r.raise_for_status()


@dataclass
class Ergebnis:
    angestossen_cent: int = 0  # so viel Guthaben geht mit den Meldungen zurück
    fehlgeschlagen: list[str] = field(default_factory=list)  # Zahlungsbezüge, die nicht gingen
    von_hand_cent: int = 0  # Frist vorbei oder keine passende Zahlung – per Überweisung


def erstatten(db: Session, g: Guthaben, von: str) -> Ergebnis:
    """Alles Erstattbare eines Guthabens beim Anbieter erstatten (Verwaltung → Abrechnung → Erstattungen). Beträge
    rechnet immer der Server (aufteilen), nie das Formular. Zahlungen älter als die Frist bleiben für die Überweisung."""
    import httpx

    from app import abrechnung, zahlung

    a = aufteilen(db, g)
    e = Ergebnis(von_hand_cent=a.von_hand_cent)
    bereit = set(zahlung.bereit(db))
    for t in a.teile:
        v = db.get(Bezahlvorgang, t.vorgang_id)
        if t.frist_vorbei or v is None or v.anbieter not in bereit:
            e.von_hand_cent += t.credit_cent
            continue
        try:
            _beim_anbieter(db, v, t.beim_anbieter_cent)
        except (httpx.HTTPError, ValueError, KeyError, TypeError) as fehler:
            e.fehlgeschlagen.append(t.zahlung_ref)
            abrechnung.protokoll(db, von, "erstattung_fehlgeschlagen", g.id, vorgang=v.id,
                                 grund=type(fehler).__name__)
            continue
        e.angestossen_cent += t.credit_cent
        abrechnung.protokoll(db, von, "erstattung_angestossen", g.id, vorgang=v.id, anbieter=v.anbieter,
                             brutto_cent=t.beim_anbieter_cent, betrag_cent=t.credit_cent)
    return e
