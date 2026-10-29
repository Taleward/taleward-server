"""Hinweis-Mails bei Änderungen am Konto (0.4.136): neue Anmeldung, Passwort neu gesetzt, Anmeldedienst verknüpft
oder getrennt, E-Mail-Adresse geändert (an die alte Adresse), zweiter Faktor an oder aus.

- Nur an Konten mit bestätigter Adresse (bzw. an die bisherige), nie an das Einrichtungskonto.
- Inhalt: was, wann (Uhrzeit), wie (Anmeldeart, grob App oder Browser). Keine IP-Adresse, kein Ort, nichts davon
  gespeichert. Kein Link, der etwas auslöst oder anmeldet.
- Höchstens eine Anmelde-Mail je Konto in 15 Minuten; spätere fallen still weg.
- Verschickt wird erst nach dem Festschreiben (Commit) und in einem eigenen Faden: Ein Fehler beim Versand verhindert
  nie die Anmeldung oder Änderung. Nach einem Rollback geht nichts raus.
- Schalter für den Betreiber: Verwaltung → Einstellungen → Benachrichtigungen (`melden.konto_hinweise`, Standard an).
"""
from __future__ import annotations

import logging
import threading

from sqlalchemy import event
from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import User

log = logging.getLogger("konto_mail")
ANLAESSE = ("anmeldung", "passwort", "dienst_neu", "dienst_weg", "email", "faktor_an", "faktor_aus")
ANMELDUNG_FENSTER = 15 * 60
_SCHLUESSEL = "konto_mails"

# Wo bzw. auf welchem Weg (de, en)
ORTE = {
    "app": ("in der App", "in the app"),
    "browser": ("im Browser", "in a browser"),
    "verwaltung": ("in der Verwaltung", "in the administration"),
    "guthaben": ("auf der Guthaben-Seite", "on the credit page"),
    "link": ("über den Link aus der Mail", "via the link from the e-mail"),
    "durch_verwaltung": ("durch die Verwaltung", "by the administrators"),
    "server": ("direkt am Server", "directly on the server"),
}


def an(db: Session) -> bool:
    from app.einstellungen import meta_lesen

    return (meta_lesen(db, "melden.konto_hinweise") or "1") == "1"


def ort_aus(request) -> str:
    """Grob App oder Browser aus dem User-Agent (nur für den Text, nicht gespeichert)."""
    ua = request.headers.get("user-agent") or ""
    if "Taleward" in ua or "; wv)" in ua or not ua.startswith("Mozilla/"):
        return "app"
    return "browser"


def vormerken(db: Session, user: User | None, anlass: str, *, ort: str | None = None, dienst: str | None = None,
              an_adresse: str | None = None) -> None:
    """Hinweis vormerken; verschickt wird nach dem Commit dieser Sitzung."""
    from app.begrenzung import ZAEHLER

    if user is None or user.setup_account or anlass not in ANLAESSE:
        return
    adresse = an_adresse or user.email
    if not adresse:
        return
    if anlass == "anmeldung" and not ZAEHLER.versuch(f"konto-mail:{user.id}", 1, ANMELDUNG_FENSTER):
        return
    _horchen()
    db.info.setdefault(_SCHLUESSEL, []).append({"user_id": user.id, "anlass": anlass, "ort": ort, "dienst": dienst,
                                                "an": adresse, "wann": utcnow()})


_GEHORCHT = False


def _horchen() -> None:
    global _GEHORCHT
    if _GEHORCHT:
        return
    event.listen(Session, "after_commit", _nach_commit)
    event.listen(Session, "after_rollback", _nach_rollback)
    _GEHORCHT = True


def _nach_commit(session: Session) -> None:
    posten = session.info.pop(_SCHLUESSEL, None)
    if posten:
        _starten(posten)


def _nach_rollback(session: Session) -> None:
    session.info.pop(_SCHLUESSEL, None)


def _starten(posten: list[dict]) -> None:
    # kein Daemon: auch ein kurzer Befehl (Passwort am Server setzen) wartet, bis die Mail raus ist
    threading.Thread(target=senden_alle, args=(posten,), name="konto-mail").start()


def _wie(p: dict, sprache: str) -> str:
    from app import anmeldedienste

    i = 1 if sprache == "en" else 0
    ort = ORTE.get(p["ort"] or "", ("", ""))[i]
    dienst = anmeldedienste.DIENSTE.get(p["dienst"] or "", {}).get("name") if p["dienst"] else None
    if p["anlass"] in ("dienst_neu", "dienst_weg"):
        return dienst or "?"
    if p["anlass"] == "anmeldung":
        art = (f"via {dienst}" if i else f"über {dienst}") if dienst else ("with password" if i else "mit Passwort")
        return f"{art} {ort}".strip()
    return ort


def _wann(p: dict, sprache: str, zone=None) -> str:
    """0.4.141: Ortszeit des Servers (Verwaltung → Einstellungen → Benachrichtigungen) statt UTC."""
    from datetime import timezone

    from app.benachrichtigung import ortszeit

    return ortszeit(p["wann"], zone or timezone.utc, sprache)


def senden_alle(posten: list[dict]) -> None:
    from app import mail
    from app.db import session_factory

    try:
        with session_factory()() as db:
            if not an(db) or not mail.kann_senden(db):
                return
            from app.benachrichtigung import zeitzone

            zone = zeitzone(db)
            for p in posten:
                u = db.get(User, p["user_id"])
                if u is None:
                    continue
                sprache = mail.sprache_fuer(db, u)
                betreff, text, html = mail.text(db, f"konto_{p['anlass']}", sprache, name=u.display_name,
                                                benutzer=u.username, wann=_wann(p, sprache, zone), wie=_wie(p, sprache))
                try:
                    mail.senden(db, p["an"], betreff, text, html)
                except (mail.MailFehler, ValueError):
                    log.warning("Konto-Hinweis nicht zugestellt (Konto %s)", u.id)
    except Exception:  # noqa: BLE001 – ein Hinweis darf nie etwas anderes stören
        log.exception("Konto-Hinweise fehlgeschlagen")
