"""Verarbeitungsstand für das Taleward-Programm auf dem PC (Einzelplatz, Übersicht und Verarbeitung).

Nur Zahlen, Zeiten und Schritte. Titel, Kapitelnummer, Datum und Länge einer Runde nur für Kampagnen, die das Konto des
Programms leitet – alles andere erscheint als Runde einer anderen Spielleitung, ohne Angaben. Nie Abschriften, Notizen,
Namen von Unterlagen oder andere Inhalte.
"""
from datetime import datetime

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Campaign, CampaignDocument, GameSession, Job, Member, OrgMember, User

META_KONTO = "einzelplatz.konto"
SCHRITTE = {"transcribe": "transcribing", "summarize": "summarizing", "revise": "revising", "document": "document",
            "voice_enroll": "voice"}
ZULETZT = 10


def konto_id(db: Session) -> str | None:
    """Konto des Programms: beim Einrichten gemerkt; bei älteren Einrichtungen das älteste Verwalter-Konto."""
    from app.einstellungen import meta_lesen

    gemerkt = meta_lesen(db, META_KONTO)
    if gemerkt and db.get(User, gemerkt) is not None:
        return gemerkt
    return db.scalar(select(User.id).join(OrgMember, OrgMember.user_id == User.id)
                     .where(OrgMember.role == "admin", User.setup_account.is_(False))
                     .order_by(User.created_at).limit(1))


def _geleitet(db: Session, konto: str | None) -> set[str]:
    if konto is None:
        return set()
    return set(db.scalars(select(Member.campaign_id).where(Member.user_id == konto, Member.role == "gm",
                                                           Member.left_at.is_(None))))


def _zeit(t: datetime | None) -> str | None:
    return t.isoformat().replace("+00:00", "Z") if t is not None else None


def _auftrag(db: Session, j: Job, geleitet: set[str], konto: str | None) -> dict:
    s = db.get(GameSession, j.session_id) if j.session_id else None
    kampagne = s.campaign_id if s else None
    if kampagne is None and j.document_id:
        dok = db.get(CampaignDocument, j.document_id)
        kampagne = dok.campaign_id if dok else None
    eigen = kampagne in geleitet if kampagne else (j.type == "voice_enroll" and j.owner_user_id == konto)
    c = db.get(Campaign, kampagne) if eigen and kampagne else None
    return {
        "step": SCHRITTE.get(j.type, "other"),
        "own": bool(eigen),
        "campaignTitle": c.title if c else None,
        "chapterNumber": s.number if eigen and s else None,
        "playedAt": _zeit(s.played_at) if eigen and s else None,
        "durationSeconds": s.duration_seconds if eigen and s else None,
    }


def uebersicht(db: Session) -> dict:
    konto = konto_id(db)
    geleitet = _geleitet(db, konto)
    laufend = db.scalar(select(Job).where(Job.state == "leased").order_by(Job.started_at.is_(None), Job.started_at)
                        .limit(1))
    aktuell = None
    if laufend is not None:
        aktuell = _auftrag(db, laufend, geleitet, konto) | {
            # nur, was der Worker selbst gemeldet hat – ohne verlässlichen Wert zeigt das Programm nur den Schritt
            "progress": laufend.progress, "startedAt": _zeit(laufend.started_at)}
    fertig = []
    for j in db.scalars(select(Job).where(Job.state.in_(("done", "failed")), Job.finished_at.is_not(None))
                        .order_by(Job.finished_at.desc()).limit(ZULETZT)):
        dauer = (int(round((j.finished_at - j.started_at).total_seconds()))
                 if j.started_at is not None and j.finished_at >= j.started_at else None)
        fertig.append(_auftrag(db, j, geleitet, konto) | {
            "result": j.state, "finishedAt": _zeit(j.finished_at), "seconds": dauer})
    warten = db.scalar(select(func.count()).select_from(Job).where(Job.state == "queued")) or 0
    pruefen = (db.scalar(select(func.count()).select_from(GameSession).where(
        GameSession.campaign_id.in_(geleitet), GameSession.state == "awaiting_review")) or 0) if geleitet else 0
    return {"current": aktuell, "queued": warten, "recent": fertig, "awaitingReview": pruefen}
