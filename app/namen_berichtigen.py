"""Namen einmal berichtigen (Schnittstelle 0.4.31, S62): eine Schreibweise kampagnenweit ersetzen.

Verbindlich (Vorgabe `claude/app-vorgabe-0.4.31.md`, Bedingungen 1–14 des Sicherheits-Chats):
- Nur SL der Kampagne (sonst 404). Kein Sprachmodell. Wörtliche Suche (escapte Muster), ganze Wörter, Groß-/Klein-
  schreibung wie eingegeben; beide Seiten bereinigt, 3–64 Zeichen, from ≠ to.
- Geändert werden nur: Kapitel (Titel, Text, offene Fäden), Bibel (Name, „Auch genannt“, Beschreibung, SL-Notiz – über
  denselben Weg wie PATCH /entries, Sichtbarkeit bleibt), offene Vorschläge aus Runden (Titel, Text) und Abschriften
  (reine Wortersetzung, Zeitmarken bleiben). Charaktere, Kommentare und Vorschläge von Spielenden werden nur gezählt bzw.
  als Ort genannt, nie geändert und nie im Wortlaut gezeigt; Kommentare nur, soweit die SL sie sieht. Merkstellen,
  Zähler, Teilen-Schlüssel, Stimmen, Unterlagen, Mails und Protokolle werden nie durchsucht.
- Vorschau-Fassung = Prüfsumme je Treffer-Ort; hat sich ein Ort geändert, passiert nichts (409 preview_stale).
- Rückgängig 24 Stunden: alte Texte nur in `name_fix_undo` (nie im Umzug, nie in einer Antwort), danach löscht die
  Wartung sie; gelöschte Runden, Einträge oder Kampagnen nehmen sie mit (Fremdschlüssel).
- Verhörer-Regel (`rule`): nur als Vorschlag in „Namen prüfen“ (`unsicher.begriffe`), nie still ersetzt; höchstens
  MAX_REGELN je Kampagne. Geht im Umzug mit (ohne Person, ohne Protokoll).
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable

from sqlalchemy import exists, select
from sqlalchemy.orm import Session

from app import aliasse, errors
from app.db import utcnow
from app.models import (Campaign, Comment, Entry, GameSession, Member, NameFix, NameFixUndo, PlayerProposal, Proposal,
                        Recap, TranscriptSegment)

MIN_ZEICHEN, MAX_ZEICHEN = 3, 64
MAX_STELLEN = 2000
MAX_REGELN = 200
RUECKGAENGIG_STUNDEN = 24
VORSCHAU_JE_STUNDE = 60  # je Konto
BERICHTIGEN_JE_STUNDE = 20  # je Kampagne
AUSZUG_ZEICHEN = 200

ORT_FOLGE = ("chapter_title", "chapter", "entry_name", "entry_alias", "entry_summary", "entry_gm_notes",
             "session_proposal", "transcript")
_FELDNAME = {"entry_name": ("Name", "name"), "entry_alias": ("Auch genannt", "also known as"),
             "entry_summary": ("Beschreibung", "description"), "entry_gm_notes": ("SL-Notiz", "GM note")}


# ---------------------------------------------------------------- Eingabe und Suche
def eingabe(von: str, nach: str) -> tuple[str, str]:
    """Beide Seiten bereinigen und prüfen (400 invalid_name_fix)."""
    roh = (von or "") + (nach or "")
    if "\n" in roh or "\r" in roh:
        raise errors.bad_request("invalid_name_fix")
    a, b = aliasse.zeichen_bereinigen(von), aliasse.zeichen_bereinigen(nach)
    if not (MIN_ZEICHEN <= len(a) <= MAX_ZEICHEN and MIN_ZEICHEN <= len(b) <= MAX_ZEICHEN) or a == b:
        raise errors.bad_request("invalid_name_fix")
    return a, b


def muster(wort: str) -> re.Pattern:
    """Ganzes Wort, wörtlich (keine regulären Ausdrücke aus der Eingabe), Groß-/Kleinschreibung wie eingegeben.
    Endungen wie „Orasilas’“ stehen nach einem Nicht-Wortzeichen und passen deshalb mit."""
    return re.compile(rf"(?<!\w){re.escape(wort)}(?!\w)")


def _zaehlen(m: re.Pattern, werte: dict[str, str]) -> int:
    return sum(len(m.findall(t or "")) for t in werte.values())


def _auszuege(m: re.Pattern, werte: dict[str, str], anzahl: int = 3) -> list[str]:
    aus: list[str] = []
    for text in werte.values():
        for treffer in m.finditer(text or ""):
            if len(aus) >= anzahl:
                return aus
            a, b = treffer.span()
            links_ = max(text.rfind(z, 0, a) for z in (".", "!", "?", "\n")) + 1
            rechts_ = [i for i in (text.find(z, b) for z in (".", "!", "?", "\n")) if i >= 0]
            ende = min(rechts_) + 1 if rechts_ else len(text)
            platz = AUSZUG_ZEICHEN - (b - a) - 4
            links_ = max(links_, a - platz // 2)
            ende = min(ende, b + max(0, platz - (a - links_)))
            satz = text[links_:a] + "[[" + text[a:b] + "]]" + text[b:ende]
            aus.append(" ".join(satz.split())[:AUSZUG_ZEICHEN])
    return aus


def _pruefsumme(werte: dict[str, str]) -> str:
    return hashlib.sha256(json.dumps(sorted(werte.items()), ensure_ascii=False).encode()).hexdigest()


# ---------------------------------------------------------------- Orte
@dataclass
class Ort:
    place: str
    oid: str
    label: tuple[str, str]
    werte: dict[str, str]
    lesen: Callable[[list[str]], dict[str, str]]  # aktuelle Werte zu den Schlüsseln
    setzen: Callable[[dict[str, str]], None]
    read_by_players: bool = False
    session_id: str | None = None
    session_number: int | None = None
    entry_id: str | None = None
    proposal_id: str | None = None
    rang: tuple = field(default_factory=tuple)

    @property
    def hit_id(self) -> str:
        return hashlib.sha1(f"{self.place}:{self.oid}".encode()).hexdigest()[:16]


def _kapitel_orte(db: Session, c: Campaign, m: re.Pattern) -> list[Ort]:
    aus = []
    for s, r in db.execute(select(GameSession, Recap).join(Recap, Recap.session_id == GameSession.id)
                           .where(GameSession.campaign_id == c.id)):
        def lesen_titel(_k, r=r):
            return {"title": r.title or ""}

        def setzen_titel(w, r=r):
            r.title = (w.get("title", r.title) or "")[:300]
            r.edited_at = utcnow()

        def lesen_text(_k, r=r):
            faeden = json.loads(r.open_threads or "[]")
            return {"text": r.text or "", **{f"faden{i}": f for i, f in enumerate(faeden)}}

        def setzen_text(w, r=r):
            if "text" in w:
                r.text = w["text"]
            faeden = json.loads(r.open_threads or "[]")
            for i in range(len(faeden)):
                faeden[i] = w.get(f"faden{i}", faeden[i])
            r.open_threads = json.dumps(faeden, ensure_ascii=False)
            r.edited_at = utcnow()

        titel = (r.title or "").strip()
        label = (f"Kapitel {s.number}" + (f" · {titel}" if titel else ""),
                 f"Chapter {s.number}" + (f" · {titel}" if titel else ""))
        gemeinsam = dict(read_by_players=s.state == "published", session_id=s.id, session_number=s.number)
        for place, lesen, setzen in (("chapter_title", lesen_titel, setzen_titel), ("chapter", lesen_text, setzen_text)):
            werte = lesen([])
            if _zaehlen(m, werte):
                aus.append(Ort(place, s.id, label, werte, lesen, setzen, rang=(0, s.number, place), **gemeinsam))
    return aus


def _eintrag_orte(db: Session, c: Campaign, m: re.Pattern) -> list[Ort]:
    from app import schemas
    from app.services import apply_entry_input

    aus = []
    for e in db.scalars(select(Entry).where(Entry.campaign_id == c.id)):
        def lesen_alias(_k, e=e):
            return {f"a{i}": a for i, a in enumerate(e.aliases)}

        def setzen_alias(w, e=e):
            liste = [w.get(f"a{i}", a) for i, a in enumerate(e.aliases)]
            apply_entry_input(db, e, schemas.EntryInput(aliases=liste))

        oeffentlich = e.visibility == "public"
        for place, feld in (("entry_name", "name"), ("entry_alias", None), ("entry_summary", "summary"),
                            ("entry_gm_notes", "gm_notes")):
            if feld:
                lesen = (lambda _k, e=e, f=feld: {f: getattr(e, f) or ""})
                setzen = (lambda w, e=e, f=feld: apply_entry_input(db, e, schemas.EntryInput(**{f: w[f]}))
                          if f in w else None)
            else:
                lesen, setzen = lesen_alias, setzen_alias
            werte = lesen([])
            if not _zaehlen(m, werte):
                continue
            de, en = _FELDNAME[place]
            aus.append(Ort(place, e.id, (f"Bibel: {e.name} ({de})", f"Codex: {e.name} ({en})"), werte, lesen, setzen,
                           read_by_players=oeffentlich and place != "entry_gm_notes", entry_id=e.id,
                           rang=(1, e.name.casefold(), ORT_FOLGE.index(place))))
    return aus


def _vorschlag_orte(db: Session, c: Campaign, m: re.Pattern) -> list[Ort]:
    aus = []
    for p, s in db.execute(select(Proposal, GameSession).join(GameSession, GameSession.id == Proposal.session_id)
                           .where(Proposal.campaign_id == c.id, Proposal.decision == "open",
                                  Proposal.source.is_(None), Proposal.submitted_by_member_id.is_(None))):
        def lesen(_k, p=p):
            return {"title": p.title or "", "detail": p.detail or ""}

        def setzen(w, p=p):
            p.title = w.get("title", p.title)[:300]
            p.detail = w.get("detail", p.detail)

        werte = lesen([])
        if _zaehlen(m, werte):
            aus.append(Ort("session_proposal", p.id, (f"Offener Vorschlag aus Runde {s.number}: {p.title}",
                                                      f"Open suggestion from session {s.number}: {p.title}"),
                           werte, lesen, setzen, session_id=s.id, session_number=s.number, proposal_id=p.id,
                           rang=(2, s.number, p.position, p.id)))
    return aus


def _abschrift_orte(db: Session, c: Campaign, wort: str, m: re.Pattern) -> list[Ort]:
    aus = []
    nach_runde: dict[str, dict[str, str]] = {}
    for seg in db.scalars(select(TranscriptSegment).join(GameSession, GameSession.id == TranscriptSegment.session_id)
                          .where(GameSession.campaign_id == c.id, TranscriptSegment.text.contains(wort, autoescape=True))
                          .order_by(TranscriptSegment.start, TranscriptSegment.id)):
        if m.search(seg.text or ""):
            nach_runde.setdefault(seg.session_id, {})[seg.id] = seg.text
    for sid, werte in nach_runde.items():
        s = db.get(GameSession, sid)

        def lesen(keys, sid=sid):
            if not keys:
                return {}
            return {x.id: x.text for x in db.scalars(select(TranscriptSegment).where(
                TranscriptSegment.session_id == sid, TranscriptSegment.id.in_(keys)))}

        def setzen(w, sid=sid):
            for x in db.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == sid,
                                                                TranscriptSegment.id.in_(list(w)))):
                x.text = w[x.id]

        aus.append(Ort("transcript", sid, (f"Abschrift Runde {s.number}", f"Transcript, session {s.number}"), werte,
                       lesen, setzen, session_id=sid, session_number=s.number, rang=(3, s.number)))
    return aus


def _orte(db: Session, c: Campaign, wort: str) -> list[Ort]:
    m = muster(wort)
    orte = (_kapitel_orte(db, c, m) + _eintrag_orte(db, c, m) + _vorschlag_orte(db, c, m)
            + _abschrift_orte(db, c, wort, m))
    return sorted(orte, key=lambda o: o.rang)


# ---------------------------------------------------------------- Vorschau
def _geheim(db: Session, c: Campaign, nach: str) -> bool:
    """„Richtig“ ist Name oder „Auch genannt“ eines Eintrags, den nicht alle Spielenden sehen – und keines, den alle sehen."""
    ziel = nach.casefold()
    geheim = sichtbar = False
    for e in db.scalars(select(Entry).where(Entry.campaign_id == c.id)):
        if ziel not in {e.name.casefold(), *(a.casefold() for a in e.aliases)}:
            continue
        if e.visibility == "public" and not e.hidden_member_ids and not e.origin_analysis_id:
            sichtbar = True
        else:
            geheim = True
    return geheim and not sichtbar


def _unberuehrt(db: Session, c: Campaign, sl: Member, m: re.Pattern, wort: str) -> dict:
    from app.routers.miteinander import sichtbar_fuer

    figuren = []
    for x in db.scalars(select(Member).where(Member.campaign_id == c.id, Member.left_at.is_(None))
                        .order_by(Member.joined_at, Member.id)):
        for feld, wert in (("name", x.character_name), ("summary", x.character_summary),
                           ("background", x.character_backstory)):
            if wert and m.search(wert):
                figuren.append({"memberId": x.id, "field": feld})
    kommentare = sum(1 for k in db.scalars(
        select(Comment).join(GameSession, GameSession.id == Comment.session_id)
        .where(GameSession.campaign_id == c.id, Comment.text.contains(wort, autoescape=True), sichtbar_fuer(sl)))
        if m.search(k.text or ""))
    spieler = sum(1 for p in db.scalars(select(PlayerProposal).where(PlayerProposal.campaign_id == c.id,
                                                                     PlayerProposal.state == "open"))
                  if m.search(p.title or "") or m.search(p.detail or ""))
    spieler += sum(1 for p in db.scalars(select(Proposal).where(
        Proposal.campaign_id == c.id, Proposal.decision == "open",
        (Proposal.source.is_not(None)) | (Proposal.submitted_by_member_id.is_not(None))))
        if m.search(p.title or "") or m.search(p.detail or ""))
    return {"characters": figuren, "comments": kommentare, "playerProposals": spieler}


def regel_fuer(db: Session, c: Campaign, von: str) -> NameFix | None:
    return db.scalar(select(NameFix).where(NameFix.campaign_id == c.id, NameFix.von == von, NameFix.rule.is_(True))
                     .order_by(NameFix.created_at.desc()).limit(1))


def _version(von: str, nach: str, orte: list[Ort]) -> str:
    teile = [von, nach] + sorted(f"{o.hit_id}:{_pruefsumme(o.werte)}" for o in orte)
    return hashlib.sha256("\n".join(teile).encode()).hexdigest()[:32]


def vorschau(db: Session, c: Campaign, sl: Member, von: str, nach: str, lang: str = "de") -> tuple[dict, list[Ort]]:
    von, nach = eingabe(von, nach)
    m = muster(von)
    orte = _orte(db, c, von)
    gesamt = sum(_zaehlen(m, o.werte) for o in orte)
    if gesamt > MAX_STELLEN:
        raise errors.conflict("too_many_hits", n=MAX_STELLEN)
    warnung = any(o.read_by_players for o in orte) and _geheim(db, c, nach)
    en = lang == "en"
    treffer = []
    for o in orte:
        treffer.append({
            "id": o.hit_id, "place": o.place, "label": o.label[1 if en else 0], "sessionId": o.session_id,
            "sessionNumber": o.session_number, "entryId": o.entry_id, "proposalId": o.proposal_id,
            "count": _zaehlen(m, o.werte), "readByPlayers": o.read_by_players,
            "preselected": not (warnung and o.read_by_players), "excerpts": _auszuege(m, o.werte),
        })
    regel = regel_fuer(db, c, von)
    return {"from": von, "to": nach, "previewVersion": _version(von, nach, orte), "total": gesamt,
            "secretWarning": warnung, "hits": treffer, "untouched": _unberuehrt(db, c, sl, m, von),
            "existingRule": regel.id if regel else None}, orte


# ---------------------------------------------------------------- Berichtigen
def berichtigen(db: Session, c: Campaign, sl: Member, von: str, nach: str, version: str, auslassen: list[str],
                regel: bool) -> NameFix:
    von, nach = eingabe(von, nach)
    _ansicht, orte = vorschau(db, c, sl, von, nach)
    if _ansicht["previewVersion"] != version:
        raise errors.conflict("preview_stale")
    bekannt = {o.hit_id for o in orte}
    weg = set(auslassen or [])
    if weg - bekannt:
        raise errors.bad_request("invalid_name_fix", "invalid_name_fix.skip")
    alt = regel_fuer(db, c, von)
    if regel and alt is None:
        anzahl = len(list(db.scalars(select(NameFix.id).where(NameFix.campaign_id == c.id, NameFix.rule.is_(True)))))
        if anzahl >= MAX_REGELN:
            raise errors.conflict("rule_limit", n=MAX_REGELN)
    m = muster(von)
    jetzt = utcnow()
    fix = NameFix(campaign_id=c.id, von=von, nach=nach, rule=bool(regel), created_at=jetzt,
                  created_by_member_id=sl.id, undo_until=jetzt + timedelta(hours=RUECKGAENGIG_STUNDEN))
    db.add(fix)
    db.flush()
    geaendert = 0
    orte_ids = []
    for o in orte:
        if o.hit_id in weg:
            continue
        neu = {k: m.sub(nach, v) for k, v in o.werte.items()}
        neu = {k: v for k, v in neu.items() if v != o.werte[k]}
        if not neu:
            continue
        geaendert += sum(len(m.findall(o.werte[k])) for k in neu)
        o.setzen(neu)
        db.flush()
        nachher = o.lesen(list(neu))
        db.add(NameFixUndo(fix_id=fix.id, place=o.place, oid=o.oid, label_de=o.label[0][:300], label_en=o.label[1][:300],
                           alt=json.dumps({k: o.werte[k] for k in neu}, ensure_ascii=False),
                           pruefsumme=_pruefsumme(nachher), session_id=o.session_id, entry_id=o.entry_id,
                           proposal_id=o.proposal_id))
        orte_ids.append({"place": o.place, "id": o.oid})
    fix.changed = geaendert
    fix.orte = json.dumps(orte_ids)
    if regel:
        if alt is not None:  # Berichtigen ersetzt das Ziel einer vorhandenen Regel
            alt.rule = False
        from app import namenshilfe

        namenshilfe.hinzufuegen(db, c, nach)
    return fix


# ---------------------------------------------------------------- Rückgängig, Regeln, Wartung
def _ort_neu(db: Session, c: Campaign, u: NameFixUndo) -> Ort | None:
    """Den gespeicherten Ort neu aufbauen (unabhängig davon, ob das Wort noch drin steht)."""
    from app import schemas
    from app.services import apply_entry_input

    if u.place in ("chapter", "chapter_title"):
        r = db.get(Recap, u.oid)
        s = db.get(GameSession, u.oid)
        if r is None or s is None or s.campaign_id != c.id:
            return None
        for o in _kapitel_orte(db, c, re.compile(r"(?s).")):  # jedes Kapitel passt
            if o.oid == u.oid and o.place == u.place:
                return o
        return None
    if u.place.startswith("entry_"):
        e = db.get(Entry, u.oid)
        if e is None or e.campaign_id != c.id:
            return None
        if u.place == "entry_alias":
            return Ort(u.place, e.id, ("", ""), {}, lambda _k: {f"a{i}": a for i, a in enumerate(e.aliases)},
                       lambda w: apply_entry_input(db, e, schemas.EntryInput(
                           aliases=[w.get(f"a{i}", a) for i, a in enumerate(e.aliases)])))
        f = {"entry_name": "name", "entry_summary": "summary", "entry_gm_notes": "gm_notes"}[u.place]
        return Ort(u.place, e.id, ("", ""), {}, lambda _k: {f: getattr(e, f) or ""},
                   lambda w: apply_entry_input(db, e, schemas.EntryInput(**{f: w[f]})))
    if u.place == "session_proposal":
        p = db.get(Proposal, u.oid)
        if p is None or p.campaign_id != c.id:
            return None

        def setzen(w):
            p.title = w.get("title", p.title)[:300]
            p.detail = w.get("detail", p.detail)
        return Ort(u.place, p.id, ("", ""), {}, lambda _k: {"title": p.title or "", "detail": p.detail or ""}, setzen)
    if u.place == "transcript":
        sid = u.oid

        def lesen(keys):
            return {x.id: x.text for x in db.scalars(select(TranscriptSegment).where(
                TranscriptSegment.session_id == sid, TranscriptSegment.id.in_(keys)))}

        def setzen(w):
            for x in db.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == sid,
                                                                TranscriptSegment.id.in_(list(w)))):
                x.text = w[x.id]
        return Ort(u.place, sid, ("", ""), {}, lesen, setzen)
    return None


def rueckgaengig(db: Session, c: Campaign, fix: NameFix, lang: str = "de") -> dict:
    if fix.undone_at is not None:
        raise errors.conflict("already_undone")
    bis = fix.undo_until
    if bis is not None and bis.tzinfo is None:
        from datetime import timezone

        bis = bis.replace(tzinfo=timezone.utc)
    if bis is None or bis < utcnow():
        raise errors.conflict("undo_expired")
    zurueck, uebersprungen = 0, []
    for u in db.scalars(select(NameFixUndo).where(NameFixUndo.fix_id == fix.id).order_by(NameFixUndo.id)):
        alt = json.loads(u.alt or "{}")
        o = _ort_neu(db, c, u)
        if o is None:
            continue
        jetzt = o.lesen(list(alt))
        if _pruefsumme(jetzt) != u.pruefsumme:
            uebersprungen.append({"place": u.place, "label": u.label_en if lang == "en" else u.label_de})
            continue
        o.setzen(alt)
        zurueck += sum(len(muster(fix.nach).findall(jetzt.get(k) or "")) for k in alt)
    db.execute(NameFixUndo.__table__.delete().where(NameFixUndo.fix_id == fix.id))
    fix.undone_at, fix.rule, fix.undo_until = utcnow(), False, None
    return {"restored": zurueck, "skipped": uebersprungen}


def ausgeben(fix: NameFix) -> dict:
    def z(t):
        if t is None:
            return None
        return t.isoformat().replace("+00:00", "Z") if t.tzinfo else t.isoformat() + "Z"

    bis = fix.undo_until
    if bis is not None:
        from datetime import timezone

        bis_utc = bis if bis.tzinfo else bis.replace(tzinfo=timezone.utc)
        if bis_utc < utcnow() or fix.undone_at is not None:
            bis = None
    return {"id": fix.id, "from": fix.von, "to": fix.nach, "changed": fix.changed or 0, "rule": bool(fix.rule),
            "createdAt": z(fix.created_at), "createdBy": fix.created_by_member_id or "", "undoUntil": z(bis),
            "undoneAt": z(fix.undone_at)}


def liste(db: Session, c: Campaign) -> list[NameFix]:
    return list(db.scalars(select(NameFix).where(NameFix.campaign_id == c.id)
                           .order_by(NameFix.created_at.desc(), NameFix.id)))


def regeln(db: Session, c: Campaign) -> dict[str, NameFix]:
    """Aktive Verhörer-Regeln je falscher Schreibweise (für „Namen prüfen“)."""
    aus: dict[str, NameFix] = {}
    for f in db.scalars(select(NameFix).where(NameFix.campaign_id == c.id, NameFix.rule.is_(True))
                        .order_by(NameFix.created_at)):
        aus[f.von] = f
    return aus


def wartung(db: Session) -> int:
    """Rückgängig-Texte nach RUECKGAENGIG_STUNDEN löschen."""
    grenze = utcnow() - timedelta(hours=RUECKGAENGIG_STUNDEN)
    alt = [f.id for f in db.scalars(select(NameFix).where(NameFix.created_at < grenze, exists().where(
        NameFixUndo.fix_id == NameFix.id)))]
    if alt:
        db.execute(NameFixUndo.__table__.delete().where(NameFixUndo.fix_id.in_(alt)))
    return len(alt)


def fuer_umzug(db: Session, c: Campaign) -> list[dict]:
    return [{"from": f.von, "to": f.nach} for f in regeln(db, c).values()]


def aus_umzug(db: Session, c: Campaign, roh) -> None:
    if not isinstance(roh, list):
        return
    gesehen = set()
    for r in roh[:MAX_REGELN]:
        if not isinstance(r, dict):
            continue
        try:
            von, nach = eingabe(str(r.get("from") or ""), str(r.get("to") or ""))
        except errors.ApiError:
            continue
        if von in gesehen:
            continue
        gesehen.add(von)
        db.add(NameFix(campaign_id=c.id, von=von, nach=nach, rule=True, changed=0, created_at=utcnow(),
                       created_by_member_id=None, undo_until=None))
