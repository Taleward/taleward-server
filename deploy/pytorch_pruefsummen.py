"""Prüfsummen der PyTorch-Dateien für eine Server-Fassung festhalten (läuft im Ablauf „Fassung markieren“).

Die Worker-App installiert torch, torchaudio und torchvision in der Variante für die Grafikkarte (bzw. „nur
Prozessor“) vom PyTorch-Verzeichnis. Für diese Dateien stehen keine Prüfsummen in uv.lock. Dieses Skript liest die
Versionen aus uv.lock, holt aus dem Verzeichnis die SHA-256 aller passenden Dateien (Python 3.11, Windows und Linux)
und schreibt sie in `pytorch-pruefsummen.txt`. Die Datei hängt am Release der Fassung und wird mit der Freigabe
unterschrieben; die Worker-App installiert PyTorch dann nur noch mit genau diesen Prüfsummen.

Aufbau, eine Zeile je Variante:  <variante> <paket>==<fassung> --hash=sha256:<hex> [--hash=sha256:<hex> …]
Nur Standardbibliothek, ohne Abhängigkeiten.

    python3 deploy/pytorch_pruefsummen.py uv.lock pytorch-pruefsummen.txt
"""
from __future__ import annotations

import re
import sys
import tomllib
import urllib.parse
import urllib.request
from pathlib import Path

VERZEICHNIS = "https://download.pytorch.org/whl"
VARIANTEN = ("cu128", "cpu")  # wie TORCH_BACKEND in der Worker-App (taleward_worker/motor.py) plus „nur Prozessor“
PAKETE = ("torch", "torchaudio", "torchvision")
PYTHON = "cp311"
# Ohne diese Systeme ist die Liste unvollständig: Name → erkennt den Plattform-Teil des Dateinamens
PFLICHT = {
    "Windows x86_64": lambda plattform: plattform == "win_amd64",
    "Linux x86_64": lambda plattform: "linux" in plattform and plattform.endswith("x86_64"),
}
MAX_SEITE = 4 * 1024 * 1024
_HREF = re.compile(r'href="([^"]+)"')
_SHA = re.compile(r"^[0-9a-f]{64}$")


class Fehler(Exception):
    pass


def fassungen(lock: str) -> dict[str, str]:
    """Versionen von torch, torchaudio und torchvision aus uv.lock."""
    daten = tomllib.loads(lock)
    gefunden: dict[str, str] = {}
    for p in daten.get("package", []):
        name = str(p.get("name", ""))
        if name in PAKETE:
            if name in gefunden and gefunden[name] != p.get("version"):
                raise Fehler(f"{name} steht in mehreren Fassungen in uv.lock")
            gefunden[name] = str(p.get("version"))
    fehlt = [n for n in PAKETE if n not in gefunden]
    if fehlt:
        raise Fehler("nicht in uv.lock: " + ", ".join(fehlt))
    return gefunden


class _NurGleicherHost(urllib.request.HTTPRedirectHandler):
    """Umleitungen nur auf https und denselben Host – sonst Abbruch."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        alt, neu = urllib.parse.urlsplit(req.full_url), urllib.parse.urlsplit(newurl)
        if neu.scheme != "https" or (neu.hostname or "") != (alt.hostname or ""):
            raise Fehler(f"Umleitung nach {newurl[:120]} nicht erlaubt")
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_OEFFNER = urllib.request.build_opener(_NurGleicherHost)


def seite_laden(adresse: str) -> str:
    if urllib.parse.urlsplit(adresse).scheme != "https":
        raise Fehler(f"nur https: {adresse}")
    anfrage = urllib.request.Request(adresse, headers={"User-Agent": "Taleward-Freigabe"})
    with _OEFFNER.open(anfrage, timeout=60) as antwort:
        roh = antwort.read(MAX_SEITE + 1)
    if len(roh) > MAX_SEITE:
        raise Fehler(f"Seite zu groß: {adresse}")
    return roh.decode("utf-8", errors="replace")


def dateien(html: str, paket: str, fassung: str) -> dict[str, list[tuple[str, str, str]]]:
    """Aus einer Seite des Verzeichnisses: volle Fassung (mit lokalem Teil, z. B. 2.8.0+cu128) → [(Datei, Plattform,
    SHA-256)], nur Räder für Python 3.11. Ein Rad für ein Pflicht-System ohne Prüfsumme ist ein Fehler."""
    muster = re.compile(rf"^{re.escape(paket)}-{re.escape(fassung)}(\+[A-Za-z0-9.]+)?-{PYTHON}-{PYTHON}-([A-Za-z0-9_.]+)\.whl$")
    ergebnis: dict[str, list[tuple[str, str, str]]] = {}
    for href in _HREF.findall(html):
        adresse, _, fragment = href.partition("#")
        name = urllib.parse.unquote(adresse.rsplit("/", 1)[-1])
        m = muster.match(name)
        if not m:
            continue
        plattform = m.group(2)
        if not fragment.startswith("sha256="):
            if any(passt(plattform) for passt in PFLICHT.values()):
                raise Fehler(f"Keine Prüfsumme im Verzeichnis: {name}")
            continue
        sha = fragment[len("sha256="):].lower()
        if not _SHA.match(sha):
            raise Fehler(f"Prüfsumme unlesbar: {name}")
        ergebnis.setdefault(fassung + (m.group(1) or ""), []).append((name, plattform, sha))
    return ergebnis


def erzeugen(lock: str, laden=None) -> str:
    laden = laden or seite_laden
    zeilen = ["# Prüfsummen der PyTorch-Dateien (aus uv.lock und " + VERZEICHNIS + "), Python 3.11"]
    for variante in VARIANTEN:
        for paket, fassung in fassungen(lock).items():
            gefunden = dateien(laden(f"{VERZEICHNIS}/{variante}/{paket}/"), paket, fassung)
            eigene = gefunden.get(f"{fassung}+{variante}", [])
            for system, passt in PFLICHT.items():
                if not any(passt(plattform) for _, plattform, _ in eigene):
                    raise Fehler(f"{paket} {fassung}+{variante}: keine Datei für {system}")
            for voll in sorted(gefunden):
                hashes = sorted({sha for _, _, sha in gefunden[voll]})
                zeilen.append(f"{variante} {paket}=={voll} " + " ".join(f"--hash=sha256:{h}" for h in hashes))
    return "\n".join(zeilen) + "\n"


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    try:
        text = erzeugen(Path(argv[1]).read_text(encoding="utf-8"))
    except (Fehler, OSError, ValueError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    Path(argv[2]).write_text(text, encoding="utf-8")
    print(text, end="")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
