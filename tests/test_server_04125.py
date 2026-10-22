"""Server 0.4.125: PDFs werden in einem eigenen Prozess mit Zeit- und Speichergrenze ausgelesen; pypdf aktuell."""
import sys
import time
from importlib.metadata import version

import pytest

from app import errors, unterlagen
from tests.dateien import pdf


def test_pypdf_aktuell():
    assert tuple(int(x) for x in version("pypdf").split(".")[:3]) >= (6, 21, 1)


def test_pdf_wird_weiter_gelesen():
    x = unterlagen.auslesen("pdf", pdf(["Der Wirt heisst Borin.", "", "Die Burg Rabenfels."]))
    assert x.seiten == 3 and x.leere_seiten == 1
    assert [(a["seite"], a["text"]) for a in x.abschnitte] == [(1, "Der Wirt heisst Borin."), (3, "Die Burg Rabenfels.")]


def test_abgeschnittene_pdf_schnell_abgelehnt():
    """Datei endet mitten in der xref-Tabelle → unsupported_document, ohne den Server festzuhalten."""
    import io

    from pypdf import PdfWriter

    schreiber = PdfWriter()
    for _ in range(3):
        schreiber.add_blank_page(200, 200)
    puffer = io.BytesIO()
    schreiber.write(puffer)
    daten = puffer.getvalue()
    stelle = daten.index(b"\nxref\n") + 1
    assert daten[stelle:stelle + 30].startswith(b"xref\n0 ")  # echte xref-Tabelle
    kaputt = daten[: stelle + len(b"xref\n0 7\n0000000000 65535 f \n00000")]
    beginn = time.monotonic()
    with pytest.raises(errors.ApiError) as e:
        unterlagen.auslesen("pdf", kaputt)
    assert e.value.code == "unsupported_document"
    assert time.monotonic() - beginn < 5


def test_haengender_leser_wird_abgebrochen(tmp_path, monkeypatch):
    """Was immer im Leseprozess hängt: nach der Wartezeit ist Schluss, der Server antwortet."""
    leser = tmp_path / "haengt.py"
    leser.write_text("import time\ntime.sleep(60)\n", encoding="utf-8")
    monkeypatch.setattr(unterlagen, "PDF_LESER", leser)
    monkeypatch.setattr(unterlagen, "PDF_ZEIT", 1)
    beginn = time.monotonic()
    with pytest.raises(errors.ApiError) as e:
        unterlagen.auslesen("pdf", pdf(["Text"]))
    assert e.value.code == "unsupported_document" and time.monotonic() - beginn < 5


@pytest.mark.skipif(sys.platform == "win32", reason="Speichergrenze nur unter Linux/macOS")
def test_leseprozess_hat_eine_speichergrenze():
    import subprocess

    probe = ("import runpy,resource,sys;sys.argv=['x','seiten','1'];"
             f"m=runpy.run_path({str(unterlagen.PDF_LESER)!r},run_name='m');m['_grenzen']();"
             "print(resource.getrlimit(resource.RLIMIT_AS)[0])")
    r = subprocess.run([sys.executable, "-I", "-c", probe], capture_output=True, text=True, timeout=30)
    assert int(r.stdout.strip()) == 2 * 1024 ** 3
