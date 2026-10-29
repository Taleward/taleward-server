"""Begriffslisten je Rollenspielsystem (app/begriffe/<schlüssel>.txt, 0.4.140).

Aufbau einer Datei (Abschnitte mit „# “, Abschnitte ohne Sprachangabe gelten für beide Sprachen):

    # <Systemname> – Begriffe für die Transkription
    # Erkennungsnamen: name eins, name zwei, …
    # Stufe A (höchstens 40, häufigste zuerst)      – Schreibhilfe für die Abschrift (deutsch bzw. gleich)
    # Stufe A (en)                                    – abweichende englische Schreibweisen
    # Stufe B / # Stufe B (en)                        – nur Wörterbuch (bekannte Schreibweisen nach der Abschrift)
    # Regelbegriffe (de) / # Regelbegriffe (en)       – helfen der Abschrift, kommen im Kapitel nicht vor
    # Verhörer                                        – „Falsch => Richtig“, nur als Vorschlag für die SL

Wohin was geht:
- An die Abschrift (Worker bzw. Cloud, als Schreibhilfe) nur Stufe A in der Sprache der Kampagne und, wenn Platz ist,
  die ersten Regelbegriffe – Whisper nutzt ohnehin nur einen kurzen Vorspann (app/namenshilfe.py).
- Ins Wörterbuch der Prüfung unsicherer Wörter (app/woerterbuch.py) alle Namen und Begriffe.
- In den Regelfilter des Kapitels (app/artefakte.py) die Regelbegriffe beider Sprachen. Ein Regelbegriff, der auch ein
  Alltagswort ist („Schmerz“, „Edge“, „Wunden“), zählt nur mit einer Zahl oder „plus/minus“ dicht daneben als
  Regelrest; ohne Zahl bleibt er Erzähltext. Alltagswort = steht in der allgemeinen Wortliste der Sprache.

Welches System: Campaign.system, wenn es eine Liste dafür gibt; sonst wird Campaign.system_name mit den
Erkennungsnamen verglichen (nur ganze Wörter, der längste passende Name gewinnt, bei Gleichstand keiner).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

ORDNER = Path(__file__).resolve().parent / "begriffe"
SCHREIBHILFE_REGEL = 5  # so viele Regelbegriffe höchstens hinter Stufe A
ZUSATZ_WOERTERBUCH = {"starfinder": ("pathfinder",)}  # gleiche Götter und Orte


@dataclass(frozen=True)
class Liste:
    schluessel: str
    name: str
    erkennung: tuple[str, ...]
    stufe_a: dict[str, tuple[str, ...]] = field(default_factory=dict)  # "" | "en"
    stufe_b: dict[str, tuple[str, ...]] = field(default_factory=dict)
    regel: dict[str, tuple[str, ...]] = field(default_factory=dict)  # "de" | "en"
    verhoerer: tuple[tuple[str, str], ...] = ()


def norm(text: str) -> str:
    t = (text or "").casefold()
    t = re.sub(r"[^\w&]+", " ", t)
    return " ".join(t.split())


def _lesen(datei: Path) -> Liste:
    name, erkennung = "", ()
    abschnitte: dict[str, list[str]] = {}
    aktuell: str | None = None
    for zeile in datei.read_text(encoding="utf-8").splitlines():
        z = zeile.strip()
        if not z:
            continue
        if z.startswith("#"):
            kopf = z[1:].strip()
            klein = kopf.casefold()
            if klein.startswith("erkennungsnamen:"):
                erkennung = tuple(n for n in (norm(x) for x in kopf.split(":", 1)[1].split(",")) if n)
                aktuell = None
            elif klein.startswith("stufe a"):
                aktuell = "a:en" if "(en)" in klein else "a:"
            elif klein.startswith("stufe b"):
                aktuell = "b:en" if "(en)" in klein else "b:"
            elif klein.startswith("regelbegriffe"):
                aktuell = "r:en" if "(en)" in klein else "r:de"
            elif klein.startswith("verhörer"):
                aktuell = "v"
            elif not name:
                name = kopf.split(" – ")[0].strip()
                aktuell = None
            else:
                aktuell = None
            continue
        if aktuell:
            abschnitte.setdefault(aktuell, []).append(z)
    verh = []
    for z in abschnitte.get("v", []):
        if "=>" in z:
            falsch, richtig = (t.strip() for t in z.split("=>", 1))
            if falsch and richtig:
                verh.append((falsch, richtig))

    def teil(prefix: str, sprachen) -> dict[str, tuple[str, ...]]:
        return {s: tuple(dict.fromkeys(abschnitte.get(f"{prefix}:{s}", []))) for s in sprachen}

    return Liste(schluessel=datei.stem, name=name or datei.stem, erkennung=erkennung, stufe_a=teil("a", ("", "en")),
                 stufe_b=teil("b", ("", "en")), regel=teil("r", ("de", "en")), verhoerer=tuple(verh))


@lru_cache
def alle() -> dict[str, Liste]:
    return {d.stem: _lesen(d) for d in sorted(ORDNER.glob("*.txt"))}


def liste(schluessel: str | None) -> Liste | None:
    return alle().get(schluessel or "")


def system_fuer(system: str | None, system_name: str | None = None) -> str | None:
    """Schlüssel der passenden Liste oder None."""
    if system and system != "other" and system in alle():
        return system
    name = f" {norm(system_name or '')} "
    if not name.strip():
        return None
    treffer: dict[str, int] = {}
    for s, eintrag in alle().items():
        laenge = max((len(e) for e in eintrag.erkennung if f" {e} " in name), default=0)
        if laenge:
            treffer[s] = laenge
    if not treffer:
        return None
    best = max(treffer.values())
    gewinner = [s for s, n in treffer.items() if n == best]
    return gewinner[0] if len(gewinner) == 1 else None


def _sprache(sprache: str | None) -> str:
    return "en" if sprache == "en" else "de"


def schreibhilfe(schluessel: str | None, sprache: str | None) -> list[str]:
    """Für die Abschrift: Stufe A der Sprache (das Häufigste zuerst), danach die ersten Regelbegriffe."""
    eintrag = liste(schluessel)
    if eintrag is None:
        return []
    sp = _sprache(sprache)
    a = (list(eintrag.stufe_a.get("en", ())) + list(eintrag.stufe_a.get("", ()))) if sp == "en" else list(eintrag.stufe_a.get("", ()))
    return list(dict.fromkeys(a + list(eintrag.regel.get(sp, ())[:SCHREIBHILFE_REGEL])))


def woerterbuch(schluessel: str | None) -> list[str]:
    """Alle Namen und Begriffe (beide Sprachen, auch Stufe B und die richtigen Formen der Verhörer)."""
    aus: list[str] = []
    for s in (schluessel, *ZUSATZ_WOERTERBUCH.get(schluessel or "", ())):
        eintrag = liste(s)
        if eintrag is None:
            continue
        for teil in (eintrag.stufe_a, eintrag.stufe_b, eintrag.regel):
            for eintraege in teil.values():
                aus += eintraege
        aus += [richtig for _, richtig in eintrag.verhoerer]
    return list(dict.fromkeys(aus))


def verhoerer(schluessel: str | None) -> dict[str, str]:
    """Falsch (klein, normalisiert) → Richtig. Nur als Vorschlag, nie still."""
    eintrag = liste(schluessel)
    return {norm(f): r for f, r in eintrag.verhoerer} if eintrag else {}


def regelwoerter(schluessel: str | None, allgemein: frozenset[str] | set[str] = frozenset()
                 ) -> tuple[tuple[str, ...], tuple[str, ...]]:
    """(Fachwörter, Alltagswörter) aus den Regelbegriffen beider Sprachen. Alltagswort: jedes Wort steht in der
    allgemeinen Wortliste. Ohne Wortliste gelten einzelne Wörter vorsichtshalber als Alltagswörter (nur mit Zahl)."""
    eintrag = liste(schluessel)
    if eintrag is None:
        return (), ()
    fach, alltag = [], []
    for w in dict.fromkeys(eintrag.regel.get("de", ()) + eintrag.regel.get("en", ())):
        teile = [t.casefold() for t in re.findall(r"[^\W\d_]+", w)]
        if not teile:
            continue  # nur Zahlen wie „Nat 20“ allein: nichts zu filtern
        if allgemein:
            ist_alltag = all(t in allgemein for t in teile)
        else:
            ist_alltag = len(teile) == 1
        (alltag if ist_alltag else fach).append(w)
    return tuple(fach), tuple(alltag)
