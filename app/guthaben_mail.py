"""Hinweis-Mails zum Guthaben (0.4.135): außerhalb der App sagen, wo aufgeladen wird.

Die Wartung ruft `pruefen(db)` regelmäßig auf. Nur bei eingeschalteter Abrechnung, eingerichtetem Mailserver und
eingetragener öffentlicher Adresse (der Link kommt nur aus ihr, nie aus einer Anfrage – G15).

Anlässe, in dieser Reihenfolge (höchstens eine Mail je Guthaben und 24 Stunden; die wichtigste zuerst):

- wartet: eine Verarbeitung wartet, weil das Guthaben leer ist oder voraussichtlich nicht reicht,
- minus: das Guthaben ist unter null,
- freigabe: eine Verarbeitung wartet auf „Verarbeiten“ (teurer als der eigene Freigabe-Betrag),
- knapp: vom Guthaben ist noch etwas übrig, aber weniger als die Warnschwelle der Verwaltung („Guthaben knapp“).

Jeder Anlass wird einmal gemeldet und erst wieder, wenn er vorbei war (z. B. nach dem Aufladen). „wartet“ und „minus“
schließen „knapp“ mit ein. Gesperrte Guthaben (Rückbuchung) und Kampagnen ohne Zahler lösen keine Mail aus – dort hilft
Aufladen nicht.

Empfänger: beim eigenen Guthaben die SL, beim Topf die Verwalter der Organisation – jeweils nur mit bestätigter Adresse
und ohne Abbestellung. Keine Beträge in der Mail. Der Kampagnentitel steht nur bei Personen, die dort heute SL sind
(G14); sonst heißt es nur „etwas“.
"""
from __future__ import annotations

import logging
from datetime import timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import abrechnung
from app.db import utcnow
from app.models import Campaign, Guthaben, Job, KostenAuftrag, OrgMember, Organization, User

log = logging.getLogger("guthaben_mail")
ANLAESSE = ("wartet", "minus", "freigabe", "knapp")  # Vorrang
SCHLIESST_EIN = {"wartet": {"knapp"}, "minus": {"knapp"}}
ABSTAND = timedelta(hours=24)
TITEL_HOECHSTENS = 80

GUTHABEN = {
    "de": ("dein Guthaben", "Dein Guthaben", "das Guthaben der Organisation „{name}“",
           "Das Guthaben der Organisation „{name}“"),
    "en": ("your credit", "Your credit", "the credit of the organisation “{name}”",
           "The credit of the organisation “{name}”"),
}
WO = {"de": " in der Kampagne „{titel}“", "en": " in the campaign “{titel}”"}


def _wartende(db: Session) -> dict[str, dict[str, Campaign | None]]:
    """Guthaben-ID → {Anlass: Kampagne des ersten wartenden Auftrags}. Ein Durchgang über die wartenden Aufträge."""
    aus: dict[str, dict[str, Campaign | None]] = {}
    for job in db.scalars(select(Job).where(Job.state == "queued", Job.type.in_(abrechnung.KOSTENPFLICHTIG))
                          .order_by(Job.created_at).limit(2000)):
        if db.get(KostenAuftrag, job.id) is not None:
            continue
        grund = abrechnung.warte_grund(db, job)
        anlass = {"leer": "wartet", "zu_knapp": "wartet", "freigabe": "freigabe"}.get(grund or "")
        if anlass is None:
            continue
        c = abrechnung.kampagne_von_auftrag(db, job)
        g = abrechnung.zahler(db, c, anlegen=True)  # Topf noch ohne Zeile: anlegen, damit er sich merkt, was gemeldet ist
        if g is None:
            continue
        aus.setdefault(g.id, {}).setdefault(anlass, c)
    return aus


def empfaenger(db: Session, g: Guthaben) -> list[User]:
    if g.art == "konto":
        kandidaten = [db.get(User, g.user_id)] if g.user_id else []
    else:
        kandidaten = list(db.scalars(select(User).join(OrgMember, OrgMember.user_id == User.id).where(
            OrgMember.organization_id == g.organization_id, OrgMember.role == "admin").order_by(User.username)))
    return [u for u in kandidaten if u is not None and u.email and not u.setup_account and not u.guthaben_mails_aus]


def _werte(db: Session, g: Guthaben, u: User, c: Campaign | None, sprache: str, link: str) -> dict:
    from app.aliasse import zeichen_bereinigen

    klein, gross, org_klein, org_gross = GUTHABEN[sprache]
    if g.art == "organisation":
        org = db.get(Organization, g.organization_id) if g.organization_id else None
        name = zeichen_bereinigen(org.name if org else "")[:TITEL_HOECHSTENS] or "?"
        klein, gross = org_klein.format(name=name), org_gross.format(name=name)
    wo = ""
    if c is not None and abrechnung.ist_aktive_sl(db, u.id, c):
        titel = zeichen_bereinigen(c.title or "")[:TITEL_HOECHSTENS]
        wo = WO[sprache].format(titel=titel) if titel else ""
    return {"name": u.display_name, "benutzer": u.username, "link": link, "guthaben": klein, "guthaben_gross": gross,
            "wo": wo}


def senden(db: Session, g: Guthaben, anlass: str, kampagne: Campaign | None, basis: str) -> int:
    """Mail an alle Empfänger dieses Guthabens. Gibt die Zahl der zugestellten Mails zurück."""
    from app import mail

    zugestellt = 0
    for u in empfaenger(db, g):
        sprache = mail.sprache_fuer(db, u)
        betreff, text, html = mail.text(db, f"guthaben_{anlass}", sprache,
                                        **_werte(db, g, u, kampagne, sprache, f"{basis}/guthaben"))
        try:
            mail.senden(db, u.email, betreff, text, html)
            zugestellt += 1
        except (mail.MailFehler, ValueError):  # ValueError: Kopfzeile ungültig – wie ein Versandfehler
            log.warning("Hinweis-Mail zum Guthaben nicht zugestellt (Konto %s)", u.id)
    return zugestellt


def pruefen(db: Session) -> int:
    """Gibt die Zahl der verschickten Mails zurück."""
    from app import mail
    from app.einstellungen import mail_adresse

    if not abrechnung.an(db) or not mail.kann_senden(db):
        return 0
    basis = mail_adresse(db)
    if not basis:
        return 0
    wartend = _wartende(db)
    jetzt = utcnow()
    verschickt = 0
    for g in db.scalars(select(Guthaben).order_by(Guthaben.angelegt)).all():
        aktiv: dict[str, Campaign | None] = {}
        if g.gesperrt_at is None:
            aktiv.update(wartend.get(g.id, {}))
            st = abrechnung.stand(db, g)
            if st == "minus":
                aktiv["minus"] = None
            elif st == "knapp":  # nur mit Rest: leer ohne Wartendes ist kein Anlass (sonst Mails beim Einschalten)
                aktiv["knapp"] = None
        gemeldet = {a for a in (g.hinweise or "").split(",") if a} & set(aktiv)  # vorbei → vergessen
        neu = [a for a in ANLAESSE if a in aktiv and a not in gemeldet]
        frei = g.hinweis_mail_at is None or jetzt - g.hinweis_mail_at >= ABSTAND
        if neu and frei and empfaenger(db, g):
            anlass = neu[0]
            n = senden(db, g, anlass, aktiv[anlass], basis)
            if n:
                verschickt += n
                gemeldet |= {anlass} | (SCHLIESST_EIN.get(anlass, set()) & set(aktiv))
                g.hinweis_mail_at = jetzt
        wert = ",".join(a for a in ANLAESSE if a in gemeldet) or None
        if wert != g.hinweise:
            g.hinweise = wert
    db.commit()
    return verschickt
