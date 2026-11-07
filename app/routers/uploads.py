"""Stückweiser Upload der Aufnahme: starten, Teile senden (idempotent), fehlende Teile abfragen, abschließen."""
import hashlib
import math
import threading

from typing import Any, Literal

from fastapi import APIRouter, Depends, Header, Request, Response
from fastapi.security import HTTPAuthorizationCredentials
from pydantic import Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors, schemas, storage
from app.access import _bearer, current_user, load_session_gm, membership
from app.config import get_settings
from app.db import get_db, utcnow
from app.errors import sprache
from app.models import GameSession, Upload, UploadFile, User
from app.queue import create_transcribe_job
from app.services import check_upload_consent, processing_status, set_state

router = APIRouter(tags=["Upload"])

ERLAUBTE_MIME = ("audio/", "video/webm", "video/mp4", "application/ogg")


class FileIn(schemas.ApiModel):
    file_name: str = Field(min_length=1, max_length=300)
    size_bytes: int = Field(gt=0)
    mime_type: str = Field(min_length=1, max_length=100)
    track_member_id: str | None = None
    track_attendee_id: str | None = Field(default=None, max_length=64)  # 0.4.32


def nutzer_oder_bot(request: Request, creds: HTTPAuthorizationCredentials | None = Depends(_bearer),
                    db: Session = Depends(get_db)):
    """0.4.32: Upload mit Nutzer-Token (SL) oder mit dem Schlüssel des Discord-Bots (nur eigene Runden)."""
    if creds is None and request.headers.get("x-taleward-bot"):
        from app.routers.discord import bot_schluessel

        return None, bot_schluessel(request, db)
    return current_user(request, creds, db), None


class UploadStart(schemas.ApiModel):
    source: Literal["table", "discord"]
    files: list[FileIn] = Field(max_length=500)
    # 0.4.28: Zeiten, in denen aufgenommen wurde (nur table). Bewusst ohne Schema: passt etwas nicht, verwirft der
    # Server die Spannen still (app/spielende.spannen_pruefen) – nie ein Fehler, nie ein Hindernis für das Hochladen
    spans: Any = None


class UploadFileOut(schemas.ApiModel):
    file_id: str
    file_name: str
    chunk_count: int


class UploadStartOut(schemas.ApiModel):
    upload_id: str
    chunk_size_bytes: int
    files: list[UploadFileOut]


class MissingOut(schemas.ApiModel):
    file_id: str
    missing_chunks: list[int]


class UploadStatusOut(schemas.ApiModel):
    files: list[MissingOut]


def _start_out(up: Upload) -> UploadStartOut:
    return UploadStartOut(
        upload_id=up.id, chunk_size_bytes=up.chunk_size,
        files=[UploadFileOut(file_id=f.id, file_name=f.file_name, chunk_count=f.chunk_count) for f in up.files],
    )


def _gleiche_dateien(up: Upload, body: UploadStart) -> bool:
    alt = [(f.file_name, f.size_bytes, f.track_attendee_id or f.track_member_id) for f in up.files]
    neu = [(f.file_name, f.size_bytes, f.track_attendee_id or f.track_member_id) for f in body.files]
    return up.source == body.source and alt == neu


def _pruefe_dateien(s: GameSession, body: UploadStart) -> None:
    if not body.files:
        raise errors.bad_request("files_empty")
    maxb = get_settings().max_file_bytes
    for f in body.files:
        mime = f.mime_type.lower()
        if not mime.startswith(ERLAUBTE_MIME):
            raise errors.bad_request("unsupported_audio", name=f.file_name)
        if f.size_bytes > maxb:
            raise errors.ApiError(413, "file_too_large", name=f.file_name)
    if body.source == "table":
        if any(f.track_member_id or f.track_attendee_id for f in body.files):
            raise errors.bad_request("track_member_unexpected")
    else:
        anwesend = {a.member_id: a.member_id for a in s.attendees if a.member_id}
        anwesend.update({a.id: a.member_id for a in s.attendees})  # 0.4.32: trackAttendeeId (auch Gäste)
        ids = [(f.track_attendee_id or None) if f.track_attendee_id else f.track_member_id for f in body.files]
        if any(i is None or i not in anwesend for i in ids) or len(set(ids)) != len(ids):
            raise errors.bad_request("track_member_invalid")
        if any(f.track_member_id and f.track_attendee_id for f in body.files):
            raise errors.bad_request("track_member_invalid")


def _spur_mitglied(s: GameSession, f: FileIn) -> str | None:
    """trackMemberId der Datei – bei trackAttendeeId das Mitglied dieses Anwesenden (Gäste: keins)."""
    if f.track_attendee_id:
        a = next((x for x in s.attendees if x.id == f.track_attendee_id), None)
        return a.member_id if a else None
    return f.track_member_id


@router.post("/sessions/{sessionId}/uploads", status_code=201, response_model=UploadStartOut)
def start_upload(sessionId: str, body: UploadStart, wer=Depends(nutzer_oder_bot), db: Session = Depends(get_db)):
    user, bot = wer
    if bot is not None:  # 0.4.32 (B9, Y3): nur eigene Runden, nur Spuren mit Zustimmung
        from app import discord_bot

        s = db.get(GameSession, sessionId)
        if s is None or s.bot_key_id != bot.id:
            raise errors.not_found("session")
        if body.source != "discord":
            raise errors.bad_request("track_member_invalid")
        offen = db.scalar(select(Upload).where(Upload.session_id == s.id, Upload.state == "open"))
        if offen is not None:
            if _gleiche_dateien(offen, body):
                return _start_out(offen)
            raise errors.conflict("upload_in_progress")
        discord_bot.upload_pruefen(db, bot, s, body.files)
    else:
        s = load_session_gm(db, sessionId, user).session
        check_upload_consent(db, s)
        offen = db.scalar(select(Upload).where(Upload.session_id == s.id, Upload.state == "open"))
        if offen is not None:
            if _gleiche_dateien(offen, body):
                return _start_out(offen)  # App setzt einen abgebrochenen Upload fort
            raise errors.conflict("upload_in_progress")
        # 0.4.32: Bot-Runde ohne Ton (no_recording) – die SL kann von Hand hochladen
        if s.state not in ("created", "failed", "no_recording"):
            raise errors.conflict("invalid_state", "invalid_state.upload")
    _pruefe_dateien(s, body)
    from app import abrechnung

    wartend = abrechnung.wartende_aufnahme(db, s)  # 0.4.24: ohne Guthaben höchstens eine wartende Aufnahme
    if wartend is not None:
        from app.access import player_can_see_session

        if user is None:
            raise errors.ApiError(409, "budget_recording_waiting")
        m = membership(db, wartend.campaign_id, user)  # wie GET /sessions/{id}: sonst fehlt die Angabe
        sichtbar = m is not None and (m.role == "gm" or player_can_see_session(wartend))
        raise errors.ApiError(409, "budget_recording_waiting",
                              details={"sessionId": wartend.id} if sichtbar else None)
    # Neuanfang nach Fehlschlag: altes Audio verwerfen
    for alt in db.scalars(select(Upload).where(Upload.session_id == s.id, Upload.state == "completed")):
        storage.delete_upload_files(alt.id)
        alt.state = "aborted"
    chunk = get_settings().chunk_size_bytes
    up = Upload(session_id=s.id, source=body.source, chunk_size=chunk)
    if body.source == "table":
        from app import spielende

        up.spans = spielende.spannen_pruefen(body.spans, s.played_at)
    up.files = [
        UploadFile(position=i, file_name=f.file_name, size_bytes=f.size_bytes, mime_type=f.mime_type,
                   track_member_id=_spur_mitglied(s, f), track_attendee_id=f.track_attendee_id or None,
                   chunk_count=math.ceil(f.size_bytes / chunk))
        for i, f in enumerate(body.files)
    ]
    db.add(up)
    s.source = body.source
    s.audio_deleted_at = None
    s.duration_seconds = None
    set_state(s, "uploading")
    db.commit()
    db.refresh(up)
    return _start_out(up)


def _load_upload(db: Session, upload_id: str, user: User | None, bot=None) -> tuple[Upload, GameSession]:
    up = db.get(Upload, upload_id)
    if up is None:
        raise errors.not_found()
    s = db.get(GameSession, up.session_id)
    if bot is not None:  # 0.4.32: der Bot nur bei Runden, die er selbst angelegt hat, und nur mit gültiger Verbindung
        from app import discord_bot

        if s.bot_key_id != bot.id or discord_bot.verbindung(db, s.campaign_id) is None:
            raise errors.not_found()
        return up, s
    m = membership(db, s.campaign_id, user)
    if m is None or m.role != "gm":  # Uploads gehören der SL; für alle anderen existieren sie nicht
        raise errors.not_found()
    return up, s


def _expected_size(up: Upload, f: UploadFile, index: int) -> int:
    if index < f.chunk_count - 1:
        return up.chunk_size
    return f.size_bytes - up.chunk_size * (f.chunk_count - 1)


@router.put("/uploads/{uploadId}/files/{fileId}/chunks/{index}", status_code=204)
async def put_chunk(
    uploadId: str, fileId: str, index: int, request: Request,
    x_chunk_sha256: str | None = Header(default=None),
    wer=Depends(nutzer_oder_bot), db: Session = Depends(get_db),
):
    user, bot = wer
    up, _ = _load_upload(db, uploadId, user, bot)
    f = next((x for x in up.files if x.id == fileId), None)
    if f is None:
        raise errors.not_found()
    if up.state != "open":
        raise errors.conflict("upload_closed")
    if bot is not None and f.track_attendee_id:  # 0.4.32 (Y3, B8): beim Bot Zustimmung bei jedem Teil
        from app import discord_bot

        discord_bot.teil_pruefen(db, up, f)
    if index < 0 or index >= f.chunk_count:
        raise errors.bad_request("chunk_index_invalid", index=index)
    erwartet = _expected_size(up, f, index)
    from app.koerper import lesen

    data = await lesen(request, up.chunk_size)
    if len(data) != erwartet:
        raise errors.bad_request("chunk_size_mismatch", index=index, got=len(data), expected=erwartet)
    if x_chunk_sha256 and hashlib.sha256(data).hexdigest() != x_chunk_sha256.strip().lower():
        raise errors.bad_request("chunk_checksum_mismatch", index=index)
    import anyio

    # 0.4.99: Sperre, Datenbank und Schreiben in einem Faden – nicht im Ereignis-Takt
    await anyio.to_thread.run_sync(_teil_schreiben, db, up, f.id, index, data)
    return Response(status_code=204)


def _teil_schreiben(db: Session, up: Upload, file_id: str, index: int, data: bytes) -> None:
    with _SCHREIBEN:  # 0.4.94: Prüfung und Schreiben zusammen, damit die Messung nie einen halben Tausch sieht
        db.refresh(up)
        if up.state != "open":  # 0.4.91: inzwischen abgeschlossen oder in Messung – nichts mehr tauschen
            raise errors.conflict("upload_closed")
        storage.write_atomic(storage.chunk_path(up.id, file_id, index), data)


def _missing(up: Upload) -> dict[str, list[int]]:
    return {
        f.id: [i for i in range(f.chunk_count) if not storage.chunk_path(up.id, f.id, i).exists()]
        for f in up.files
    }


@router.get("/uploads/{uploadId}", response_model=UploadStatusOut)
def upload_status(uploadId: str, wer=Depends(nutzer_oder_bot), db: Session = Depends(get_db)):
    up, _ = _load_upload(db, uploadId, *wer)
    fehlend = _missing(up) if up.state == "open" else {f.id: [] for f in up.files}
    return UploadStatusOut(files=[MissingOut(file_id=k, missing_chunks=v) for k, v in fehlend.items()])


_LAEUFT = object()
_IN_MESSUNG: set[str] = set()
_MESSUNG_SPERRE = threading.Lock()
_SCHREIBEN = threading.Lock()  # 0.4.94: Prüfen und Schreiben eines Teils und der Wechsel auf „measuring“ schließen sich aus
_MESSPLAETZE = threading.BoundedSemaphore(2)  # höchstens zwei Messungen zugleich (Dekodieren kostet Rechenzeit)
MESSPLATZ_WARTEN_S = 60  # 0.4.99: Fäden nicht lange belegen (vorher 600 s) – danach 429 wie bisher


def _messen(db: Session, up: Upload):
    """0.4.91: Das Dekodieren dauert (etwa 12 s je Stunde). Solange gemessen wird, steht der Upload auf
    „measuring“: keine Teile mehr (sonst ließe sich nach der Messung tauschen) und kein zweites Messen. Ein Stand
    „measuring“ ohne laufende Messung in diesem Prozess (Neustart mittendrin) gilt wieder als offen."""
    from app import abrechnung

    with _MESSUNG_SPERRE:
        if up.id in _IN_MESSUNG:
            return _LAEUFT
        _IN_MESSUNG.add(up.id)
    try:
        with _SCHREIBEN:
            up.state = "measuring"
            db.commit()
        if not _MESSPLAETZE.acquire(timeout=MESSPLATZ_WARTEN_S):
            up.state = "open"
            db.commit()
            raise errors.ApiError(429, "too_many_requests")
        try:
            return abrechnung.aufnahme_messen(up)
        except Exception:
            up.state = "open"
            db.commit()
            raise
        finally:
            _MESSPLAETZE.release()
    finally:
        with _MESSUNG_SPERRE:
            _IN_MESSUNG.discard(up.id)


@router.post("/uploads/{uploadId}/complete", status_code=202, response_model=schemas.ProcessingStatusOut)
def complete(uploadId: str, request: Request, wer=Depends(nutzer_oder_bot), db: Session = Depends(get_db)):
    user, bot = wer
    up, s = _load_upload(db, uploadId, user, bot)
    if bot is not None:
        from app import discord_bot

        user = discord_bot.sl_konto(db, s)  # Stand wie für die SL (Guthaben, Hinweise)
        if up.state == "open":
            discord_bot.abschluss_pruefen(db, s, up)
    if up.state == "completed":
        return processing_status(db, s, sprache(request), user)  # doppelt abgeschickt – harmlos
    if up.state == "measuring":
        with _MESSUNG_SPERRE:
            laeuft = up.id in _IN_MESSUNG
        if laeuft:
            return processing_status(db, s, sprache(request), user)
        up.state = "open"  # Messung durch einen Neustart abgebrochen: noch einmal
    if up.state != "open":
        raise errors.conflict("upload_closed")
    anzahl = sum(len(v) for v in _missing(up).values())
    if anzahl:
        raise errors.conflict("chunks_missing", count=anzahl)
    if bot is None:
        check_upload_consent(db, s)  # Widerruf während des Hochladens
    from app import abrechnung

    if abrechnung.an(db) or bot is not None:  # 0.4.85: Länge selbst messen; 0.4.156: Bot-Runden immer (B9)
        dauer = _messen(db, up)
        if dauer is _LAEUFT:  # zweiter Aufruf, während gemessen wird – harmlos, der erste schließt ab
            return processing_status(db, s, sprache(request), user)
        up.dauer_sekunden = dauer
        # Abgelehnt: Bot-Runde ohne Ton (die SL kann von Hand hochladen), sonst wieder „angelegt“
        zurueck = "no_recording" if s.bot_key_id else "created"
        if dauer is None:  # 0.4.99: nicht messbar (kein lesbares Audio, sehr viele Tonspuren) → nicht annehmen
            storage.delete_upload_files(up.id)
            up.state = "aborted"
            set_state(s, zurueck)
            db.commit()
            raise errors.conflict("recording_unreadable")
        laenge = up.dauer_sekunden
        grenze = abrechnung.MAX_AUFNAHME_SEKUNDEN
        if bot is not None:
            from app import discord_bot

            grenze = min(grenze, discord_bot.erlaubte_sekunden(s))
        if laenge > grenze:
            storage.delete_upload_files(up.id)
            up.state = "aborted"
            set_state(s, zurueck)
            db.commit()
            raise errors.conflict("recording_too_long", stunden=max(1, round(grenze / 3600)))
    up.state = "completed"
    up.completed_at = utcnow()
    if s.bot_key_id:
        from app import discord_bot

        discord_bot.gaeste_vergessen(db, s)  # B7: Discord-Prüfsummen der Gäste nur bis zum Upload
    create_transcribe_job(db, s, up)
    db.commit()
    return processing_status(db, s, sprache(request), user)
