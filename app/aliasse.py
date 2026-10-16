"""„Auch genannt“ (0.4.21): weitere Namen eines Bibel-Eintrags.

Regeln (Schnittstelle EntryInput.aliases): höchstens 10, je 1–40 Zeichen; Doppelte und der Name selbst fallen beim
Speichern weg. Sichtbar wie der Name – Spieler bekommen Aliasse nur bei Einträgen, die sie sehen. Die Recap-Pipeline
bekommt nur Aliasse allgemein bekannter Einträge, Vorschlags-Pipeline, Dublettenprüfung und Namenshilfe alle.
"""
from __future__ import annotations

import json

MAX_ANZAHL = 10
MAX_LAENGE = 40


def bereinigen(name: str, roh) -> list[str]:
    """Leerraum zusammenziehen, kürzen, Doppelte (ohne Groß-/Kleinschreibung) und den Namen selbst entfernen."""
    gesehen = {" ".join((name or "").split()).casefold()}
    aus: list[str] = []
    for x in roh or []:
        if not isinstance(x, str):
            continue
        a = " ".join(x.split())[:MAX_LAENGE]
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
