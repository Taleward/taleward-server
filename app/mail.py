"""E-Mails an Kontoinhaber (0.4.0): Adresse bestätigen, Passwort vergessen; SL-Freigabe (0.4.23).

Gleicher Mailserver wie die Benachrichtigungen (Verwaltung → Einstellungen → E-Mail-Versand). Kein Tracking, keine
Bilder, nur Text. Die Adresse dient nur Kontozwecken.
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


def senden(db: Session, an: str, betreff: str, text: str) -> None:
    from dataclasses import replace

    from app.benachrichtigung import VersandFehler, _email, konfig

    from app.einstellungen import angaben

    k = konfig(db)
    if not k.smtp_host:
        raise MailFehler("Kein Mailserver eingerichtet.")
    # 0.4.123: Antworten an den Kontakt des Betreibers (wenn er eine E-Mail-Adresse ist), nicht an den Absender
    kontakt = (angaben(db).server_contact or "").strip()
    try:
        _email(replace(k, email_an=an), betreff, text, antwort_an=kontakt if gueltig(kontakt) else None)
    except VersandFehler as e:
        log.warning("Mail an Konto nicht zugestellt: %s", e)
        raise MailFehler(str(e)) from None


TEXTE = {
    "bestaetigen": {
        "de": ("Bitte bestätige deine E-Mail-Adresse für Taleward ({server})",
               "Hallo {name},\n\ndu hast diese Adresse für dein Konto „{benutzer}“ auf {server} eingetragen. Bitte "
               "bestätige sie hier (gilt 24 Stunden):\n\n{link}\n\nWir nutzen die Adresse nur, damit du dein Passwort "
               "selbst zurücksetzen kannst. Warst du das nicht, ignoriere diese Mail einfach.\n\n{betreiber}"),
        "en": ("Please confirm your e-mail address for Taleward ({server})",
               "Hello {name},\n\nyou entered this address for your account “{benutzer}” on {server}. Please confirm "
               "it here (valid for 24 hours):\n\n{link}\n\nWe only use the address so that you can reset your password "
               "yourself. If this wasn't you, simply ignore this e-mail.\n\n{betreiber}"),
    },
    "passwort": {
        "de": ("Neues Passwort für Taleward ({server})",
               "Hallo {name},\n\njemand – hoffentlich du – möchte das Passwort für dein Konto „{benutzer}“ auf "
               "{server} zurücksetzen. Hier wählst du ein neues (gilt eine Stunde und nur einmal):\n\n{link}\n\n"
               "Warst du das nicht, ignoriere diese Mail. Dein bisheriges Passwort bleibt dann gültig.\n\n{betreiber}"),
        "en": ("New password for Taleward ({server})",
               "Hello {name},\n\nsomeone – hopefully you – wants to reset the password for your account “{benutzer}” "
               "on {server}. Choose a new one here (valid for one hour and only once):\n\n{link}\n\nIf this wasn't "
               "you, ignore this e-mail. Your current password stays valid.\n\n{betreiber}"),
    },
    # 0.4.23: SL-Freigabe. Der Link führt nur zur Liste in der Verwaltung; entschieden wird dort nach Anmeldung.
    "sl_anfrage": {
        "de": ("SL-Freigabe angefragt: {name} ({server})",
               "Hallo,\n\n{name} (Benutzername „{benutzer}“) möchte auf {server} eigene Kampagnen leiten und bittet "
               "um die SL-Freigabe (angefragt {datum}).\n\nEntscheiden kannst du nach der Anmeldung in der "
               "Verwaltung:\n\n{link}\n\nDer Link selbst gibt nichts frei.\n\n{betreiber}"),
        "en": ("Game master approval requested: {name} ({server})",
               "Hello,\n\n{name} (username “{benutzer}”) would like to run their own campaigns on {server} and asks "
               "for game master approval (requested {datum}).\n\nYou can decide after signing in to the "
               "administration:\n\n{link}\n\nThe link itself does not approve anything.\n\n{betreiber}"),
    },
    "sl_freigegeben": {
        "de": ("Du kannst jetzt eigene Kampagnen leiten ({server})",
               "Hallo {name},\n\ndie Verwaltung von {server} hat deine SL-Freigabe erteilt. In der App kannst du "
               "jetzt eigene Kampagnen anlegen.\n\n{betreiber}"),
        "en": ("You can now run your own campaigns ({server})",
               "Hello {name},\n\nthe administrators of {server} have approved you as a game master. You can now "
               "create your own campaigns in the app.\n\n{betreiber}"),
    },
    "sl_abgelehnt": {
        "de": ("Deine Anfrage zur SL-Freigabe ({server})",
               "Hallo {name},\n\ndie Verwaltung von {server} hat deine Anfrage zur SL-Freigabe abgelehnt. In "
               "Kampagnen, zu denen du eingeladen wirst, spielst du weiter wie bisher. Fragen beantworten die "
               "Betreiber.\n\n{betreiber}"),
        "en": ("Your request for game master approval ({server})",
               "Hello {name},\n\nthe administrators of {server} have declined your request for game master "
               "approval. You keep playing in campaigns you are invited to. The operators can answer any "
               "questions.\n\n{betreiber}"),
    },
    "sl_entzogen": {
        "de": ("Deine SL-Freigabe wurde zurückgenommen ({server})",
               "Hallo {name},\n\ndie Verwaltung von {server} hat deine SL-Freigabe zurückgenommen. Neue Kampagnen "
               "kannst du nicht mehr anlegen; deine bestehenden Kampagnen bleiben.\n\n{betreiber}"),
        "en": ("Your game master approval was withdrawn ({server})",
               "Hello {name},\n\nthe administrators of {server} have withdrawn your game master approval. You can "
               "no longer create new campaigns; your existing campaigns stay.\n\n{betreiber}"),
    },
}


def text(db: Session, art: str, sprache: str, **werte) -> tuple[str, str]:
    from app.einstellungen import angaben

    a = angaben(db)
    from app.aliasse import zeichen_bereinigen

    werte = {"server": a.server_name, "betreiber": a.server_operator, **werte}
    # 0.4.130: Namen ohne Steuer- und Richtungszeichen (sie stehen auch im Betreff)
    werte = {k: zeichen_bereinigen(v) if k in ("server", "betreiber", "name") and isinstance(v, str) else v
             for k, v in werte.items()}
    betreff, inhalt = TEXTE[art]["en" if sprache == "en" else "de"]
    return betreff.format(**werte), inhalt.format(**werte)
