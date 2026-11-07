"""Echtes Geld einschalten (Weg zum ersten echten Geld, Punkt 7): nur über die Kommandozeile auf dem Server.

`chronik zahlung-live --pruefen` zeigt, was noch fehlt. `chronik zahlung-live --an` fragt die Live-Zugänge ab
(nie als Parameter, damit sie nicht im Befehlsverlauf landen) und schaltet nur ein, wenn nichts fehlt:
- Bedingungen für Guthaben und Widerrufsbelehrung veröffentlicht (beide Sprachen),
- Impressum und Datenschutzhinweis ohne Platzhalter in [eckigen Klammern] (bzw. als eigener https-Link),
- zweiter Faktor bei allen Verwaltungskonten,
- Abrechnung eingeschaltet und öffentliche https-Adresse eingetragen (Rücksprung und Meldungen),
- Stripe: Live-Schlüssel (`sk_live_`/`rk_live_`) und Signatur-Geheimnis der Meldungen (`whsec_`),
- PayPal: Client-ID, Secret und Webhook-ID des Live-Kontos,
- keine Testinstanz.
`chronik zahlung-live --aus` geht immer (Notbremse) und stellt die vorherigen Test-Zugänge wieder her.
Jeder Schritt steht im Abrechnungsprotokoll (ohne Geheimnisse).
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import abrechnung
from app.einstellungen import meta_lesen, meta_schreiben

ZUGAENGE = ("stripe_schluessel", "stripe_webhook", "paypal_id", "paypal_geheim", "paypal_webhook_id")
PLATZHALTER = re.compile(r"\[[^\]\n]{1,80}\]")


def _ohne_platzhalter(text: str | None) -> bool:
    return bool(text and text.strip()) and not PLATZHALTER.search(text)


def fehlt(db: Session, neu: dict[str, str] | None = None) -> list[str]:
    """Was vor dem Einschalten noch fehlt – je Punkt ein Satz für den Betreiber. Leer = alles da."""
    from app import rechtstexte, zahlung
    from app.config import get_settings
    from app.einstellungen import angaben
    from app.links import betreiber_link
    from app.models import OrgMember, User
    from app.verwaltung import zweiter_faktor

    aus: list[str] = []
    if get_settings().taleward_testinstanz:
        aus.append("Das ist eine Testinstanz – dort gibt es nie echtes Geld.")
    if not abrechnung.an(db):
        aus.append("Die Abrechnung ist aus (Verwaltung → Server → Abrechnung → Schalter).")
    if zahlung.basis(db) is None or not (zahlung.basis(db) or "").startswith("https://"):
        aus.append("Keine öffentliche https-Adresse eingetragen (Verwaltung → Einstellungen → Einrichtung).")
    if not rechtstexte.veroeffentlicht(db, "bedingungen"):
        aus.append("Die Bedingungen für Guthaben sind nicht in beiden Sprachen veröffentlicht.")
    if not rechtstexte.veroeffentlicht(db, "widerruf"):
        aus.append("Die Widerrufsbelehrung ist nicht in beiden Sprachen veröffentlicht.")
    impressum = meta_lesen(db, "impressum.text")
    if not (_ohne_platzhalter(impressum) or (not impressum and betreiber_link(meta_lesen(db, "impressum.url")))):
        aus.append("Das Impressum fehlt oder hat noch Stellen in [eckigen Klammern].")
    datenschutz = meta_lesen(db, "datenschutz.text")
    if not (_ohne_platzhalter(datenschutz) or (not datenschutz and betreiber_link(angaben(db).privacy_policy_url))):
        aus.append("Der Datenschutzhinweis fehlt oder hat noch Stellen in [eckigen Klammern].")
    ohne = sorted(set(db.scalars(select(User.username).join(OrgMember, OrgMember.user_id == User.id).where(
        OrgMember.role == "admin", User.setup_account.is_(False)))))
    ohne = [n for n in ohne if not zweiter_faktor.aktiv(db, db.scalar(select(User.id).where(User.username == n)))]
    if ohne:
        aus.append("Ohne zweiten Faktor: " + ", ".join(ohne) + ".")
    if neu is not None:
        if not neu.get("stripe_schluessel", "").startswith(("sk_live_", "rk_live_")):
            aus.append("Der Stripe-Schlüssel ist kein Live-Schlüssel (beginnt mit sk_live_ oder rk_live_).")
        if not neu.get("stripe_webhook", "").startswith("whsec_"):
            aus.append("Das Signatur-Geheimnis der Stripe-Meldungen fehlt (beginnt mit whsec_).")
        for name, text in (("paypal_id", "Client-ID"), ("paypal_geheim", "Secret"), ("paypal_webhook_id", "Webhook-ID")):
            if len((neu.get(name) or "").strip()) < 8:
                aus.append(f"PayPal: {text} des Live-Kontos fehlt.")
    return aus


def an(db: Session, neu: dict[str, str], von: str = "kommandozeile") -> list[str]:
    """Einschalten, wenn nichts fehlt. Gibt die Liste der fehlenden Punkte zurück (leer = eingeschaltet)."""
    from app import zahlung

    luecken = fehlt(db, neu)
    if luecken:
        abrechnung.protokoll(db, von, "zahlung_live_verweigert", anzahl=len(luecken))
        return luecken
    if not zahlung.live(db):  # Test-Zugänge für den Rückweg aufheben
        for name in ZUGAENGE:
            meta_schreiben(db, f"zahlung.test.{name}", meta_lesen(db, f"zahlung.{name}") or "")
    for name in ZUGAENGE:
        meta_schreiben(db, f"zahlung.{name}", neu[name].strip())
    meta_schreiben(db, "zahlung.live", "1")
    abrechnung.protokoll(db, von, "zahlung_live_an")
    return []


def aus(db: Session, von: str = "kommandozeile") -> None:
    """Ausschalten geht immer. Die Test-Zugänge von vorher kommen zurück, die Live-Zugänge werden gelöscht."""
    war_live = meta_lesen(db, "zahlung.live") == "1"
    meta_schreiben(db, "zahlung.live", "0")
    if war_live:
        for name in ZUGAENGE:
            meta_schreiben(db, f"zahlung.{name}", meta_lesen(db, f"zahlung.test.{name}") or "")
            meta_schreiben(db, f"zahlung.test.{name}", "")
    abrechnung.protokoll(db, von, "zahlung_live_aus")
