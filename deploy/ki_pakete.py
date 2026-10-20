"""Paketlisten mit Prüfsummen für den Worker-Container (Dockerfile.worker), wie in der Worker-App.

Aus der Paketliste von uv.lock (uv export mit Prüfsummen, Extra „ki“) entstehen drei Dateien im Zielordner:
  geprueft.txt     alle Pakete außer torch, torchaudio, torchvision – jede Zeile mit Prüfsumme aus uv.lock
                   (bei „cpu“ ohne die reinen Grafikkarten-Pakete)
  pytorch.txt      torch, torchaudio, torchvision in der Variante (cu128 oder cpu) mit den Prüfsummen aus
                   deploy/pytorch-pruefsummen.txt (für diese Dateien stehen keine Prüfsummen in uv.lock)
  bauwerkzeug.txt  das Bauwerkzeug für den Taleward-Code, soweit es nicht schon in geprueft.txt steht
Fehlt irgendwo eine Prüfsumme, bricht das Skript ab – dann wird nichts installiert.
Nur Standardbibliothek, ohne Abhängigkeiten.

    python3 deploy/ki_pakete.py <export.txt> <pytorch-pruefsummen.txt> <bauwerkzeug.txt> <cu128|cpu> <zielordner>
"""
from __future__ import annotations

import re
import sys
from pathlib import Path

PYTORCH_VERZEICHNIS = ("torch", "torchaudio", "torchvision")
NUR_CUDA = ("nvidia-", "triton", "cuda-")  # nur für die Grafikkarte nötig, bei „cpu“ weglassen
VARIANTEN = ("cu128", "cpu")
_PYTORCH_ZEILE = re.compile(r"^([a-z0-9]+) ([a-z0-9_.-]+)==([A-Za-z0-9.+!]+)((?: --hash=sha256:[0-9a-f]{64})+)$")
_HASH = re.compile(r"--hash=sha256:[0-9a-f]{64}")


class Fehler(Exception):
    pass


def eintraege(text: str) -> list[tuple[str, str]]:
    """requirements.txt mit Fortsetzungszeilen → [(Paketname, ganzer Eintrag)]."""
    liste: list[tuple[str, str]] = []
    for zeile in text.splitlines():
        if not zeile.strip() or zeile.lstrip().startswith("#"):
            continue
        if zeile[:1].isspace() and liste:
            name, block = liste[-1]
            liste[-1] = (name, block + "\n" + zeile)
        else:
            name = re.split(r"[=<>!~ ;\[]", zeile.strip(), maxsplit=1)[0].lower().replace("_", "-")
            liste.append((name, zeile))
    return liste


def ohne_pruefsumme(text: str) -> list[str]:
    """Namen aller Einträge ohne Prüfsumme (leer = alles in Ordnung)."""
    return [name for name, block in eintraege(text) if not _HASH.search(block)]


def pytorch_liste_lesen(text: str) -> dict[tuple[str, str, str], str]:
    liste: dict[tuple[str, str, str], str] = {}
    for zeile in text.splitlines():
        if not zeile.strip() or zeile.startswith("#"):
            continue
        m = _PYTORCH_ZEILE.match(zeile.strip())
        if not m:
            raise Fehler("Liste der PyTorch-Prüfsummen ist nicht lesbar.")
        liste[(m.group(1), m.group(2), m.group(3))] = m.group(4).strip()
    return liste


def aufteilen(export: str, liste: dict[tuple[str, str, str], str], variante: str) -> tuple[str, str]:
    """→ (geprueft.txt, pytorch.txt) für die Variante."""
    if variante not in VARIANTEN:
        raise Fehler(f"Unbekannte Variante {variante!r} (erlaubt: {', '.join(VARIANTEN)}).")
    geprueft, pytorch = [], []
    for name, block in eintraege(export):
        if name in PYTORCH_VERZEICHNIS:
            anforderung, _, bedingung = block.split("\\")[0].strip().partition(";")
            paket, _, fassung = anforderung.strip().partition("==")
            if not fassung:
                raise Fehler(f"{name} steht ohne feste Fassung in der Paketliste.")
            voll = f"{fassung.strip()}+{variante}"
            hashes = liste.get((variante, paket.strip().lower(), voll))
            if not hashes:
                raise Fehler(f"Keine Prüfsumme für {paket.strip()} {voll} in der Liste der PyTorch-Prüfsummen.")
            pytorch.append(f"{paket.strip()}=={voll}" + (f" ; {bedingung.strip()}" if bedingung.strip() else "")
                           + f" {hashes}")
        elif variante == "cpu" and name.startswith(NUR_CUDA):
            continue
        else:
            geprueft.append(block)
    text_geprueft = "\n".join(geprueft) + "\n"
    fehlt = ohne_pruefsumme(text_geprueft)
    if fehlt:
        raise Fehler("Ohne Prüfsumme in uv.lock: " + ", ".join(fehlt))
    if len(pytorch) != len(PYTORCH_VERZEICHNIS):
        raise Fehler("torch, torchaudio und torchvision müssen genau einmal in der Paketliste stehen.")
    return text_geprueft, "\n".join(pytorch) + "\n"


def bauwerkzeug(text: str, geprueft: str) -> str:
    """Bauwerkzeug ohne die Pakete, die schon aus uv.lock kommen (die behalten deren Fassung)."""
    vorhanden = {name for name, _ in eintraege(geprueft)}
    bloecke = [block for name, block in eintraege(text) if name not in vorhanden]
    ergebnis = "\n".join(bloecke) + ("\n" if bloecke else "")
    fehlt = ohne_pruefsumme(ergebnis)
    if fehlt:
        raise Fehler("Bauwerkzeug ohne Prüfsumme: " + ", ".join(fehlt))
    return ergebnis


def main(argv: list[str]) -> int:
    if len(argv) != 6:
        print(__doc__.strip().splitlines()[-1].strip(), file=sys.stderr)
        return 2
    export, liste, werkzeug, variante, ziel = argv[1:]
    try:
        geprueft, pytorch = aufteilen(Path(export).read_text(encoding="utf-8"),
                                      pytorch_liste_lesen(Path(liste).read_text(encoding="utf-8")), variante)
        bau = bauwerkzeug(Path(werkzeug).read_text(encoding="utf-8"), geprueft)
    except (Fehler, OSError) as e:
        print(f"Fehler: {e}", file=sys.stderr)
        return 1
    ordner = Path(ziel)
    ordner.mkdir(parents=True, exist_ok=True)
    (ordner / "geprueft.txt").write_text(geprueft, encoding="utf-8")
    (ordner / "pytorch.txt").write_text(pytorch, encoding="utf-8")
    (ordner / "bauwerkzeug.txt").write_text(bau, encoding="utf-8")
    print(f"{len(eintraege(geprueft))} Pakete aus uv.lock, PyTorch ({variante}) und "
          f"{len(eintraege(bau))} Bauwerkzeug-Pakete – alle mit Prüfsumme.")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
