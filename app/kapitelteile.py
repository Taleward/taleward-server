"""Teile im Kapitel (Schnittstelle 0.4.17): Recap.parts = [{title, firstParagraph}].

Lange Runden bekommen beim Schreiben (Weg „Erst Notizen“) Zwischenüberschriften, wenn es einen natürlichen Einschnitt
gibt (sprachmodell.teile_verorten). Der Text bleibt eine Folge von Absätzen; die Teile sagen nur, vor welchem Absatz
eine Überschrift steht. Absatz = durch Leerzeile getrennter Block, gezählt ab 0 – wie in Prüfteil und Korrektur.
"""
from __future__ import annotations

import json
import re

TITEL_MAX = 120
MAX_TEILE = 20


class Ungueltig(ValueError):
    pass


def anzahl_absaetze(text: str) -> int:
    return len([a for a in re.split(r"\n\s*\n", (text or "").strip()) if a.strip()])


def pruefen(teile: list[dict], text: str) -> list[dict]:
    """Teile streng prüfen (Überschrift 1–120 Zeichen, Index im Text, aufsteigend). Wirft Ungueltig."""
    n = anzahl_absaetze(text)
    if len(teile) > MAX_TEILE:
        raise Ungueltig("zu viele Teile")
    aus, letzter = [], -1
    for t in teile:
        titel = " ".join(str(t.get("title") or "").split())
        i = t.get("firstParagraph")
        if not titel or len(titel) > TITEL_MAX:
            raise Ungueltig("Überschrift")
        if not isinstance(i, int) or isinstance(i, bool) or not (letzter < i < n):
            raise Ungueltig("Absatz")
        aus.append({"title": titel, "firstParagraph": i})
        letzter = i
    return aus


def bereinigt(teile, text: str) -> list[dict]:
    """Für Ergebnisse von Sprachmodell und Worker: Passt etwas nicht, gibt es keine Teile (das Kapitel bleibt lesbar)."""
    if not isinstance(teile, list) or not all(isinstance(t, dict) for t in teile):
        return []
    try:
        return pruefen(teile, text)
    except Ungueltig:
        return []


def lesen(r) -> list[dict]:
    try:
        teile = json.loads(r.parts) if getattr(r, "parts", None) else []
    except ValueError:
        return []
    return bereinigt(teile, r.text)


def schreiben(r, teile: list[dict]) -> None:
    r.parts = json.dumps(teile, ensure_ascii=False) if teile else None
