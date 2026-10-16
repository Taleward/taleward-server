"""Dublettenprüfung für „neu anlegen“-Vorschläge (0.4.21, Proposal.match) – ohne Sprachmodell.

Ein create-Vorschlag wird gegen Name und Aliasse aller Einträge der Kampagne geprüft (auch gm_only; die Vorschläge
sieht nur die SL). Spielercharaktere (type pc) sind nie Ziel – die pflegt die App. Drei Arten, in dieser Rangfolge:
- spelling: Schreibweise fast gleich (kleiner Zeichenabstand, SequenceMatcher),
- sound: gleicher Klang (Kölner Phonetik), z. B. Tarvok/Tharvok/Darvok,
- part_name: ein Teil des einen Namens ist der andere („Hauptmann Varek“ gegen „Varek“).
Höchstens ein Treffer, der beste. Kurze Wörter und reine Gattungswörter (der Alte, die Wirtin) treffen nur
buchstabengleich, damit ähnliche, aber verschiedene Namen nicht zusammengeworfen werden.
"""
from __future__ import annotations

import json
import re
from difflib import SequenceMatcher

from app.unsicher import phonetik

SCHREIBWEISE_AB = 0.84  # SequenceMatcher-Ähnlichkeit ganzer Namen
KLANG_SCHREIBWEISE_AB = 0.67  # gleicher Klang zählt nur, wenn auch die Schreibweise nicht zu weit weg ist
MIN_LAENGE = 4  # kürzere Namen nur buchstabengleich
_FUELLWOERTER = {"der", "die", "das", "den", "dem", "des", "ein", "eine", "einer", "the", "a", "an", "of", "von",
                 "vom", "zu", "zur", "zum", "und", "and", "im", "in", "am", "an", "auf", "the"}
_TITEL = {"hauptmann", "herr", "frau", "meister", "meisterin", "baron", "baronin", "graf", "gräfin", "könig",
          "königin", "fürst", "fürstin", "bruder", "schwester", "vater", "mutter", "doktor", "dr", "captain", "lord",
          "lady", "sir", "king", "queen", "master", "brother", "sister", "father", "mother", "prinz", "prinzessin",
          "prince", "princess", "herzog", "herzogin", "duke", "duchess", "ritter", "knight", "kapitän", "general"}


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^\w\s'-]", " ", (s or "").casefold()).split())


def _woerter(s: str) -> list[str]:
    return [w for w in re.findall(r"[\w'-]+", _norm(s)) if w not in _FUELLWOERTER]


def _kern(s: str) -> list[str]:
    """Wörter ohne Titel/Anreden – der eigentliche Name."""
    w = _woerter(s)
    kern = [x for x in w if x not in _TITEL]
    return kern or w


def _eigenname(woerter: list[str], allgemein) -> bool:
    """Nur Wörter, die nicht im allgemeinen Wörterbuch stehen, gelten als Namen (Klang- und Teilnamen-Treffer)."""
    return bool(woerter) and all(len(w) >= MIN_LAENGE and w not in allgemein for w in woerter)


def _vergleich(titel: str, kandidat: str, allgemein=frozenset()) -> str | None:
    """Art des Treffers oder None."""
    a, b = _norm(titel), _norm(kandidat)
    if not a or not b:
        return None
    if a == b:
        return "spelling"
    if min(len(a), len(b)) < MIN_LAENGE:
        return None
    if SequenceMatcher(None, a, b).ratio() >= SCHREIBWEISE_AB:
        return "spelling"
    ka, kb = _kern(titel), _kern(kandidat)
    if ka == kb:
        return "part_name"  # nur Titel oder Anrede unterscheiden sich („Hauptmann Varek“ / „Varek“)
    if len(ka) != len(kb):
        # Teilname: alle Kernwörter des kürzeren Namens stecken im längeren (buchstabengleich oder gleich klingend)
        kurz, lang = (ka, kb) if len(ka) <= len(kb) else (kb, ka)
        if _eigenname(kurz, allgemein):
            lang_phon = {phonetik(w) for w in lang if len(w) >= MIN_LAENGE}
            if all(w in lang or (phonetik(w) and phonetik(w) in lang_phon) for w in kurz):
                return "part_name"
        return None
    pa, pb = "".join(phonetik(w) for w in ka), "".join(phonetik(w) for w in kb)
    if pa and len(pa) >= 3 and pa == pb and _eigenname(ka, allgemein) \
            and SequenceMatcher(None, " ".join(ka), " ".join(kb)).ratio() >= KLANG_SCHREIBWEISE_AB:
        return "sound"
    return None


_RANG = {"spelling": 0, "sound": 1, "part_name": 2}


def finden(titel: str, eintraege, allgemein=frozenset(), ausser_id: str | None = None) -> dict | None:
    """Bester Treffer unter den Einträgen (Objekte mit id, type, name, alle_namen) als Schnittstellen-Dict oder None.
    allgemein: Wörterbuch der Sprache (app/woerterbuch.py) – Gattungswörter treffen nur buchstabengleich."""
    beste: tuple[int, float, dict] | None = None
    for e in eintraege:
        if e.type == "pc" or e.id == ausser_id:
            continue
        for name in e.alle_namen:
            art = _vergleich(titel, name, allgemein)
            if art is None:
                continue
            guete = SequenceMatcher(None, _norm(titel), _norm(name)).ratio()
            kandidat = (_RANG[art], -guete, {"entryId": e.id, "name": e.name, "matchedName": name,
                                             "entryType": e.type, "kind": art})
            if beste is None or kandidat[:2] < beste[:2]:
                beste = kandidat
    return beste[2] if beste else None


def schreiben(treffer: dict | None) -> str | None:
    return json.dumps(treffer, ensure_ascii=False) if treffer else None


def lesen(roh: str | None) -> dict | None:
    if not roh:
        return None
    try:
        d = json.loads(roh)
    except ValueError:
        return None
    return d if isinstance(d, dict) and d.get("entryId") else None
