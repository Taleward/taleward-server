"""PDF und DOCX in einem eigenen Prozess auslesen – mit Zeit- und Speichergrenze, damit eine kaputte oder präparierte
Datei den Server nicht festhält. Aufruf nur über `app.unterlagen` (`python -I pdf_lesen.py text|seiten MAX_SEITEN`
bzw. `docx MAX_XML`, Datei auf stdin). Ergebnis als JSON auf stdout; Rückgabe 0 = gelesen, 2 = Passwort, sonst unlesbar. Nur Standardbibliothek und
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


W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"
MAX_TEXT = 6_000_000  # Zeichen; mehr braucht der Server nie (er schneidet früher ab)


class _Abgelehnt(Exception):
    pass


def _nein(*_):
    raise _Abgelehnt  # DOCTYPE und Entitäten gehören in kein Word-Dokument


def docx(daten: bytes, max_xml: int) -> dict:
    """Text eines Word-Dokuments. Dokumente mit DOCTYPE oder eigenen Entitäten werden abgelehnt."""
    import io
    import zipfile
    from xml.etree import ElementTree

    with zipfile.ZipFile(io.BytesIO(daten)) as z:
        info = z.getinfo("word/document.xml")
        if info.file_size > max_xml:
            raise _Abgelehnt
        with z.open(info) as f:
            roh = f.read(max_xml + 1)
    if len(roh) > max_xml:
        raise _Abgelehnt
    from xml.parsers import expat

    def name(n: str) -> str:  # „namensraum}element“ → „{namensraum}element“, wie ElementTree
        return "{" + n if "}" in n else n

    baum = ElementTree.TreeBuilder()
    leser = expat.ParserCreate(namespace_separator="}")
    leser.StartDoctypeDeclHandler = leser.EntityDeclHandler = leser.UnparsedEntityDeclHandler = _nein
    leser.StartElementHandler = lambda n, a: baum.start(name(n), {name(k): v for k, v in a.items()})
    leser.EndElementHandler = lambda n: baum.end(name(n))
    leser.CharacterDataHandler = baum.data
    leser.Parse(roh, True)
    wurzel = baum.close()
    seite, abschnitte, aktuell, umbrueche, zeichen = 1, [], [], False, 0
    for p in wurzel.iter(f"{W}p"):
        teile = []
        for el in p.iter():
            if el.tag == f"{W}t" and el.text:
                teile.append(el.text)
            elif el.tag == f"{W}tab":
                teile.append("\t")
            elif (el.tag == f"{W}br" and el.get(f"{W}type") == "page") or el.tag == f"{W}lastRenderedPageBreak":
                if aktuell:
                    abschnitte.append({"seite": seite, "text": "\n".join(aktuell)})
                    aktuell = []
                seite += 1
                umbrueche = True
        zeile = "".join(teile).strip()
        if zeile:
            aktuell.append(zeile)
            zeichen += len(zeile)
            if zeichen > MAX_TEXT:
                break
    if aktuell:
        abschnitte.append({"seite": seite, "text": "\n".join(aktuell)})
    if not umbrueche:  # keine Seiteninformation im Dokument
        for a in abschnitte:
            a["seite"] = None
    return {"seiten": seite if umbrueche else None, "abschnitte": abschnitte}


def main(argv: list[str]) -> int:
    _grenzen()
    modus, max_seiten = argv[0], int(argv[1])
    daten = sys.stdin.buffer.read()
    if modus == "docx":
        try:
            json.dump(docx(daten, max_seiten), sys.stdout)
            return 0
        except BaseException:  # noqa: BLE001 – unlesbar oder abgelehnt, Einzelheiten bleiben im Prozess
            return 1
    import io

    from pypdf import PdfReader

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
