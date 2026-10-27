"""Charakterbögen und Hintergründe auswerten (Schnittstelle 0.4.18).

Ablauf: Die SL schaltet `allow_character_analysis` für die Kampagne ein. Eine Spielerin lädt einen Charakterbogen hoch
oder gibt ihren Hintergrund ab → CharacterAnalysis im Zustand awaiting_gm und ein Hinweis an die SL. Erst nach deren
Zustimmung wird gelesen: Auftrag „document“ (wie SL-Unterlagen, auch auf älteren Workern) mit `analysis_id`. Ergebnis
sind Vorschläge mit source character, über die die SL wie bei der mitgebrachten Welt sofort entscheidet.

Verbindlich (hier erzwungen, nicht dem Sprachmodell überlassen):
- Eingabe nur dieses eine Dokument bzw. dieser Hintergrund, als Daten; Kontext nur Bibel-Einträge, die dieses Mitglied
  sehen darf – ohne gmNotes, nie Daten anderer Mitglieder.
- Vorschläge public mit hiddenFromMemberIds = alle anderen aktiven Spieler (wissen nur SL und Urheberin). Ergänzt ein
  Vorschlag einen vorhandenen Eintrag, kommt das Neue nur in den geheimen Teil – der öffentliche Text bleibt.
- Keine Links in Vorschlägen. Höchstens die ersten 20 Seiten. Cloud nur, wenn die Kampagne es beim Anlegen der Anfrage
  erlaubt hat (uses_cloud) und noch erlaubt.
- Spieler sehen nur den Stand, nie die Vorschläge (auch nicht deren Anzahl).
- Der auszuwertende Text bleibt nur bis zum Ende der Auswertung auf dem Server (Bogen-Datei unberührt).
"""
from __future__ import annotations

import json
import re

from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from app import errors, schemas
from app.db import utcnow
from app.models import (Campaign, CampaignDocument, CharacterAnalysis, Entry, GmNotice, Job, Member, Proposal,
                        UsageLog)

MAX_SEITEN = 20
MAX_BOEGEN = 3
ZEICHEN_JE_SEITE = 3000  # Texte ohne Seiten (txt, md, Hintergrund): so viel gilt als eine Seite
LISTE_HOECHSTENS = 100
OFFEN = ("awaiting_gm", "queued", "processing")
HINWEIS = "character_analysis_pending"
# Adressen mit Schema oder www., jede Domain mit Pfad („foo.xyz/x“) und E-Mail-Adressen (Link-Filter)
_LINK = re.compile(r"(?i)\b(?:[a-z][a-z0-9+.-]*://|www\.)\S+|\b(?:[a-z0-9-]+\.)+[a-z]{2,}/\S*|[\w.+-]+@[\w-]+(?:\.[\w-]+)+")


# ---------------------------------------------------------------- Anlegen
def _neu(db: Session, c: Campaign, me: Member, source: str, titel: str, document_id: str | None = None,
         abschnitte: list[dict] | None = None) -> CharacterAnalysis:
    a = CharacterAnalysis(campaign_id=c.id, member_id=me.id, source=source, document_id=document_id,
                          title=(titel or "").strip()[:300] or "?", state="awaiting_gm",
                          uses_cloud=bool(c.allow_cloud_summary), pages_limited=False,
                          abschnitte=json.dumps(abschnitte, ensure_ascii=False) if abschnitte is not None else None)
    db.add(a)
    db.flush()
    db.add(GmNotice(campaign_id=c.id, code=HINWEIS, member_id=me.id, entry_ids="[]", analysis_id=a.id))
    return a


def bogen_hochgeladen(db: Session, doc: CampaignDocument, me: Member) -> CharacterAnalysis | None:
    """Nach dem Hochladen eines Charakterbogens: mit eingeschalteter Auswertung eine Anfrage an die SL."""
    c = db.get(Campaign, doc.campaign_id)
    if c is None or not c.allow_character_analysis:
        return None
    return _neu(db, c, me, "sheet", doc.title, document_id=doc.id)


def hintergrund_abgeben(db: Session, me: Member) -> CharacterAnalysis:
    """POST …/members/me/backstory-analysis. Ausgewertet wird der Hintergrund zum Zeitpunkt dieser Anfrage."""
    c = db.get(Campaign, me.campaign_id)
    if not c.allow_character_analysis:
        raise errors.conflict("analysis_off")
    text = (me.character_backstory or "").strip()
    if not text:
        raise errors.conflict("backstory_empty")
    offen = db.scalar(select(CharacterAnalysis.id).where(
        CharacterAnalysis.member_id == me.id, CharacterAnalysis.source == "backstory",
        CharacterAnalysis.state.in_(OFFEN)).limit(1))
    if offen is not None:
        raise errors.conflict("analysis_pending")
    return _neu(db, c, me, "backstory", me.character_name or "?", abschnitte=[{"seite": None, "text": text}])


# ---------------------------------------------------------------- Entscheiden, abschalten, entfallen
def _hinweis_weg(db: Session, a: CharacterAnalysis) -> None:
    db.execute(delete(GmNotice).where(GmNotice.code == HINWEIS, GmNotice.analysis_id == a.id))


def _ablehnen(db: Session, a: CharacterAnalysis) -> None:
    a.state, a.decided_at, a.abschnitte = "rejected", utcnow(), None
    _hinweis_weg(db, a)


def _seiten_begrenzen(abschnitte: list[dict], seiten: int | None) -> tuple[list[dict], int | None, bool]:
    """Höchstens die ersten 20 Seiten; ohne Seitenangaben nach Zeichen (20 × 3000)."""
    if any(x.get("seite") for x in abschnitte):
        drin = [x for x in abschnitte if not x.get("seite") or int(x["seite"]) <= MAX_SEITEN]
        return drin, seiten, bool(seiten and seiten > MAX_SEITEN) or len(drin) < len(abschnitte)
    grenze, summe, drin = MAX_SEITEN * ZEICHEN_JE_SEITE, 0, []
    for x in abschnitte:
        rest = grenze - summe
        if rest <= 0:
            break
        drin.append({"seite": None, "text": x["text"][:rest]})
        summe += len(drin[-1]["text"])
    gesamt = sum(len(x["text"]) for x in abschnitte)
    return drin, seiten or max(1, -(-gesamt // ZEICHEN_JE_SEITE)), gesamt > grenze


def _bogen_lesen(db: Session, a: CharacterAnalysis) -> list[dict]:
    """Charakterbogen erst jetzt (nach der Zustimmung der SL) auslesen."""
    from app import unterlagen
    from app.queue import status_message

    doc = db.get(CampaignDocument, a.document_id or "")
    pfad = unterlagen.datei_pfad(doc.id) if doc is not None else None
    if pfad is None:
        a.message = status_message("doc.no_text")
        return []
    try:
        daten = pfad.read_bytes()
        x = unterlagen.auslesen(unterlagen.art_der_datei(doc.file_name or pfad.name, daten), daten)
    except errors.ApiError:
        a.message = status_message("doc.no_text")
        return []
    abschnitte, a.page_count, a.pages_limited = _seiten_begrenzen(x.abschnitte, x.seiten)
    if doc.page_count is None and x.seiten:
        doc.page_count = x.seiten
    if not abschnitte:
        a.message = status_message("doc.no_text")
    return abschnitte


def entscheiden(db: Session, a: CharacterAnalysis, zustimmen: bool, user_id: str | None = None) -> None:
    """POST /character-analyses/{id}/decision (nur SL, nur awaiting_gm). 0.4.25: Zustimmen gilt zugleich als Freigabe
    über dem Freigabe-Betrag – außer beim Topf der Organisation (dort geben die Verwalter frei)."""
    if a.state != "awaiting_gm":
        raise errors.conflict("wrong_state", "wrong_state.analysis")
    if not zustimmen:
        _ablehnen(db, a)
        return
    a.decided_at = utcnow()
    _hinweis_weg(db, a)
    if a.source == "sheet":
        abschnitte = _bogen_lesen(db, a)
    else:
        abschnitte, a.page_count, a.pages_limited = _seiten_begrenzen(json.loads(a.abschnitte or "[]"), None)
    if not abschnitte:
        a.state, a.abschnitte = "failed", None
        return
    a.abschnitte = json.dumps(abschnitte, ensure_ascii=False)
    a.state = "queued"
    job = Job(type="document", analysis_id=a.id, required_capability="llm", engine="local")
    from app import abrechnung

    if abrechnung.an(db) and user_id is not None:
        g = abrechnung.zahler(db, db.get(Campaign, a.campaign_id))
        if g is not None and not abrechnung.ist_topf(g):
            job.kosten_freigegeben_at = utcnow()
            abrechnung.protokoll(db, user_id, "auftrag_freigegeben", g.id, auswertung=a.id, zustimmung=True)
    db.add(job)


def abgeschaltet(db: Session, c: Campaign) -> None:
    """allowCharacterAnalysis aus: offene Anfragen (awaiting_gm) gelten als abgelehnt. Schon zugestimmte Auswertungen
    (queued, processing) laufen zu Ende – so steht es in der Schnittstelle; die SL hat ihnen einzeln zugestimmt."""
    for a in db.scalars(select(CharacterAnalysis).where(CharacterAnalysis.campaign_id == c.id,
                                                       CharacterAnalysis.state == "awaiting_gm")):
        _ablehnen(db, a)


def bogen_geloescht(db: Session, doc: CampaignDocument) -> None:
    """Bogen gelöscht: eine noch nicht entschiedene Anfrage entfällt; die übrigen verlieren nur den Verweis."""
    for a in db.scalars(select(CharacterAnalysis).where(CharacterAnalysis.document_id == doc.id)):
        if a.state == "awaiting_gm":
            _ablehnen(db, a)
        a.document_id = None


def mitglied_weg(db: Session, member_id: str) -> None:
    """Verlassen, entfernt oder Konto gelöscht: Was noch nicht läuft, entfällt – samt dem gespeicherten Text."""
    for a in db.scalars(select(CharacterAnalysis).where(CharacterAnalysis.member_id == member_id,
                                                       CharacterAnalysis.state.in_(("awaiting_gm", "queued")))):
        if a.state == "queued":
            db.execute(delete(Job).where(Job.analysis_id == a.id, Job.state == "queued"))
        _ablehnen(db, a)


def kampagne_entfernt(db: Session, campaign_id: str) -> None:
    """Vor dem Löschen der Kampagne: Aufträge der Auswertungen mit weg (die Auswertungen selbst per Kaskade)."""
    ids = select(CharacterAnalysis.id).where(CharacterAnalysis.campaign_id == campaign_id)
    db.execute(delete(Job).where(Job.analysis_id.in_(ids)))


# ---------------------------------------------------------------- Lesen
def zu(db: Session, analysis_id: str, me_von) -> tuple[CharacterAnalysis, Member]:
    """Auswertung samt Mitgliedschaft des Aufrufers; Fremdes → 404."""
    a = db.get(CharacterAnalysis, analysis_id)
    me = me_von(a.campaign_id) if a is not None else None
    if a is None or me is None:
        raise errors.not_found("analysis")
    return a, me


def liste(db: Session, c: Campaign, me: Member) -> list[CharacterAnalysis]:
    stmt = select(CharacterAnalysis).where(CharacterAnalysis.campaign_id == c.id)
    if me.role != "gm":
        stmt = stmt.where(CharacterAnalysis.member_id == me.id)
    return list(db.scalars(stmt.order_by(CharacterAnalysis.created_at.desc(), CharacterAnalysis.id)
                           .limit(LISTE_HOECHSTENS)))


def _kosten(db: Session, a: CharacterAnalysis, user_id: str | None) -> tuple[int | None, dict | None]:
    """0.4.25: (costEstimateCents, creditWait) – nur für die SL der Kampagne, Betrag nur für Personen, die das zahlende
    Guthaben sehen dürfen."""
    from app import abrechnung

    if user_id is None or not abrechnung.an(db) or a.state not in ("awaiting_gm", "queued"):
        return None, None
    c = db.get(Campaign, a.campaign_id)
    if not abrechnung.ist_aktive_sl(db, user_id, c):
        return None, None
    job = db.scalar(select(Job).where(Job.analysis_id == a.id, Job.state == "queued").limit(1)) \
        if a.state == "queued" else None
    g = abrechnung.zahler(db, c)
    schaetzung = None
    if g is not None and abrechnung.darf_sehen(db, user_id, g):
        schaetzung = abrechnung.schaetzung(db, job if job is not None else Job(type="document", analysis_id=a.id))
    return schaetzung, abrechnung.warten_fuer(db, job, user_id) if job is not None else None


def out(db: Session, a: CharacterAnalysis, sl: bool, lang: str = "de",
        user_id: str | None = None) -> schemas.CharacterAnalysisOut:
    anzahl = None
    if sl and a.state == "done":
        anzahl = db.scalar(select(func.count()).select_from(Proposal).where(Proposal.analysis_id == a.id)) or 0
    schaetzung, warten = _kosten(db, a, user_id if sl else None)
    return schemas.CharacterAnalysisOut(
        id=a.id, campaign_id=a.campaign_id, member_id=a.member_id, source=a.source, document_id=a.document_id,
        title=a.title, state=a.state, uses_cloud=a.uses_cloud, page_count=a.page_count,
        pages_limited=a.pages_limited, message=_meldung(a, sl, lang) if a.state == "failed" else None,
        proposal_count=anzahl, cost_estimate_cents=schaetzung, credit_wait=warten, created_at=a.created_at,
        decided_at=a.decided_at)


FESTE_GRUENDE = ("doc.no_text",)  # Meldungen ohne Platzhalter – alles andere wird zum festen Satz


def _meldung(a: CharacterAnalysis, sl: bool, lang: str) -> str | None:
    """Grund bei failed: nur ein fester Satz des Servers, nie Inhalte aus Bogen, Hintergrund, Vorschlägen, Bibel oder
    die Rückmeldung von Modell bzw. Worker (YAML 0.4.18, `CharacterAnalysis.message`)."""
    try:
        schluessel = json.loads(a.message or "{}").get("key")
    except (ValueError, AttributeError):
        schluessel = None
    if schluessel not in FESTE_GRUENDE:
        schluessel = "analysis.failed_sl" if sl else "analysis.failed"
    return errors.ApiError(0, schluessel).message(lang)


def zu_bogen(db: Session, doc: CampaignDocument) -> str | None:
    """CampaignDocument.analysisId: die neueste Auswertung dieses Bogens."""
    return db.scalar(select(CharacterAnalysis.id).where(CharacterAnalysis.document_id == doc.id)
                     .order_by(CharacterAnalysis.created_at.desc()).limit(1))


# ---------------------------------------------------------------- Auftrag
def fuer_auftrag(db: Session, job: Job) -> CharacterAnalysis | None:
    return db.get(CharacterAnalysis, job.analysis_id) if job.analysis_id else None


def _sichtbar_fuer(db: Session, a: CharacterAnalysis) -> list[Entry]:
    """Öffentliche Einträge, die dieses Mitglied sehen darf."""
    return [e for e in db.scalars(select(Entry).where(Entry.campaign_id == a.campaign_id,
                                                      Entry.visibility == "public").order_by(Entry.name))
            if a.member_id not in e.hidden_member_ids]


def eingabe(db: Session, a: CharacterAnalysis) -> dict:
    """Eingabe in der Form der Unterlagen (art „handout“: alles darin ist Wissen dieser Spielerin, ohne geheimen Teil) –
    so verstehen auch ältere Worker den Auftrag. `quelle` wählt bei neueren die passende Anweisung."""
    c = db.get(Campaign, a.campaign_id)
    bibel = [{"id": e.id, "typ": e.type, "name": e.name, "aliases": e.aliases, "zusammenfassung": e.summary or "",
              "gm_notes": None,
              # teilweise verborgen = nicht allgemein bekannt → im Aufruf als geheim (wie bei Unterlagen)
              "sichtbarkeit": "public" if e.allgemein_bekannt else "teilweise"}
             for e in _sichtbar_fuer(db, a)]
    return {"sprache": c.language, "kampagne": c.title, "system": c.system, "system_name": c.system_name,
            "welt": c.world_info, "art": "handout", "quelle": "character", "titel": a.title,
            "abschnitte": json.loads(a.abschnitte or "[]"), "bibel": bibel}


def _ohne_links(text: str | None) -> str | None:
    if text is None:
        return None
    return re.sub(r"[ \t]{2,}", " ", _LINK.sub("", text)).strip()


def speichern(db: Session, a: CharacterAnalysis, d: dict, engine: str, worker_id: str | None, rechenzeit: float,
              kosten_cent: int = 0) -> None:
    """Vorschläge anlegen – mit den festen Regeln oben."""
    from app.sprachmodell import dokument_pruefen

    me = db.get(Member, a.member_id)
    if me is None or not me.aktiv:  # inzwischen ausgetreten – keine Vorschläge aus ihrem Material
        a.state, a.abschnitte, a.decided_at = "rejected", None, a.decided_at or utcnow()
        proposals_d = None
    else:
        proposals_d = d.get("proposals") or []
    sichtbar = {e.id: e for e in _sichtbar_fuer(db, a)}
    # pc-Einträge pflegt die App – keine Vorschläge dafür, auch nicht als neuer Eintrag gleichen Namens
    spieler = {e.name.casefold() for e in db.scalars(select(Entry).where(Entry.campaign_id == a.campaign_id,
                                                                         Entry.type == "pc"))}
    if me is not None and me.character_name:
        spieler.add(me.character_name.casefold())
    # 0.4.91: offene Plätze zählen mit – wer einen Platz einnimmt, durchläuft den Neuzugang nicht
    andere = sorted(m.id for m in db.scalars(select(Member).where(Member.campaign_id == a.campaign_id))
                    if (m.aktiv or m.open_seat) and m.id != a.member_id and m.role != "gm")
    vorschlaege = dokument_pruefen(proposals_d or [], {i: e.name for i, e in sichtbar.items() if e.type != "pc"})
    db.execute(delete(Proposal).where(Proposal.analysis_id == a.id))
    pos = 0
    for v in vorschlaege:
        titel = (_ohne_links(v["title"]) or "")[:300]
        if not titel or titel.casefold() in spieler:
            continue
        detail, gm_notes = _ohne_links(v["detail"]) or "", _ohne_links(v["gmNotes"]) or None
        if v["action"] == "update":  # Neues zu einem vorhandenen Eintrag: nur in den geheimen Teil
            gm_notes, detail = "\n\n".join(t for t in (detail, gm_notes) if t) or None, ""
        elif not detail and gm_notes:  # neuer Eintrag braucht einen Text (das Material kennt die Spielerin ohnehin)
            detail, gm_notes = gm_notes, None
        if not detail and not gm_notes:
            continue
        db.add(Proposal(
            campaign_id=a.campaign_id, source="character", analysis_id=a.id, submitted_by_member_id=a.member_id,
            origin_character_id=me.character_id if me is not None else None, position=pos,
            entry_type=v["entryType"], action=v["action"], target_entry_id=v["targetEntryId"], title=titel,
            detail=detail, gm_notes=gm_notes, suggested_visibility="public", hidden_member_ids_json=json.dumps(andere),
            public_suggested=False, confidence=v["confidence"], flags=json.dumps(v["flags"]),
            evidence=json.dumps([{**b, "quote": _ohne_links(b["quote"]) or ""} for b in v["evidence"]],
                                ensure_ascii=False),
            decision="open",
        ))
        pos += 1
    if proposals_d is not None:
        a.state, a.abschnitte = "done", None
    db.add(UsageLog(campaign_id=a.campaign_id, analysis_id=a.id, kind="document", engine=engine,
                    model=d.get("model"), worker_id=worker_id, compute_seconds=rechenzeit,
                    tokens_in=int(d.get("tokensIn") or 0), tokens_out=int(d.get("tokensOut") or 0),
                    cost_cents=kosten_cent))


def zustand(db: Session, job: Job, neu: str, meldung: str | None = None) -> None:
    """Aus der Warteschlange: queued | processing | failed."""
    a = fuer_auftrag(db, job)
    if a is None or a.state in ("done", "rejected"):
        return
    a.state = neu
    if neu == "failed":
        a.abschnitte = None
        if meldung:
            a.message = meldung


def seiten(a: CharacterAnalysis) -> int:
    """Menge für die Abrechnung (wie Unterlagen: Seiten)."""
    return max(1, min(a.page_count or 1, MAX_SEITEN))
