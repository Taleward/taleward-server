"""„Auch genannt“ (0.4.21): weitere Namen eines Bibel-Eintrags.

Regeln (Schnittstelle EntryInput.aliases): höchstens 10, je 1–40 Zeichen; Doppelte und der Name selbst fallen beim
Speichern weg. Sichtbar wie der Name – Spieler bekommen Aliasse nur bei Einträgen, die sie sehen. Die Recap-Pipeline
bekommt nur Aliasse allgemein bekannter Einträge, Vorschlags-Pipeline, Dublettenprüfung und Namenshilfe alle.
"""
from __future__ import annotations

import json
import re

MAX_ANZAHL = 10
MAX_LAENGE = 40


# Steuerzeichen und Richtungszeichen (z. B. U+202E) verdrehen die Anzeige bei Spielenden – nie speichern.
# ZWJ/ZWNJ bleiben (gehören zu manchen Schriften und Emoji).
_UNSICHTBAR = re.compile("[" + "".join(f"{chr(a)}-{chr(b)}" for a, b in (
    (0x00, 0x1F), (0x7F, 0x9F), (0x61C, 0x61C), (0x200E, 0x200F), (0x202A, 0x202E), (0x2066, 0x2069))) + "]")


def zeichen_bereinigen(text: str) -> str:
    """Steuer- und Richtungszeichen entfernen, Leerraum zusammenziehen (für Aliasse und Namen)."""
    return " ".join(_UNSICHTBAR.sub(" ", text or "").split())


def bereinigen(name: str, roh) -> list[str]:
    """Leerraum zusammenziehen, kürzen, Doppelte (ohne Groß-/Kleinschreibung) und den Namen selbst entfernen."""
    gesehen = {zeichen_bereinigen(name).casefold()}
    aus: list[str] = []
    for x in roh or []:
        if not isinstance(x, str):
            continue
        a = zeichen_bereinigen(x)[:MAX_LAENGE].strip()
        if a and a.casefold() not in gesehen:
            gesehen.add(a.casefold())
            aus.append(a)
    return aus[:MAX_ANZAHL]


def schreiben(liste: list[str]) -> str | None:
    return json.dumps(liste, ensure_ascii=False) if liste else None


def hinzufuegen(eintrag, neu: str) -> bool:
    """Eine Schreibweise ergänzen („Dort ergänzen“, 0.4.21), wenn sie weder Name noch Alias ist und noch Platz ist.
    True = ergänzt."""
    vorher = eintrag.aliases
    liste = bereinigen(eintrag.name, [*vorher, neu])
    if liste == vorher:
        return False
    eintrag.aliases_json = schreiben(liste)
    return True
