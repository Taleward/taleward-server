"""0.4.167: Spielleitungen erfahren außerhalb der App, wie das Guthaben geht – und bekommen auf Wunsch des Betreibers ein
Startguthaben für die erste Runde.

Info-Mail an heutige Spielleitungen (Verwaltung → Abrechnung, „Spielleitungen über das Guthaben informieren“; nach dem
Einschalten des Guthabens bietet die Seite es von selbst an): einmal je Konto (Merker `users.guthaben_info_at`, den auch
die Begrüßungs-Mail und `sl_freigegeben_guthaben` setzen), nur mit bestätigter Adresse, ohne Abbestellung, Link nur aus
der eingetragenen öffentlichen Adresse. Vorher Rückfrage mit der Zahl der Empfänger; dazu frischer zweiter Faktor
und ein Eintrag im Abrechnungsprotokoll.

Startguthaben (Verwaltung → Abrechnung → Preise, Standard 0 = aus): nur im Modus „Guthaben je Spielleitung“, einmal je
Konto und je E-Mail-Adresse über alle Konten – auch gelöschte. Gespeichert wird dafür nur eine Prüfsumme der
normalisierten Adresse (klein, „+…“ im lokalen Teil weg, bei Gmail ohne Punkte; HMAC mit einem eigenen Schlüssel des Servers), die nach
`AUFBEWAHRUNG` gelöscht wird. Gutgeschrieben wird als Gutschrift (nie erstattbar) auf das eigene Guthaben der Person, nie
auf einen Topf. Auslöser: das Anlegen einer Kampagne als SL und das Bestätigen einer E-Mail-Adresse (wer schon SL ist).
Ist die Grenze je Kalendermonat für den ganzen Server erreicht, gibt es keins – ohne Fehler, mit Protokolleintrag.
"""
from __future__ import annotations

import hashlib
import hmac
import logging
import secrets
from datetime import datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import abrechnung
from app.db import utcnow
from app.models import Member, StartguthabenAdresse, User

log = logging.getLogger("startguthaben")
SALZ = "startguthaben.salz"  # geheim (app/dpapi.py)
AUFBEWAHRUNG_JAHRE = 3
GLEICHE_POSTFAECHER = {"gmail.com": "gmail.com", "googlemail.com": "gmail.com"}
AUFBEWAHRUNG = timedelta(days=365 * AUFBEWAHRUNG_JAHRE)


# ---------------------------------------------------------------- Prüfsumme der Adresse
def normalisiert(adresse: str) -> str:
    lokal, _, domain = (adresse or "").strip().lower().rpartition("@")
    lokal = lokal.split("+", 1)[0]
    if domain in GLEICHE_POSTFAECHER:  # dort landen alle Schreibweisen mit Punkten im selben Postfach
        lokal, domain = lokal.replace(".", ""), GLEICHE_POSTFAECHER[domain]
    return f"{lokal}@{domain}" if lokal and domain else ""


def _salz(db: Session) -> bytes:
    from app.einstellungen import meta_lesen, meta_schreiben

    wert = meta_lesen(db, SALZ)
    if not wert:
        wert = secrets.token_hex(32)
        meta_schreiben(db, SALZ, wert)
        db.flush()
    return wert.encode()


def pruefsumme(db: Session, adresse: str) -> str | None:
    norm = normalisiert(adresse)
    if not norm:
        return None
    return hmac.new(_salz(db), norm.encode(), hashlib.sha256).hexdigest()


# ---------------------------------------------------------------- Wer ist heute SL, wer bekommt Mail
def ist_sl(db: Session, user: User) -> bool:
    return db.scalar(select(Member.id).where(Member.user_id == user.id, Member.role == "gm").limit(1)) is not None


def mail_moeglich(db: Session, u: User) -> bool:
    return bool(u.email) and not u.setup_account and not u.guthaben_mails_aus


def info_empfaenger(db: Session) -> list[User]:
    """Heutige SL mit bestätigter Adresse, ohne Abbestellung, noch nicht informiert."""
    ids = select(Member.user_id).where(Member.role == "gm").distinct()
    kandidaten = db.scalars(select(User).where(User.id.in_(ids), User.guthaben_info_at.is_(None))
                            .order_by(User.username)).all()
    return [u for u in kandidaten if mail_moeglich(db, u)]


def _senden(db: Session, u: User, art: str, basis: str, **werte) -> bool:
    from app import mail

    betreff, text, html = mail.text(db, art, mail.sprache_fuer(db, u), name=u.display_name, benutzer=u.username,
                                    link=f"{basis}/guthaben", **werte)
    try:
        mail.senden(db, u.email, betreff, text, html)
    except (mail.MailFehler, ValueError):
        log.warning("Mail %s nicht zugestellt (Konto %s)", art, u.id)
        return False
    u.guthaben_info_at = utcnow()
    return True


def _bereit(db: Session) -> str | None:
    """Öffentliche Adresse, wenn Mails zum Guthaben gerade gehen – sonst None."""
    from app import mail
    from app.einstellungen import mail_adresse

    if abrechnung.modus(db) != "guthaben" or not mail.kann_senden(db):
        return None
    return mail_adresse(db) or None


def info_senden(db: Session, von: str) -> tuple[int, int]:
    """Info-Mail an alle `info_empfaenger`. Gibt (zugestellt, nicht zugestellt) zurück."""
    basis = _bereit(db)
    if basis is None:
        return 0, 0
    gut = schlecht = 0
    for u in info_empfaenger(db):
        if _senden(db, u, "guthaben_info", basis):
            gut += 1
        else:
            schlecht += 1
        db.flush()
    abrechnung.protokoll(db, von, "guthaben_info", zugestellt=gut, nicht_zugestellt=schlecht)
    return gut, schlecht


# ---------------------------------------------------------------- Startguthaben
def _monat_beginn(jetzt: datetime) -> datetime:
    return jetzt.replace(day=1, hour=0, minute=0, second=0, microsecond=0)


def im_monat(db: Session) -> int:
    return int(db.scalar(select(func.coalesce(func.sum(StartguthabenAdresse.betrag_cent), 0)).where(
        StartguthabenAdresse.angelegt >= _monat_beginn(utcnow()))) or 0)


def betrag(db: Session) -> int:
    p = abrechnung.preise(db)
    return min(p.startguthaben, p.gutschrift_max)


def gewaehren(db: Session, u: User) -> int:
    """Startguthaben, wenn alles passt. Gibt den gutgeschriebenen Betrag zurück (0 = keins)."""
    b = betrag(db)
    if (b <= 0 or abrechnung.modus(db) != "guthaben" or u.startguthaben_at is not None or u.setup_account
            or not u.email or not ist_sl(db, u)):
        return 0
    summe = pruefsumme(db, u.email)
    if summe is None or db.get(StartguthabenAdresse, summe) is not None:
        u.startguthaben_at = utcnow()  # diese Adresse hatte schon eins – das Konto nicht weiter prüfen
        return 0
    if im_monat(db) + b > abrechnung.preise(db).startguthaben_monat:
        abrechnung.protokoll(db, "system", "startguthaben_grenze", user_id=u.id, betrag_cent=b)
        return 0
    g = abrechnung.guthaben_konto(db, u.id)
    db.add(StartguthabenAdresse(adresse_hash=summe, betrag_cent=b))
    abrechnung.buchen(db, g, "gutschrift", b, von="system", grund="Startguthaben")
    abrechnung.protokoll(db, "system", "startguthaben", g.id, betrag_cent=b)
    u.startguthaben_at = utcnow()
    db.flush()
    return b


def begruessen(db: Session, u: User) -> None:
    """Nach dem Anlegen einer Kampagne bzw. dem Bestätigen der Adresse: Startguthaben prüfen, dann einmal die
    Begrüßungs-Mail (mit Startguthaben, wenn es eins gab). Fehler beim Versand ändern nichts am Ablauf."""
    if abrechnung.modus(db) != "guthaben" or not ist_sl(db, u):
        return
    b = gewaehren(db, u)
    basis = _bereit(db)
    if basis is None or u.guthaben_info_at is not None or not mail_moeglich(db, u):
        return
    if b:
        _senden(db, u, "guthaben_willkommen_start", basis, betrag=abrechnung.euro(b))
    else:
        _senden(db, u, "guthaben_willkommen", basis)


def aufraeumen(db: Session) -> int:
    """Wartung: Prüfsummen nach `AUFBEWAHRUNG` löschen."""
    alt = db.scalars(select(StartguthabenAdresse).where(StartguthabenAdresse.angelegt < utcnow() - AUFBEWAHRUNG)).all()
    for z in alt:
        db.delete(z)
    return len(alt)
