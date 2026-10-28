"""E-Mails an Kontoinhaber (0.4.0): Adresse bestätigen, Passwort vergessen; SL-Freigabe (0.4.23).

Gleicher Mailserver wie die Benachrichtigungen (Verwaltung → Einstellungen → E-Mail-Versand). Die Adresse dient nur
Kontozwecken (dazu zählen ab 0.4.135 die abbestellbaren Hinweise zum Guthaben, app/guthaben_mail.py).

0.4.135: Jede Mail an ein Konto hat eine Text- und eine HTML-Fassung aus denselben Inhalten (app/mailvorlage.py). Kein
Tracking, keine nachgeladenen Bilder; nur das eigene Zeichen ist eingebettet.
"""
from __future__ import annotations

import logging
import re

from sqlalchemy.orm import Session

log = logging.getLogger("mail")
MUSTER = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[^@\s]{2,}$")


class MailFehler(Exception):
    pass


def gueltig(adresse: str) -> bool:
    return bool(MUSTER.match(adresse or "")) and len(adresse) <= 254


def normal(adresse: str) -> str:
    return (adresse or "").strip().lower()


def kann_senden(db: Session) -> bool:
    from app.benachrichtigung import konfig

    return bool(konfig(db).smtp_host)


def senden(db: Session, an: str, betreff: str, text: str, html: str | None = None) -> None:
    from dataclasses import replace

    from app.benachrichtigung import VersandFehler, _email, konfig

    k = konfig(db)
    if not k.smtp_host:
        raise MailFehler("Kein Mailserver eingerichtet.")
    # 0.4.123: Antworten an den Kontakt des Betreibers (wenn er eine E-Mail-Adresse ist), nicht an den Absender
    try:
        _email(replace(k, email_an=an), betreff, text, antwort_an=kontakt(db), html=html)
    except VersandFehler as e:
        log.warning("Mail an Konto nicht zugestellt: %s", e)
        raise MailFehler(str(e)) from None


def sprache_merken(user, request) -> None:
    """Bei der Anmeldung: Sprache der App (Accept-Language) am Konto merken. Ohne passende Angabe bleibt der alte Wert."""
    for teil in (request.headers.get("accept-language") or "").split(","):
        tag = teil.split(";")[0].strip().lower()
        if tag.startswith(("de", "en")):
            user.sprache = tag[:2]
            return


def sprache_fuer(db: Session, user) -> str:
    """Sprache für Mails an dieses Konto: zuletzt in der App, sonst die der SL-Anfrage, sonst die des Servers."""
    from app.benachrichtigung import konfig

    return "en" if (user.sprache or user.gm_sprache or konfig(db).sprache) == "en" else "de"


def kontakt(db: Session) -> str | None:
    """Kontakt des Betreibers, wenn er eine gültige Adresse ist – derselbe Wert steht als Reply-To und im Fuß."""
    from app.einstellungen import angaben

    k = (angaben(db).server_contact or "").strip()
    return k if gueltig(k) else None


def text(db: Session, art: str, sprache: str, **werte) -> tuple[str, str, str]:
    """(Betreff, Textfassung, HTML-Fassung) für eine Mail-Art aus app/mailvorlage.INHALTE."""
    from app import mailvorlage
    from app.aliasse import zeichen_bereinigen
    from app.einstellungen import angaben

    a = angaben(db)
    werte = {"server": a.server_name, "betreiber": a.server_operator, **werte}
    # 0.4.130: Namen ohne Steuer- und Richtungszeichen (sie stehen auch im Betreff)
    werte = {k: zeichen_bereinigen(v) if k in BEREINIGEN and isinstance(v, str) else v for k, v in werte.items()}
    k = kontakt(db)
    return (mailvorlage.betreff(art, sprache, **werte), mailvorlage.text(art, sprache, kontakt=k, **werte),
            mailvorlage.html(art, sprache, kontakt=k, **werte))


BEREINIGEN = ("server", "betreiber", "name")  # Titel in {wo} und {guthaben} säubert der Aufrufer vorher
