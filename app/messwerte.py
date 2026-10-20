"""Messwerte je Auftrag (0.4.114): Was ein Worker beim Verarbeiten gemessen hat – damit Worker und Modelle
vergleichbar werden. Abschrift: Rechenzeit je Aufnahmestunde und Laufzeit je Schritt; Kapitel, Unterlagen und
Korrektur: Modell, Kontext, Token/s beim Einlesen und Schreiben, ob das Modell ganz auf der Grafikkarte lag.

Nur für die Verwaltung (und die Worker-App), nie für Spielende. Gespeichert wird nur, was hier bekannt ist."""
from __future__ import annotations

import json
import math

ZAHLEN = ("minutenJeStunde", "einlesenTokS", "schreibenTokS", "kontext", "kartenspeicherMb")
TEXTE = ("art", "modell", "quantisierung", "karte")
WAHRHEITEN = ("ganzAufKarte", "abgestuft")
STUFEN = ("einlesen", "erkennung", "ausrichtung", "sprechertrennung", "auswertung")


# Texte der Verwaltung (Übersetzung in app/verwaltung/i18n.py, tests/test_server_04114.py prüft sie)
TEXTE_UI = ("{n} min Rechenzeit je Aufnahmestunde", "Einlesen", "Erkennung", "Ausrichtung", "Sprechertrennung",
            "Auswertung", "Kontext {n}", "Einlesen {n} Token/s", "Schreiben {n} Token/s", "ganz auf der Grafikkarte",
            "teils im Arbeitsspeicher", "kleiner gewählt, damit es auf die Grafikkarte passt", "Messwerte")


def _zahl(v) -> bool:
    return isinstance(v, (int, float)) and not isinstance(v, bool) and math.isfinite(v) and 0 <= v < 1e7


def bereinigen(m) -> dict:
    if not isinstance(m, dict):
        return {}
    aus: dict = {k: round(float(m[k]), 1) for k in ZAHLEN if _zahl(m.get(k))}
    aus |= {k: str(m[k]).strip()[:80] for k in TEXTE if isinstance(m.get(k), str) and m[k].strip()}
    aus |= {k: m[k] for k in WAHRHEITEN if isinstance(m.get(k), bool)}
    if isinstance(m.get("stufen"), dict):
        s = {k: round(float(m["stufen"][k]), 1) for k in STUFEN if _zahl(m["stufen"].get(k))}
        if s:
            aus["stufen"] = s
    return aus


def speichern(job, m) -> None:
    werte = bereinigen(m)
    job.messwerte = json.dumps(werte, ensure_ascii=False) if werte else None


def lesen(job) -> dict:
    try:
        return bereinigen(json.loads(job.messwerte or "{}"))
    except ValueError:
        return {}


def _de(x: float, stellen: int = 1) -> str:
    return f"{x:.{stellen}f}".replace(".", ",")


def zeilen(m: dict, _=lambda s, **k: s.format(**k)) -> list[str]:
    """Für die Warteschlange der Verwaltung; _ übersetzt (tr der Verwaltung)."""
    if not m:
        return []
    z = []
    if m.get("art") == "abschrift":
        if "minutenJeStunde" in m:
            z.append(_("{n} min Rechenzeit je Aufnahmestunde", n=_de(m["minutenJeStunde"])))
        namen = {"einlesen": _("Einlesen"), "erkennung": _("Erkennung"), "ausrichtung": _("Ausrichtung"),
                 "sprechertrennung": _("Sprechertrennung"), "auswertung": _("Auswertung")}
        teile = [f"{namen[k]} {_de(v / 60)} min" for k, v in (m.get("stufen") or {}).items()]
        if teile:
            z.append(" · ".join(teile))
    elif m.get("modell"):
        z.append(m["modell"] + (f" ({m['quantisierung']})" if m.get("quantisierung") else "")
                 + (", " + _("Kontext {n}", n=int(m["kontext"])) if m.get("kontext") else ""))
        tempo = []
        if "einlesenTokS" in m:
            tempo.append(_("Einlesen {n} Token/s", n=_de(m["einlesenTokS"], 0)))
        if "schreibenTokS" in m:
            tempo.append(_("Schreiben {n} Token/s", n=_de(m["schreibenTokS"])))
        if tempo:
            z.append(" · ".join(tempo))
        if "ganzAufKarte" in m:
            z.append(_("ganz auf der Grafikkarte") if m["ganzAufKarte"] else _("teils im Arbeitsspeicher"))
        if m.get("abgestuft"):
            z.append(_("kleiner gewählt, damit es auf die Grafikkarte passt"))
    if m.get("karte"):
        z.append(m["karte"] + (f" ({_de(m['kartenspeicherMb'] / 1024)} GB)" if m.get("kartenspeicherMb") else ""))
    return z
