"""SL-Freigabe: Wer darf eigene Kampagnen anlegen? (Schnittstelle 0.4.23, Server 0.4.128)

Regel (ServerInfo.gmApproval):
- none, wenn die Abrechnung „Einzelzahler“ ist (abrechnung.modus guthaben): Ohne Guthaben startet nichts
  Kostenpflichtiges, eine Freigabe schützt dann nichts mehr.
- sonst required, wenn die Registrierung offen ist oder der Betreiber die Freigabe immer verlangt
  (Verwaltung → Einstellungen → Konten, meta „sl_freigabe“: offen | immer).
- sonst none.

Bei required braucht POST /campaigns und POST /imports (Beginn und …/complete) User.gmPermission granted, sonst 403
gm_permission_required. SL einer bestehenden Kampagne zu werden (Einladung als SL, Übergabe) bleibt frei.

Anfrage (POST /me/gm-request): eine je Konto, nur mit bestätigter E-Mail-Adresse. Die Mail an die Verwaltung enthält
nur einen Link zur Liste in der Verwaltung; entschieden wird dort angemeldet per POST mit CSRF-Schutz, damit
Link-Prüfer der Mailanbieter nichts auslösen. Schlägt der Versand fehl, bleibt die Anfrage stehen und die Verwaltung
zeigt sie mit Hinweis und „Mail erneut senden“.
"""
from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors
from app.db import utcnow
from app.models import OrgMember, User

log = logging.getLogger("slfreigabe")

ARTEN = ("offen", "immer")
STAENDE = ("granted", "none", "pending", "rejected")
# Begrenzung der Anfragen je Konto (auch wiederholtes Absenden zählt)
MAX_ANFRAGEN, FENSTER = 10, 60 * 60


def einstellung(db: Session) -> str:
    from app.einstellungen import meta_lesen

    wert = meta_lesen(db, "sl_freigabe") or "offen"
    return wert if wert in ARTEN else "offen"


def einzelzahler(db: Session) -> bool:
    from app import abrechnung

    return abrechnung.modus(db) == "guthaben"


def gm_approval(db: Session) -> str:
    from app.konto import registrierung

    if einzelzahler(db):
        return "none"
    if registrierung(db) == "open" or einstellung(db) == "immer":
        return "required"
    return "none"


def pruefen(db: Session, user: User) -> None:
    """Vor dem Anlegen einer eigenen Kampagne (auch per Umzug)."""
    if user.gm_permission != "granted" and gm_approval(db) == "required":
        raise errors.ApiError(403, "gm_permission_required")


def gewaehren_ohne_entscheidung(user: User) -> None:
    """Verwaltungskonten dürfen immer leiten (beim Anlegen oder Ernennen); kein Entscheidungszeitpunkt."""
    if user.gm_permission != "granted":
        user.gm_permission, user.gm_mail_fehler = "granted", False


# ---------------------------------------------------------------- Anfrage
def empfaenger(db: Session) -> str | None:
    """Empfänger der Benachrichtigungen, sonst alle Verwaltungskonten mit bestätigter Adresse."""
    from app.benachrichtigung import konfig

    k = konfig(db)
    if (k.email_an or "").strip():
        return k.email_an.strip()
    adressen = sorted({u.email for u in db.scalars(
        select(User).join(OrgMember, OrgMember.user_id == User.id).where(OrgMember.role == "admin"))
        if u.email})
    return ", ".join(adressen) or None


def mail_an_verwaltung(db: Session, user: User, basis: str) -> bool:
    """True, wenn die Mail raus ist. Fehler stehen am Konto (gm_mail_fehler) und in der Verwaltung."""
    from app import mail
    from app.benachrichtigung import konfig

    an = empfaenger(db)
    ok = False
    if an and mail.kann_senden(db):
        betreff, text = mail.text(db, "sl_anfrage", konfig(db).sprache, name=user.display_name,
                                  benutzer=user.username, datum=user.gm_requested_at.strftime("%d.%m.%Y %H:%M UTC"),
                                  link=f"{basis.rstrip('/')}/verwaltung/sl-freigaben")
        try:
            mail.senden(db, an, betreff, text)
            ok = True
        except mail.MailFehler:
            pass
    if not ok:
        log.warning("Mail zur SL-Anfrage nicht verschickt (Konto %s)", user.id)
    user.gm_mail_fehler = not ok
    return ok


def anfragen(db: Session, user: User, sprache: str, basis: str) -> int:
    """Antwortstatus: 200 (nichts zu tun) oder 202 (Anfrage liegt vor)."""
    from app.begrenzung import ZAEHLER

    if not ZAEHLER.versuch(f"sl-anfrage:{user.id}", MAX_ANFRAGEN, FENSTER):
        raise errors.ApiError(429, "too_many_requests")
    if user.gm_permission == "granted" or gm_approval(db) == "none":
        return 200
    if user.gm_permission == "rejected":
        raise errors.conflict("gm_request_closed")
    if user.gm_permission == "pending":
        return 202
    if not user.email:
        raise errors.conflict("email_unverified")
    user.gm_permission, user.gm_requested_at = "pending", utcnow()
    user.gm_sprache = "en" if sprache == "en" else "de"
    db.flush()
    mail_an_verwaltung(db, user, basis)
    return 202


# ---------------------------------------------------------------- Entscheidung (nur Verwaltung)
ENTSCHEIDUNGEN = {
    # aktion: (neuer Stand, Mail an die Person oder None)
    "freigeben": ("granted", "sl_freigegeben"),
    "ablehnen": ("rejected", "sl_abgelehnt"),
    "entziehen": ("rejected", "sl_entzogen"),
    "erlauben": ("none", None),  # nach Ablehnung: neue Anfrage wieder möglich
}


def entscheiden(db: Session, user: User, aktion: str, von: User) -> bool:
    """Gibt zurück, ob die Mail an die Person verschickt wurde (False auch, wenn keine nötig oder möglich)."""
    from app import mail

    stand, art = ENTSCHEIDUNGEN[aktion]
    erlaubt = {"freigeben": ("pending", "none", "rejected"), "ablehnen": ("pending",),
               "entziehen": ("granted",), "erlauben": ("rejected",)}[aktion]
    if user.gm_permission not in erlaubt:
        return False
    user.gm_permission, user.gm_mail_fehler = stand, False
    user.gm_decided_at = utcnow() if stand != "none" else None
    user.gm_decided_by = von.id
    db.flush()
    if art is None or not user.email or not mail.kann_senden(db):
        return False
    betreff, text = mail.text(db, art, user.gm_sprache or "de", name=user.display_name, benutzer=user.username)
    try:
        mail.senden(db, user.email, betreff, text)
        return True
    except mail.MailFehler:
        return False


def offene(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.gm_permission == "pending").order_by(User.gm_requested_at)))


def entschiedene(db: Session) -> list[User]:
    return list(db.scalars(select(User).where(User.gm_permission.in_(("granted", "rejected")))
                           .order_by(User.gm_decided_at.desc().nulls_last(), User.username)))
