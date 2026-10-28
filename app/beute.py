"""Wer hat den Gegenstand? (Schnittstelle 0.4.26)

Das Sprachmodell nennt bei Beute nur den Namen der Figur, wie er am Tisch fällt. Der Server löst ihn selbst auf – nur
auf aktive Mitglieder mit Rolle player, über Charaktername, Spitzname sowie Name und Aliasse ihres pc-Eintrags. Nie
raten: Passt kein Name eindeutig, bleibt der Halter leer (die SL sieht den Vorschlag ohne Halter und wählt selbst).
"""
from __future__ import annotations

import re
import unicodedata

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Entry, Member

MIN_VORNAME = 3  # kürzere Teilnamen treffen zu leicht Falsches


def _norm(text: str) -> str:
    t = unicodedata.normalize("NFKC", text or "").casefold()
    t = re.sub(r"[^\w\s'-]", " ", t)
    return " ".join(t.replace("'", " ").replace("-", " ").split())


class Halter:
    """Einmal je Speichern aufgebaut: Namen → Mitglieder der Kampagne."""

    def __init__(self, db: Session, campaign_id: str):
        spieler = [m for m in db.scalars(select(Member).where(Member.campaign_id == campaign_id,
                                                              Member.role == "player")) if m.aktiv]
        ids = {m.id for m in spieler}
        namen: dict[str, set[str]] = {m.id: set() for m in spieler}
        for m in spieler:
            for n in (m.character_name, m.character_nickname):
                if n and _norm(n):
                    namen[m.id].add(_norm(n))
        for e in db.scalars(select(Entry).where(Entry.campaign_id == campaign_id, Entry.type == "pc")):
            if e.holder_member_id in ids:
                for n in [e.name, *e.aliases]:
                    if n and _norm(n):
                        namen[e.holder_member_id].add(_norm(n))
        self._ganz: dict[str, set[str]] = {}
        self._teil: dict[str, set[str]] = {}
        for mid, ns in namen.items():
            for n in ns:
                self._ganz.setdefault(n, set()).add(mid)
                for w in n.split():
                    if len(w) >= MIN_VORNAME:
                        self._teil.setdefault(w, set()).add(mid)

    def aufloesen(self, name: str | None) -> str | None:
        """Mitglied zum Namen oder None, wenn kein Name eindeutig passt."""
        n = _norm(name or "")
        if not n:
            return None
        treffer = self._ganz.get(n)
        if treffer:
            return next(iter(treffer)) if len(treffer) == 1 else None
        if " " not in n:  # nur ein Wort, z. B. der Vorname: nur, wenn es genau eine Figur so heißt
            treffer = self._teil.get(n)
            if treffer and len(treffer) == 1:
                return next(iter(treffer))
        return None
