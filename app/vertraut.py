"""Vertraute Geräte beim zweiten Faktor (S44, 0.4.143; Entwurf `claude/server-vorschlag-vertraute-geraete.md`, vom
Sicherheits-Chat am 20.10. beantwortet).

Nach dem Code oder dem Passkey kann die Person „Diesem Gerät 30 Tage vertrauen“ ankreuzen. Dann entfällt auf diesem
Browser beim nächsten Anmelden nur der zweite Schritt – das Passwort bleibt immer Pflicht, und Schritte, die den Faktor
frisch verlangen (Geld, Schlüssel, Verwalter, der Faktor selbst), fragen ihn trotzdem (zweiter_faktor.frisch).

- Merkzeichen: 32 Byte aus ``secrets`` im Cookie, in der Datenbank nur SHA-256 (je Bereich Verwaltung/Guthaben).
- Cookie ``__Host-tw_vertraut`` auf https (Secure, Path=/), am Einzelplatz (localhost) ``tw_vertraut``; HttpOnly,
  SameSite=Strict. Über http im Heimnetz wird nichts angeboten.
- Fest 30 Tage ab Anlage, keine Verlängerung. Gebunden an Konto, ``token_version`` (Passwortwechsel) und Faktor-Fassung
  (Faktor aus/neu, Passkey entfernt, neue Wiederherstellungscodes). Höchstens 10 je Konto und Bereich.
- Das Merkzeichen wird erst nach richtigem Passwort angesehen. Ein ungültiges führt still zum Code-Schritt.
- Neues vertrautes Gerät: Hinweis an die Person (Konto-Mail) und an den Betreiber (Meldewege), ohne Merkzeichen und
  ohne Adresse; ohne Meldeweg steht der Hinweis beim nächsten Anmelden auf der Übersicht.
"""
from __future__ import annotations

import hashlib
import secrets
from datetime import timedelta

from fastapi import Request, Response
from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.aliasse import zeichen_bereinigen
from app.db import utcnow
from app.einstellungen import meta_lesen, meta_schreiben
from app.models import ServerMeta, User, VertrautesGeraet

TAGE = 30
HOECHSTENS = 10
COOKIE_SICHER = "__Host-tw_vertraut"
COOKIE_LOKAL = "tw_vertraut"
BEREICHE = ("verwaltung", "guthaben")


def _hash(wert: str, bereich: str) -> str:
    return hashlib.sha256(f"{bereich}:{wert}".encode()).hexdigest()


def _lokal(request: Request) -> bool:
    return (request.url.hostname or "").lower() in ("localhost", "127.0.0.1", "::1")


def angeboten(request: Request) -> bool:
    """Nur über https oder am PC selbst (Einzelplatz) – im Heimnetz über http liefe das Merkzeichen 30 Tage im Klartext."""
    return request.url.scheme == "https" or _lokal(request)


def _cookie_name(request: Request) -> str:
    return COOKIE_SICHER if request.url.scheme == "https" else COOKIE_LOKAL


def _wert(request: Request) -> str:
    wert = request.cookies.get(COOKIE_SICHER) or request.cookies.get(COOKIE_LOKAL) or ""
    return wert if 16 <= len(wert) <= 200 else ""


def pruefen(request: Request, db: Session, user: User, bereich: str) -> bool:
    """Gültiges Merkzeichen für dieses Konto und diesen Bereich? Nur nach richtigem Passwort aufrufen."""
    from app.verwaltung import zweiter_faktor

    wert = _wert(request)
    if not wert or not angeboten(request):
        return False
    g = db.get(VertrautesGeraet, _hash(wert, bereich))
    if g is None or g.user_id != user.id or g.bereich != bereich:
        return False
    if g.expires_at <= utcnow() or g.token_version != user.token_version \
            or g.faktor_fassung != zweiter_faktor.faktor_fassung(db, user.id):
        db.delete(g)  # abgelaufen oder ungültig: weg, Code-Schritt wie immer
        return False
    g.last_used_at = utcnow()
    return True


def bezeichnung(ua: str) -> str:
    from app.passkeys import namensvorschlag

    return zeichen_bereinigen(namensvorschlag(ua))[:60] or "Browser"


def merken(antwort: Response, request: Request, db: Session, user: User, bereich: str) -> VertrautesGeraet | None:
    """Nach dem zweiten Faktor: Merkzeichen setzen und Eintrag anlegen. None, wenn hier nichts angeboten wird."""
    from app.verwaltung import zweiter_faktor

    if not angeboten(request) or bereich not in BEREICHE:
        return None
    wert = _wert(request) or secrets.token_urlsafe(32)  # ein Browser, ein Merkzeichen – je Bereich ein Eintrag
    jetzt = utcnow()
    alt = db.get(VertrautesGeraet, _hash(wert, bereich))
    if alt is not None:
        db.delete(alt)
        db.flush()
    g = VertrautesGeraet(id=_hash(wert, bereich), user_id=user.id, bereich=bereich, token_version=user.token_version,
                         faktor_fassung=zweiter_faktor.faktor_fassung(db, user.id),
                         bezeichnung=bezeichnung(request.headers.get("user-agent", "")), created_at=jetzt,
                         expires_at=jetzt + timedelta(days=TAGE), last_used_at=jetzt)
    db.add(g)
    db.flush()
    vorhandene = list(db.scalars(select(VertrautesGeraet).where(VertrautesGeraet.user_id == user.id,
                                                                VertrautesGeraet.bereich == bereich)
                                 .order_by(VertrautesGeraet.created_at.desc())))
    for zu_alt in vorhandene[HOECHSTENS:]:
        db.delete(zu_alt)
    antwort.set_cookie(_cookie_name(request), wert, max_age=TAGE * 86400, httponly=True, samesite="strict",
                       secure=request.url.scheme == "https", path="/")
    _melden(db, user, g)
    return g


def _melden(db: Session, user: User, g: VertrautesGeraet) -> None:
    """Hinweis an die Person (Konto-Mail) und den Betreiber (Meldewege); ohne Meldeweg Hinweis auf der Übersicht."""
    from app import benachrichtigung, konto_mail
    from app.einstellungen import angaben

    konto_mail.vormerken(db, user, "vertraut", ort=g.bereich)
    gemeldet = False
    try:
        k = benachrichtigung.konfig(db)
        if k.aktiv:
            name = angaben(db).server_name or "Taleward"
            benachrichtigung.senden(db, f"Neues vertrautes Gerät ({name})",
                                    f"Für das Konto „{user.username}“ wurde gerade ein Browser als vertraut eingetragen "
                                    f"({g.bezeichnung}, Bereich {g.bereich}, 30 Tage). Warst du das nicht? In der "
                                    "Verwaltung unter Anmeldung → Vertraute Geräte entfernen.", wichtig=False)
            gemeldet = True
    except Exception:  # noqa: BLE001 – ein Hinweis darf das Anmelden nie stören
        pass
    if not gemeldet:
        meta_schreiben(db, f"vertraut.hinweis.{user.id}", f"{g.bezeichnung}|{g.created_at.isoformat()}")


def hinweis_abholen(db: Session, user_id: str) -> str | None:
    """Beim nächsten Anmelden einmal zeigen: „Neues vertrautes Gerät seit …“ (nur ohne Meldeweg gesetzt)."""
    schluessel = f"vertraut.hinweis.{user_id}"
    wert = meta_lesen(db, schluessel)
    row = db.get(ServerMeta, schluessel)
    if row is not None:
        db.delete(row)
    return wert or None


def liste(db: Session, user_id: str) -> list[VertrautesGeraet]:
    return list(db.scalars(select(VertrautesGeraet).where(VertrautesGeraet.user_id == user_id)
                           .order_by(VertrautesGeraet.created_at.desc())))


def entfernen(db: Session, user_id: str, geraet_id: str | None = None) -> int:
    """Eines oder (ohne ID) alle vertrauten Geräte eines Kontos entfernen."""
    bed = [VertrautesGeraet.user_id == user_id]
    if geraet_id:
        bed.append(VertrautesGeraet.id == geraet_id)
    return db.execute(delete(VertrautesGeraet).where(*bed)).rowcount


def aufraeumen(db: Session) -> int:
    return db.execute(delete(VertrautesGeraet).where(VertrautesGeraet.expires_at < utcnow())).rowcount
