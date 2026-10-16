"""Geheimes im Kapitel (0.4.21, Recap.gmWarnings) – nur für die SL.

Der Server prüft den Kapiteltext gegen die Bibel: Nennt er Name oder Alias eines Eintrags, den nicht alle Spieler
kennen (gm_only, teilweise verborgen, aus Bogen/Hintergrund), oder übernimmt er wörtlich eine längere Stelle aus
gmNotes oder dem Text eines geheimen Eintrags? Ein Hinweis je Code und Eintrag. Veröffentlichen bleibt möglich.
Ein Eintrag, den dieses Kapitel per angenommenem reveal-Vorschlag aufdeckt, zählt nicht.

Hintergrund: Namen geheimer Einträge stehen als Schreibhilfe in der Transkription (hotwords) – verhört sich Whisper,
steht ein geheimer Name in der Abschrift und von dort vielleicht im Kapitel. Die Prüfung läuft ohne Sprachmodell bei
jedem Lesen des Kapitels durch die SL, deshalb ist sie nach jeder Änderung des Textes aktuell.
"""
from __future__ import annotations

import re

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Entry, GameSession, Proposal, Recap
from app.zusammenfassung import _woerter

MIN_NAME = 3  # kürzere Namen und Aliasse treffen zu oft zufällig
WORTFOLGE = 6  # so viele Wörter am Stück gelten als wörtlich übernommen
MAX_HINWEISE = 50


def _absaetze(text: str) -> list[str]:
    return [a for a in re.split(r"\n\s*\n", text or "")]


_ARTIKEL = {"der", "die", "das", "den", "dem", "des", "the"}
_ENDUNG = r"(?:s|'s|’s|es|n|en|e|er|em)?"  # gebeugt: „Tarvoks“, „den Grauen Fürsten“


def _muster(name: str) -> re.Pattern | None:
    """Ganze Wörter, jedes leicht gebeugt; ein führender Artikel darf fehlen oder anders stehen („den Grauen Fürst“)."""
    woerter = name.split()
    artikel = bool(woerter) and woerter[0].casefold() in _ARTIKEL
    kern = woerter[1:] if artikel else woerter
    if len("".join(kern)) < MIN_NAME:
        return None
    teile = [re.escape(w) + _ENDUNG for w in kern]
    vorn = r"(?:(?:" + "|".join(_ARTIKEL) + r")\s+)?" if artikel else ""
    return re.compile(r"(?<!\w)" + vorn + r"\s+".join(teile) + r"(?!\w)", re.IGNORECASE)


def _aufgedeckt(db: Session, s: GameSession) -> set[str]:
    """Einträge, die ein angenommener reveal-Vorschlag dieses Kapitels aufdeckt."""
    return set(db.scalars(select(Proposal.target_entry_id).where(
        Proposal.session_id == s.id, Proposal.action == "reveal", Proposal.decision == "accepted",
        Proposal.target_entry_id.is_not(None))))


def _wortfolgen(absaetze: list[str]) -> dict[tuple[str, ...], set[int]]:
    folgen: dict[tuple[str, ...], set[int]] = {}
    for nr, a in enumerate(absaetze):
        w = _woerter(a)
        for i in range(max(0, len(w) - WORTFOLGE + 1)):
            folgen.setdefault(tuple(w[i:i + WORTFOLGE]), set()).add(nr)
    return folgen


def pruefen(db: Session, s: GameSession, r: Recap) -> list[dict]:
    """Hinweise für die SL als Schnittstellen-Dicts (code, entryIds, names, paragraphs)."""
    absaetze = _absaetze(r.text)
    if not absaetze or not r.text.strip():
        return []
    aufgedeckt = _aufgedeckt(db, s)
    folgen = _wortfolgen(absaetze)
    hinweise: list[dict] = []
    for e in db.scalars(select(Entry).where(Entry.campaign_id == s.campaign_id).order_by(Entry.name)):
        geheim = not e.allgemein_bekannt and e.type != "pc"
        if geheim and e.id not in aufgedeckt:
            namen, stellen = [], set()
            for name in e.alle_namen:
                m = _muster(name)
                if m is None:
                    continue
                treffer = {nr for nr, a in enumerate(absaetze) if m.search(a)}
                if treffer:
                    namen.append(name)
                    stellen |= treffer
            if namen:
                hinweise.append({"code": "secret_entry_name", "entryIds": [e.id], "names": namen,
                                 "paragraphs": sorted(stellen)})
        # Wörtliches aus gmNotes (jeder Eintrag) und aus dem Text geheimer Einträge
        texte = [e.gm_notes or ""] + ([e.summary or ""] if geheim else [])
        stellen = set()
        for t in texte:
            gw = _woerter(t)
            if len(gw) < WORTFOLGE:
                continue
            for i in range(len(gw) - WORTFOLGE + 1):
                stellen |= folgen.get(tuple(gw[i:i + WORTFOLGE]), set())
        if stellen:
            hinweise.append({"code": "secret_wording", "entryIds": [e.id], "names": [], "paragraphs": sorted(stellen)})
        if len(hinweise) >= MAX_HINWEISE:
            break
    return hinweise
