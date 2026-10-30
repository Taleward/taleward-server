"""Spielende tragen bei (Schnittstelle 0.4.28): Vorschläge für die Bibel, „Merken“, Zähler am Charakter.

Grundsätze (Freigabe des Sicherheits-Chats vom 30.10.2026, Bedingungen 1–23):
- Nichts davon geht an ein Sprachmodell, ins Kapitel, in einen Umzug oder in Messwerte.
- Vorschläge: Spielende sehen nur die eigenen, keine Anzahl. Für die Urheberin sind „abgelehnt“ und „übernommen, aber für
  sie nicht sichtbar“ nicht unterscheidbar (S56: state closed). Erst die Entscheidung der SL macht einen Eintrag –
  angehängt wie bei Session-Vorschlägen, nie überschrieben; Geheimes landet nur in den SL-Notizen.
- Merkstellen: nur die Person selbst, auch nicht die SL, auch nicht als Anzahl. offsetMs und recapParagraph sind nur
  Zahlen; Abschrift, Audio und Belege bleiben, wo sie sind.
- Zähler: nur die Person selbst und die SL; für alle anderen fehlt das Feld.
"""
from __future__ import annotations

import json
import re
from datetime import datetime, timedelta, timezone

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import aliasse, errors, schemas
from app.db import utcnow
from app.models import Bookmark, Campaign, Entry, GameSession, Member, PlayerProposal, Recap, Upload

# ---------------------------------------------------------------- Grenzen
OFFEN_JE_MITGLIED = 20
OFFEN_JE_KAMPAGNE = 200
JE_STUNDE = 30
SL_TAGE = 30            # die SL sieht entschiedene so lange
AUFBEWAHRUNG_TAGE = 90  # entschiedene und zurückgezogene danach gelöscht
MERKEN_JE_RUNDE = 200
MERKEN_JE_MINUTE = 60
MERKEN_VORLAUF = timedelta(hours=12)
MERKEN_ZUKUNFT = timedelta(minutes=5)
SPANNEN_HOECHSTENS = 500
SPANNEN_TOLERANZ_S = 60
LIVE_STUNDEN = 16
ABSATZ_FENSTER_S = 600  # so weit darf ein Beleg vor der gemerkten Stelle liegen
MAX_TEXT = 20000

# Steuer- und Richtungszeichen wie bei Namen (aliasse), Zeilenumbrüche bleiben
_UNSICHTBAR_OHNE_ZEILE = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in (
    (0x00, 0x09), (0x0B, 0x1F), (0x7F, 0x9F), (0x61C, 0x61C), (0x200E, 0x200F), (0x202A, 0x202E),
    (0x2066, 0x2069))) + "]")


def text_bereinigen(text: str | None, laenge: int) -> str:
    """Mehrzeiliger Text: Steuer- und Richtungszeichen weg, höchstens zwei Zeilenumbrüche hintereinander."""
    t = (text or "").replace("\r\n", "\n").replace("\r", "\n")
    t = _UNSICHTBAR_OHNE_ZEILE.sub(" ", t)
    zeilen = [" ".join(z.split()) for z in t.split("\n")]
    t = re.sub(r"\n{3,}", "\n\n", "\n".join(zeilen)).strip()
    return t[:laenge].strip()


def _utc(dt: datetime) -> datetime:
    return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt.astimezone(timezone.utc)


def _anhaengen(alt: str | None, neu: str | None) -> str:
    return "\n\n".join(t for t in ((alt or "").strip(), (neu or "").strip()) if t)[:MAX_TEXT]


# ================================================================ Zähler
def arten(c: Campaign) -> list[dict]:
    try:
        liste = json.loads(c.counter_kinds or "[]")
    except ValueError:
        return []
    return [a for a in liste if isinstance(a, dict) and isinstance(a.get("id"), str)]


def arten_pruefen(roh: list[dict]) -> list[dict]:
    """Bezeichnungen und Hinweise bereinigen; leere Bezeichnung oder doppelte Kennung → 400."""
    aus, gesehen = [], set()
    for a in roh:
        label = aliasse.zeichen_bereinigen(a.get("label") or "")[:24].strip()
        hint = aliasse.zeichen_bereinigen(a.get("hint") or "")[:60].strip() or None
        if not label or a["id"] in gesehen:
            raise errors.bad_request("validation_error", "validation_error.counter_kinds")
        gesehen.add(a["id"])
        aus.append({"id": a["id"], "label": label, "hint": hint})
    return aus


def arten_setzen(db: Session, c: Campaign, neu: list[dict]) -> None:
    """Neue Liste speichern; Stände entfernter Arten verwerfen (dieselbe Transaktion)."""
    c.counter_kinds = json.dumps(neu, ensure_ascii=False) if neu else None
    bleibt = {a["id"] for a in neu}
    for m in db.scalars(select(Member).where(Member.campaign_id == c.id, Member.counters.is_not(None))):
        werte = [w for w in _staende_roh(m) if w["kindId"] in bleibt]
        m.counters = json.dumps(werte) if werte else None


def _staende_roh(m: Member) -> list[dict]:
    try:
        liste = json.loads(m.counters or "[]")
    except ValueError:
        return []
    return [w for w in liste if isinstance(w, dict) and isinstance(w.get("kindId"), str)]


def staende(m: Member, c: Campaign) -> list[schemas.CounterValue] | None:
    """Stände für die Ausgabe; None, wenn die Kampagne keine Zähler hat (dann fehlt das Feld)."""
    ids = [a["id"] for a in arten(c)]
    if not ids:
        return None
    nach = {w["kindId"]: w for w in _staende_roh(m)}
    return [schemas.CounterValue(kind_id=k, value=int(nach[k]["value"]), max=nach[k].get("max"))
            for k in ids if k in nach]


def staende_setzen(db: Session, c: Campaign, me: Member, werte: list[schemas.CounterValue]) -> list[schemas.CounterValue]:
    if me.role != "player" or not me.aktiv:
        raise errors.forbidden()
    ids = {a["id"] for a in arten(c)}
    if not ids:
        raise errors.ApiError(403, "counters_off")
    gesehen = set()
    for w in werte:
        if w.kind_id not in ids:
            raise errors.bad_request("counter_unknown")
        if w.kind_id in gesehen:
            raise errors.bad_request("validation_error", "validation_error.counter_kinds")
        gesehen.add(w.kind_id)
    me.counters = json.dumps([{"kindId": w.kind_id, "value": w.value, "max": w.max} for w in werte]) if werte else None
    return staende(me, c) or []


# ================================================================ Vorschläge von Spielenden
def urheberin_sicht(db: Session, p: PlayerProposal) -> tuple[str, str | None]:
    """Zustand und Eintrag, wie die Urheberin sie sehen darf (S56)."""
    if p.state in ("open", "withdrawn"):
        return p.state, None
    if p.state == "accepted" and p.text_sichtbar and p.result_entry_id:
        e = db.get(Entry, p.result_entry_id)
        if (e is not None and e.campaign_id == p.campaign_id and e.visibility == "public"
                and p.author_member_id not in e.hidden_member_ids):
            return "accepted", e.id
    return "closed", None


def vorschlag_out(db: Session, p: PlayerProposal, fuer_sl: bool) -> schemas.PlayerProposalOut:
    if fuer_sl:
        zustand, ergebnis = p.state, p.result_entry_id
    else:
        zustand, ergebnis = urheberin_sicht(db, p)
    return schemas.PlayerProposalOut(
        id=p.id, campaign_id=p.campaign_id, author_member_id=p.author_member_id or "", entry_type=p.entry_type,
        title=p.title, detail=p.detail or "", target_entry_id=p.target_entry_id, state=zustand, created_at=p.created_at,
        decided_at=p.decided_at, result_entry_id=ergebnis)


def offene_anzahl(db: Session, campaign_id: str) -> int:
    return db.scalar(select(func.count()).select_from(PlayerProposal).where(
        PlayerProposal.campaign_id == campaign_id, PlayerProposal.state == "open")) or 0


def liste(db: Session, c: Campaign, me: Member, nur_offen: bool) -> list[schemas.PlayerProposalOut]:
    q = select(PlayerProposal).where(PlayerProposal.campaign_id == c.id)
    if me.role == "gm":
        grenze = utcnow() - timedelta(days=SL_TAGE)
        q = q.where((PlayerProposal.state == "open") if nur_offen else
                    ((PlayerProposal.state == "open") | (PlayerProposal.decided_at >= grenze)))
    else:
        if not c.player_proposals:
            return []
        q = q.where(PlayerProposal.author_member_id == me.id)
        if nur_offen:
            q = q.where(PlayerProposal.state == "open")
    zeilen = db.scalars(q.order_by(PlayerProposal.created_at.desc(), PlayerProposal.id))
    return [vorschlag_out(db, p, me.role == "gm") for p in zeilen]


def anlegen(db: Session, c: Campaign, me: Member, body: schemas.PlayerProposalIn) -> PlayerProposal:
    from app.access import player_can_see_entry

    if me.role != "player":
        raise errors.forbidden()
    if not c.player_proposals:
        raise errors.ApiError(403, "player_proposals_off")
    ziel = None
    if body.target_entry_id is not None:
        # derselbe Weg wie GET /entries/{id}: unbekannt und verborgen → gleiche 404
        e = db.get(Entry, body.target_entry_id)
        if e is None or e.campaign_id != c.id or not player_can_see_entry(e, me):
            raise errors.not_found("entry")
        ziel = e.id
    titel = aliasse.zeichen_bereinigen(body.title)[:120].strip()
    if not titel:
        raise errors.bad_request("validation_error", "validation_error.title")
    jetzt = utcnow()
    if (db.scalar(select(func.count()).select_from(PlayerProposal).where(
            PlayerProposal.author_member_id == me.id, PlayerProposal.created_at >= jetzt - timedelta(hours=1)))
            or 0) >= JE_STUNDE:
        raise errors.ApiError(429, "too_many_requests")
    eigene = db.scalar(select(func.count()).select_from(PlayerProposal).where(
        PlayerProposal.author_member_id == me.id, PlayerProposal.state == "open")) or 0
    if eigene >= OFFEN_JE_MITGLIED or offene_anzahl(db, c.id) >= OFFEN_JE_KAMPAGNE:
        raise errors.conflict("player_proposal_limit")
    p = PlayerProposal(campaign_id=c.id, author_member_id=me.id, entry_type=body.entry_type, title=titel,
                       detail=text_bereinigen(body.detail, 2000), target_entry_id=ziel, state="open", created_at=jetzt)
    db.add(p)
    db.flush()
    return p


def laden(db: Session, proposal_id: str, user) -> tuple[PlayerProposal, Member]:
    """Vorschlag für die SL der Kampagne oder die Urheberin; alle anderen 404."""
    from app.access import membership

    p = db.get(PlayerProposal, proposal_id)
    m = membership(db, p.campaign_id, user) if p is not None else None
    if p is None or m is None or (m.role != "gm" and m.id != p.author_member_id):
        raise errors.not_found("player_proposal")
    return p, m


def zurueckziehen(db: Session, p: PlayerProposal, me: Member) -> None:
    if me.id != p.author_member_id:
        raise errors.not_found("player_proposal")
    if p.state != "open":
        raise errors.conflict("player_proposal_decided")
    p.state, p.decided_at = "withdrawn", utcnow()


def entscheiden(db: Session, p: PlayerProposal, me: Member, body: schemas.PlayerProposalDecision) -> None:
    if me.role != "gm":
        raise errors.not_found("player_proposal")
    if p.state != "open":
        raise errors.conflict("player_proposal_decided")
    if body.decision == "reject":
        p.state, p.decided_at = "rejected", utcnow()
        return
    e, sichtbar = _uebernehmen(db, p, body)
    p.state, p.decided_at, p.result_entry_id, p.text_sichtbar = "accepted", utcnow(), e.id, sichtbar


def _halter(db: Session, campaign_id: str, holder_id: str | None) -> str | None:
    """Halter nur aktive Mitglieder mit Rolle player (wie 0.4.139), sonst 400."""
    if holder_id is None:
        return None
    m = db.get(Member, holder_id)
    if m is None or m.campaign_id != campaign_id or not m.aktiv or m.role != "player":
        raise errors.bad_request("holder_unknown")
    return m.id


def _uebernehmen(db: Session, p: PlayerProposal, body: schemas.PlayerProposalDecision) -> tuple[Entry, bool]:
    """Eintrag anlegen oder ergänzen. Rückgabe: Eintrag und ob der Text im öffentlichen Teil steht."""
    from app.services import apply_entry_input

    f = body.model_fields_set
    typ = body.entry_type or p.entry_type
    titel = p.title
    if body.title is not None:
        titel = aliasse.zeichen_bereinigen(body.title)[:120].strip()
        if not titel:
            raise errors.bad_request("validation_error", "validation_error.title")
    text = text_bereinigen(body.detail, 2000) if body.detail is not None else (p.detail or "")
    geheim = text_bereinigen(body.gm_notes, MAX_TEXT) if body.gm_notes else ""
    sicht = body.visibility or "public"
    verborgen = sorted(set(body.hidden_from_member_ids or [])) if sicht == "public" else []
    if verborgen:
        gueltig = set(db.scalars(select(Member.id).where(Member.campaign_id == p.campaign_id,
                                                         Member.id.in_(verborgen))))
        if gueltig != set(verborgen):
            raise errors.bad_request("hidden_member_unknown")
    halter = _halter(db, p.campaign_id, body.holder_member_id) if typ == "item" else None
    ziel_id = body.merge_into_entry_id if "merge_into_entry_id" in f else p.target_entry_id
    e = db.get(Entry, ziel_id) if ziel_id else None
    if e is not None and (e.campaign_id != p.campaign_id or e.type == "pc"):
        e = None
    if e is None and ziel_id and "merge_into_entry_id" in f:
        raise errors.bad_request("merge_target_unknown")
    if e is not None:  # Ergänzen: anhängen, nie überschreiben
        weniger_sichtbar = e.visibility == "public" and (
            sicht != "public" or bool(set(verborgen) - e.hidden_member_ids))
        if weniger_sichtbar:
            # Geheimes zu einem sichtbareren Eintrag: nur in die SL-Notizen, ohne neues Änderungsdatum
            notiz = _anhaengen(_anhaengen(e.gm_notes, f"{titel}: {text}".strip(": ")), geheim)
            vorher = e.updated_at
            apply_entry_input(db, e, schemas.EntryInput(gm_notes=notiz))
            e.updated_at = vorher
            return e, False
        daten = dict(summary=_anhaengen(e.summary, text), gm_notes=_anhaengen(e.gm_notes, geheim))
        if sicht == "public" and verborgen and e.visibility == "public":
            daten["hidden_from_member_ids"] = sorted(e.hidden_member_ids | set(verborgen))
        if halter and (e.type or typ) == "item":
            daten["holder_member_id"] = halter
        apply_entry_input(db, e, schemas.EntryInput(**daten))
        return e, bool(text.strip())
    e = Entry(campaign_id=p.campaign_id, type="", name="", summary="", visibility="gm_only")
    db.add(e)
    db.flush()
    daten = dict(type=typ, name=titel, summary=text, gm_notes=geheim, visibility=sicht,
                 hidden_from_member_ids=verborgen)
    if halter:
        daten["holder_member_id"] = halter
    apply_entry_input(db, e, schemas.EntryInput(**daten))
    return e, True


# ================================================================ Merken
def _anwesend(s: GameSession, me: Member) -> bool:
    return any(a.member_id == me.id for a in s.attendees)


def runde_laden(db: Session, session_id: str, user, schreiben: bool) -> tuple[GameSession, Member]:
    """Lesen: jede Runde, die die Person sieht (vor dem Veröffentlichen nur Anwesende). Schreiben: nur Anwesende, nur
    bis zur Veröffentlichung (danach 409). Alles andere 404 – die Runde selbst bleibt für Spielende verborgen."""
    from app.access import membership, player_can_see_session

    s = db.get(GameSession, session_id)
    me = membership(db, s.campaign_id, user) if s is not None else None
    if s is None or me is None:
        raise errors.not_found("session")
    sieht = me.role == "gm" or player_can_see_session(s) or _anwesend(s, me)
    if not sieht:
        raise errors.not_found("session")
    if schreiben:
        if s.state == "published":
            raise errors.conflict("session_published", "session_published.bookmark")
        if not _anwesend(s, me):
            raise errors.not_found("session")
    return s, me


def merken(db: Session, s: GameSession, me: Member, body: schemas.BookmarkIn) -> Bookmark:
    from app.begrenzung import ZAEHLER

    jetzt = utcnow()
    at = _utc(body.at) if body.at is not None else jetzt
    if at < _utc(s.played_at) - MERKEN_VORLAUF or at > jetzt + MERKEN_ZUKUNFT:
        raise errors.bad_request("bookmark_time_invalid")
    if not ZAEHLER.versuch(f"merken:{me.id}", MERKEN_JE_MINUTE, 60):
        raise errors.ApiError(429, "too_many_requests")
    anzahl = db.scalar(select(func.count()).select_from(Bookmark).where(
        Bookmark.session_id == s.id, Bookmark.member_id == me.id)) or 0
    if anzahl >= MERKEN_JE_RUNDE:
        raise errors.conflict("bookmark_limit")
    b = Bookmark(session_id=s.id, member_id=me.id, at=at, note=text_bereinigen(body.note, 280) or None, created_at=jetzt)
    db.add(b)
    db.flush()
    return b


def merkstellen(db: Session, s: GameSession, me: Member) -> list[schemas.BookmarkOut]:
    zeilen = list(db.scalars(select(Bookmark).where(Bookmark.session_id == s.id, Bookmark.member_id == me.id)
                             .order_by(Bookmark.at, Bookmark.id)))
    if not zeilen:
        return []
    achse = zeitachse(db, s)
    absaetze = _absatz_belege(db, s)
    aus = []
    for b in zeilen:
        versatz = versatz_ms(achse, b.at) if achse else None
        absatz = absatz_finden(absaetze, versatz / 1000) if versatz is not None and absaetze else None
        aus.append(schemas.BookmarkOut(id=b.id, session_id=b.session_id, at=b.at, note=b.note, offset_ms=versatz,
                                       recap_paragraph=absatz, created_at=b.created_at))
    return aus


def merkstelle_loeschen(db: Session, bookmark_id: str, user) -> None:
    from app.access import membership

    b = db.get(Bookmark, bookmark_id)
    m = db.get(Member, b.member_id) if b is not None else None
    eigen = m is not None and membership(db, m.campaign_id, user) is not None and m.user_id == user.id
    if not eigen:
        raise errors.not_found("bookmark")
    db.delete(b)


# ---------------------------------------------------------------- Zeit in der Aufnahme
def spannen_pruefen(roh, played_at: datetime) -> str | None:
    """Spannen beim Start des Hochladens: aufsteigend, ohne Überlappung, jede > 0, ab playedAt − 12 h, nicht nach dem
    Hochladen. Passt etwas nicht: None (still verworfen – nie ein Fehler). Die Summe prüft zeitachse() gegen die Länge."""
    if not isinstance(roh, list) or not roh or len(roh) > SPANNEN_HOECHSTENS:
        return None
    jetzt = utcnow()
    frueheste = _utc(played_at) - MERKEN_VORLAUF
    aus, vorher = [], None
    for sp in roh:
        if not isinstance(sp, dict):
            return None
        try:
            a = _utc(datetime.fromisoformat(str(sp.get("startedAt")).replace("Z", "+00:00")))
            b = _utc(datetime.fromisoformat(str(sp.get("endedAt")).replace("Z", "+00:00")))
        except (TypeError, ValueError):
            return None
        if b <= a or a < frueheste or b > jetzt + MERKEN_ZUKUNFT or (vorher is not None and a < vorher):
            return None
        aus.append([a.timestamp(), b.timestamp()])
        vorher = b
    return json.dumps(aus)


def zeitachse(db: Session, s: GameSession) -> list[tuple[float, float]] | None:
    """Zeiten (Sekunden seit 1970), in denen aufgenommen wurde. Ohne gültige Spannen: playedAt ohne Pausen. None,
    solange keine Aufnahme da bzw. ihre Länge unbekannt ist."""
    up = db.scalar(select(Upload).where(Upload.session_id == s.id, Upload.state != "aborted")
                   .order_by(Upload.created_at.desc()).limit(1))
    if up is None:
        return None
    laenge = s.duration_seconds if s.duration_seconds is not None else up.dauer_sekunden
    if up.spans and up.source == "table":
        try:
            spannen = [(float(a), float(b)) for a, b in json.loads(up.spans)]
        except (TypeError, ValueError):
            spannen = []
        ende = _utc(up.completed_at).timestamp() + MERKEN_ZUKUNFT.total_seconds() if up.completed_at else None
        summe = sum(b - a for a, b in spannen)
        if (spannen and (ende is None or spannen[-1][1] <= ende)
                and (laenge is None or abs(summe - laenge) <= SPANNEN_TOLERANZ_S)):
            return spannen
    if laenge is None:
        return None
    beginn = _utc(s.played_at).timestamp()
    return [(beginn, beginn + float(laenge))]


def versatz_ms(achse: list[tuple[float, float]], at: datetime) -> int | None:
    """Zeit in der Aufnahme. In einer Pause: das Ende der Spanne davor. Vor dem Beginn oder nach dem Ende: None."""
    t = _utc(at).timestamp()
    if t < achse[0][0] or t > achse[-1][1]:
        return None
    bisher = 0.0
    for a, b in achse:
        if t < a:
            break
        if t <= b:
            return int(round((bisher + t - a) * 1000))
        bisher += b - a
    return int(round(bisher * 1000))


def _absatz_belege(db: Session, s: GameSession) -> list[tuple[int, float]] | None:
    """(Absatz, Beleg-Zeitpunkt) aus dem Prüfteil des veröffentlichten Kapitels; nur solange er zum Text passt."""
    if s.state != "published":
        return None
    r = db.get(Recap, s.id)
    if r is None:
        return None
    from app import pruefteil

    d = pruefteil.lesen(r.review)
    if d.get("state") != "done" or d.get("stale"):
        return None
    aus = []
    for p in d.get("paragraphs") or []:
        for b in p.get("evidence") or []:
            if isinstance(b, dict) and isinstance(b.get("start"), (int, float)) and isinstance(p.get("index"), int):
                aus.append((p["index"], float(b["start"])))
    return aus or None


def absatz_finden(belege: list[tuple[int, float]], sekunde: float) -> int | None:
    """Absatz mit dem spätesten Beleg bis kurz nach der gemerkten Stelle (höchstens ABSATZ_FENSTER_S davor)."""
    passend = [(start, absatz) for absatz, start in belege if sekunde - ABSATZ_FENSTER_S <= start <= sekunde + 30]
    return max(passend)[1] if passend else None


# ================================================================ Laufende Runde, Austritt, Aufräumen
def live_session_id(db: Session, c: Campaign, me: Member) -> str | None:
    grenze = utcnow() - timedelta(hours=LIVE_STUNDEN)
    s = db.scalar(select(GameSession).where(GameSession.campaign_id == c.id,
                                            GameSession.state.in_(("created", "uploading")),
                                            GameSession.played_at >= grenze)
                  .order_by(GameSession.played_at.desc()).limit(1))
    if s is None:
        return None
    if me.role == "gm" or _anwesend(s, me):
        return s.id
    return None


def mitglied_weg(db: Session, member_id: str, zaehler: bool = True) -> None:
    """Austritt, Konto löschen, Platz freigeben: offene Vorschläge und Merkstellen der Person löschen (entschiedene
    Vorschläge bleiben stehen). zaehler: Stände verwerfen – beim Freigeben eines Platzes bleiben sie an der Figur."""
    db.execute(delete(PlayerProposal).where(PlayerProposal.author_member_id == member_id,
                                            PlayerProposal.state == "open"))
    db.execute(delete(Bookmark).where(Bookmark.member_id == member_id))
    if zaehler:
        m = db.get(Member, member_id)
        if m is not None:
            m.counters = None


def aufraeumen(db: Session) -> int:
    grenze = utcnow() - timedelta(days=AUFBEWAHRUNG_TAGE)
    r = db.execute(delete(PlayerProposal).where(PlayerProposal.state != "open", PlayerProposal.decided_at < grenze))
    return r.rowcount or 0


def export(db: Session, member_ids: list[str]) -> dict:
    """Datenauskunft: eigene Vorschläge (Zustand wie die Person ihn sieht) und eigene Merkstellen."""
    if not member_ids:
        return {"playerProposals": [], "bookmarks": []}

    def z(dt):
        return _utc(dt).isoformat().replace("+00:00", "Z") if dt else None

    vorschlaege = []
    for p in db.scalars(select(PlayerProposal).where(PlayerProposal.author_member_id.in_(member_ids))
                        .order_by(PlayerProposal.created_at)):
        zustand, ergebnis = urheberin_sicht(db, p)
        vorschlaege.append({"campaignId": p.campaign_id, "id": p.id, "entryType": p.entry_type, "title": p.title,
                            "detail": p.detail, "state": zustand, "resultEntryId": ergebnis,
                            "createdAt": z(p.created_at), "decidedAt": z(p.decided_at)})
    merk = [{"sessionId": b.session_id, "id": b.id, "at": z(b.at), "note": b.note, "createdAt": z(b.created_at)}
            for b in db.scalars(select(Bookmark).where(Bookmark.member_id.in_(member_ids)).order_by(Bookmark.at))]
    return {"playerProposals": vorschlaege, "bookmarks": merk}
