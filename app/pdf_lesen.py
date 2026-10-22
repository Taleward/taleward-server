"""PDF in einem eigenen Prozess auslesen – mit Zeit- und Speichergrenze, damit eine kaputte oder präparierte Datei den
Server nicht festhält. Aufruf nur über `app.unterlagen` (`python -I pdf_lesen.py text|seiten MAX_SEITEN`, PDF auf
stdin). Ergebnis als JSON auf stdout; Rückgabe 0 = gelesen, 2 = Passwort, sonst unlesbar. Nur Standardbibliothek und
pypdf, damit der Prozess schnell startet."""
import json
import sys

SPEICHER = 2 * 1024 ** 3  # Adressraum des Prozesses
RECHENZEIT = 300  # Sekunden Rechenzeit, zusätzlich zur Wartezeit des Aufrufers


def _grenzen() -> None:
    try:
        import resource
    except ImportError:  # Windows: dort gilt nur die Wartezeit des Aufrufers
        return
    for art, wert in ((resource.RLIMIT_AS, SPEICHER), (resource.RLIMIT_CPU, RECHENZEIT)):
        try:
            resource.setrlimit(art, (wert, wert))
        except (ValueError, OSError):
            pass


def main(argv: list[str]) -> int:
    _grenzen()
    import io

    from pypdf import PdfReader

    modus, max_seiten = argv[0], int(argv[1])
    daten = sys.stdin.buffer.read()
    try:
        leser = PdfReader(io.BytesIO(daten))
        if leser.is_encrypted and not leser.decrypt(""):
            return 2
        seiten = leser.pages
        anzahl = len(seiten)
        if modus == "seiten":
            json.dump({"seiten": anzahl}, sys.stdout)
            return 0
        abschnitte, leer = [], 0
        for i, seite in enumerate(seiten):
            if i >= max_seiten:
                break
            try:
                text = (seite.extract_text() or "").strip()
            except Exception:  # einzelne kaputte Seite
                text = ""
            if len(text) < 3:  # nichts oder nur Seitenzahl – vermutlich eingescannt
                leer += 1
                continue
            abschnitte.append({"seite": i + 1, "text": text})
        json.dump({"seiten": anzahl, "leer": leer, "abschnitte": abschnitte}, sys.stdout)  # nur ASCII (Windows-Konsole)
        return 0
    except Exception:  # noqa: BLE001 – unlesbar, Einzelheiten bleiben im Prozess
        return 1


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
