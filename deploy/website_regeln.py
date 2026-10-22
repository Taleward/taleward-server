#!/usr/bin/env python3
"""Prüfung der Caddy-Regeln einer Website, die neben dem Server über denselben Caddy läuft (docs/BETRIEB.md,
„Website mitliefern“). Aufgerufen von website-neu-laden.sh (als root) und website-zugang.sh; nur Standardbibliothek.

Geprüft wird nicht der Text, sondern das, was Caddy daraus macht (JSON aus `caddy adapt`): nur eine Positivliste von
Bausteinen, jede Adresse aus der eingetragenen Liste der Website (nie die Server-Domain), Dateien nur aus /srv/website,
Platzhalter nur aus einer kleinen Liste. Was nicht ausdrücklich erlaubt ist, wird abgelehnt.

  website_regeln.py adressen SERVERDOMAIN DOMAIN…        – Liste der Website-Adressen prüfen
  website_regeln.py vorab DATEI                          – Regeldatei grob prüfen (vor `caddy adapt`)
  website_regeln.py json JSONDATEI SERVERDOMAIN DOMAIN… – übersetzte Regeln prüfen
Rückgabe 0 = in Ordnung, 1 = abgelehnt (Gründe auf stderr).
"""
from __future__ import annotations

import json
import re
import sys

WURZEL = "/srv/website"
MAX_BYTES = 262144
ADRESSE = re.compile(r"^(?=.{1,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$")

# Erlaubte Bausteine (Caddy-Handler) und ihre Felder
HANDLER = {
    "subroute": {"routes"},
    "headers": {"request", "response"},
    "static_response": {"status_code", "headers", "body", "close"},
    "rewrite": {"uri", "strip_path_prefix", "strip_path_suffix"},
    "file_server": {"hide", "index_names", "canonical_uris", "pass_thru"},
    "encode": {"encodings", "prefer", "minimum_length"},
    "vars": {"root"},
    "error": {"error", "status_code"},
}
MATCHER = {"host", "path", "not", "method", "header", "file"}
DATEI_MATCHER = {"root", "try_files", "try_policy"}
ROUTE = {"match", "handle", "terminal", "group"}
SERVER = {"listen", "routes", "errors"}
PORTS = {":443", ":80"}
PLATZHALTER = {"http.request.uri", "http.request.uri.path", "http.request.uri.query", "http.error.status_code",
               "http.error.status_text", "http.matchers.file.relative", "http.matchers.file.remainder"}
KLAMMER = re.compile(r"\{([^{}]*)\}")


class Abgelehnt(Exception):
    pass


def adressen(server: str, liste: list[str]) -> list[str]:
    """Adressen der Website: einfache Hostnamen (klein, ohne Port, Pfad, Schema, Platzhalter), nie die des Servers."""
    server = server.strip().lower().rstrip(".")
    aus = []
    for a in liste:
        a = a.strip().lower().rstrip(".")
        if not ADRESSE.match(a):
            raise Abgelehnt(f"„{a}“ ist keine einfache Adresse (nur Name, ohne http://, Port, Pfad oder *).")
        if a == server:
            raise Abgelehnt(f"„{a}“ ist die Adresse des Servers selbst.")
        aus.append(a)
    if not aus:
        raise Abgelehnt("Keine Adresse für die Website angegeben.")
    return aus


def vorab(text: str) -> None:
    """Grobe Prüfung am Text: `import` nur für Snippets, die in derselben Datei stehen (keine Dateien, keine Snippets
    anderer Dateien), keine Umgebungs- oder Datei-Platzhalter (die setzt Caddy schon beim Übersetzen ein, danach wären sie
    nicht mehr zu erkennen)."""
    if len(text.encode()) > MAX_BYTES:
        raise Abgelehnt("Regeldatei zu groß.")
    snippets = set(re.findall(r"^\s*\(([A-Za-z0-9_-]+)\)\s*\{", text, re.M))
    for nr, zeile in enumerate(text.splitlines(), 1):
        z = zeile.strip()
        if not z or z.startswith("#"):
            continue
        if re.match(r"import(\s|$)", z):
            m = re.fullmatch(r"import\s+([A-Za-z0-9_-]+)", z)
            if not m or m.group(1) not in snippets:
                raise Abgelehnt(f"Zeile {nr}: import nur für Snippets aus derselben Datei.")
        if re.search(r"\{\$|\{(env|file|system|vars)\.", z):
            raise Abgelehnt(f"Zeile {nr}: Platzhalter für Umgebung, Dateien oder Variablen sind nicht erlaubt.")
        if "\x00" in z:
            raise Abgelehnt(f"Zeile {nr}: Steuerzeichen.")


def _pfad(wert, wo: str) -> None:
    if not isinstance(wert, str) or not (wert == WURZEL or wert.startswith(WURZEL + "/")) or ".." in wert \
            or "{" in wert or "//" in wert or any(c.isspace() for c in wert):
        raise Abgelehnt(f"{wo}: Dateien nur aus {WURZEL} (gefunden: {wert!r}).")


def _texte(x, wo: str) -> None:
    """Jeder Platzhalter in einem Wert muss aus der Liste stammen."""
    if isinstance(x, str):
        for p in KLAMMER.findall(x):
            if p not in PLATZHALTER:
                raise Abgelehnt(f"{wo}: Platzhalter {{{p}}} ist nicht erlaubt.")
        if "{" in KLAMMER.sub("", x) or "}" in KLAMMER.sub("", x):
            raise Abgelehnt(f"{wo}: einzelne geschweifte Klammer.")
    elif isinstance(x, dict):
        for k, v in x.items():
            _texte(k, wo)
            _texte(v, wo)
    elif isinstance(x, list):
        for v in x:
            _texte(v, wo)
    elif not isinstance(x, (int, float, bool)) and x is not None:
        raise Abgelehnt(f"{wo}: unerwarteter Wert.")


def _matcher(m, erlaubt: set[str], wo: str, oben: bool) -> None:
    if not isinstance(m, dict) or not m:
        raise Abgelehnt(f"{wo}: Bedingung nicht lesbar.")
    for k, v in m.items():
        if k not in MATCHER:
            raise Abgelehnt(f"{wo}: Bedingung „{k}“ ist nicht erlaubt.")
        if k == "host":
            if not isinstance(v, list) or not v or any(not isinstance(h, str) or h.lower() not in erlaubt for h in v):
                raise Abgelehnt(f"{wo}: Adresse außerhalb der Liste der Website ({v}).")
        elif k == "not":
            if not isinstance(v, list):
                raise Abgelehnt(f"{wo}: „not“ nicht lesbar.")
            for n in v:
                _matcher(n, erlaubt, wo, False)
        elif k == "file":
            if not isinstance(v, dict) or set(v) - DATEI_MATCHER:
                raise Abgelehnt(f"{wo}: Datei-Bedingung mit unbekannten Angaben.")
            if "root" in v:
                _pfad(v["root"], wo)
            _texte(v, wo)
        else:
            _texte(v, wo)
    if oben and "host" not in m:
        raise Abgelehnt(f"{wo}: jede Regel braucht eine Adresse der Website.")


def _handler(h, erlaubt: set[str], wo: str) -> None:
    if not isinstance(h, dict) or h.get("handler") not in HANDLER:
        raise Abgelehnt(f"{wo}: Baustein „{h.get('handler') if isinstance(h, dict) else h}“ ist nicht erlaubt.")
    art = h["handler"]
    fremd = set(h) - HANDLER[art] - {"handler"}
    if fremd:
        raise Abgelehnt(f"{wo}: {art} mit nicht erlaubten Angaben {sorted(fremd)}.")
    if art == "subroute":
        _routen(h.get("routes", []), erlaubt, wo, oben=False)
    elif art == "vars":
        _pfad(h["root"], wo)
    else:
        _texte({k: v for k, v in h.items() if k != "handler"}, wo)


def _routen(routen, erlaubt: set[str], wo: str, oben: bool) -> None:
    if not isinstance(routen, list):
        raise Abgelehnt(f"{wo}: Regeln nicht lesbar.")
    for i, r in enumerate(routen):
        stelle = f"{wo}[{i}]"
        if not isinstance(r, dict) or set(r) - ROUTE:
            raise Abgelehnt(f"{stelle}: Regel mit nicht erlaubten Angaben.")
        treffer = r.get("match")
        if oben and not treffer:
            raise Abgelehnt(f"{stelle}: jede Regel braucht eine Adresse der Website.")
        for m in treffer or []:
            _matcher(m, erlaubt, stelle, oben)
        for h in r.get("handle", []):
            _handler(h, erlaubt, stelle)
        _texte(r.get("group", ""), stelle)


def json_pruefen(daten: dict, server: str, liste: list[str]) -> None:
    erlaubt = set(adressen(server, liste))
    if not isinstance(daten, dict) or set(daten) != {"apps"} or set(daten["apps"]) != {"http"} \
            or set(daten["apps"]["http"]) != {"servers"}:
        raise Abgelehnt("Nur Regeln für Seiten erlaubt (keine globalen Einstellungen, Zertifikate, Protokolle).")
    for name, s in daten["apps"]["http"]["servers"].items():
        if not isinstance(s, dict) or set(s) - SERVER:
            raise Abgelehnt(f"{name}: nicht erlaubte Server-Angaben {sorted(set(s) - SERVER)}.")
        if any(p not in PORTS for p in s.get("listen", [])):
            raise Abgelehnt(f"{name}: nur die Ports 443 und 80.")
        _routen(s.get("routes", []), erlaubt, name, oben=True)
        fehler = s.get("errors", {})
        if not isinstance(fehler, dict) or set(fehler) - {"routes"}:
            raise Abgelehnt(f"{name}: Fehlerseiten nicht lesbar.")
        _routen(fehler.get("routes", []), erlaubt, name + ".errors", oben=True)


def main(argv: list[str]) -> int:
    try:
        if len(argv) >= 2 and argv[0] == "adressen":
            print(" ".join(adressen(argv[1], argv[2:])))
        elif len(argv) == 2 and argv[0] == "vorab":
            with open(argv[1], encoding="utf-8") as f:
                vorab(f.read())
        elif len(argv) >= 3 and argv[0] == "json":
            with open(argv[1], encoding="utf-8") as f:
                json_pruefen(json.load(f), argv[2], argv[3:])
        else:
            print(__doc__, file=sys.stderr)
            return 2
    except Abgelehnt as e:
        print(f"Abgelehnt: {e}", file=sys.stderr)
        return 1
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        print(f"Abgelehnt: Regeln nicht lesbar ({e.__class__.__name__}).", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
