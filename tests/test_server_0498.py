"""Server 0.4.98: Einzelplatz – Host-Kopfzeile, Schnittstellen-Doku, Adressen außerhalb des Ereignis-Takts,
Schlüsseldatei, exklusiver Anschluss."""
import asyncio
import os
import socket
import sys
import threading

import pytest

from tests.test_server_0495 import HEIMNETZ, _von, ep  # noqa: F401 – Fixture


def _pforte(kopfzeilen: list[tuple[bytes, bytes]], pfad: str = "/api/v1/health", client=("127.0.0.1", 5000)) -> int:
    from app.einzelplatz import Pforte

    async def app(scope, receive, send):
        await send({"type": "http.response.start", "status": 200, "headers": []})
        await send({"type": "http.response.body", "body": b""})

    gesendet = []

    async def senden(nachricht):
        gesendet.append(nachricht)

    async def empfangen():
        return {"type": "http.request", "body": b""}

    scope = {"type": "http", "path": pfad, "headers": kopfzeilen, "client": client, "method": "GET"}
    asyncio.run(Pforte(app)(scope, empfangen, senden))
    return gesendet[0]["status"]


def test_genau_eine_host_kopfzeile(ep):  # noqa: F811
    assert _pforte([(b"host", b"localhost:8000")]) == 200
    assert _pforte([(b"host", b"localhost:8000"), (b"host", b"boese.example:8000")]) == 400
    assert _pforte([(b"host", b"localhost:8000"), (b"Host", b"localhost:8000")]) == 400
    assert _pforte([]) == 400


def test_keine_schnittstellen_doku_im_einzelplatz(ep):  # noqa: F811
    for pfad in ("/api/v1/docs", "/api/v1/openapi.json", "/api/v1/docs/oauth2-redirect"):
        assert ep.get(pfad).status_code == 404, pfad  # auch vom PC selbst
        assert _von(ep, "192.168.178.50", "GET", pfad).status_code == 404, pfad
    assert ep.get("/api/v1/health").status_code == 200


def test_adressen_werden_nicht_im_ereignis_takt_ermittelt(ep, monkeypatch):  # noqa: F811
    from app import einzelplatz

    faeden = []

    def ermitteln():
        faeden.append(threading.current_thread() is threading.main_thread())
        return frozenset({HEIMNETZ}), HEIMNETZ
    monkeypatch.setattr(einzelplatz, "_ermitteln", ermitteln)
    einzelplatz.zuruecksetzen()

    assert asyncio.run(einzelplatz._host_pruefen(f"{HEIMNETZ}:8000"))
    assert faeden == [False]  # nachgesehen – aber nicht im Faden des Ereignis-Takts (hier der Hauptfaden)
    assert asyncio.run(einzelplatz._host_pruefen("192.168.178.99:8000")) is False  # unbekannt, gerade nachgesehen
    assert asyncio.run(einzelplatz._host_pruefen("boese.example:8000")) is False


def test_schluesseldatei_frisch_und_nur_fuer_den_nutzer(ep, tmp_path):  # noqa: F811
    from app.db import session_factory
    from app.verwaltung.lokaler_worker import schluessel_datei_schreiben

    datei = tmp_path / "programm" / "worker2.key"
    datei.parent.mkdir(parents=True, exist_ok=True)
    fremd = tmp_path / "fremd.txt"
    fremd.write_text("unberührt", encoding="utf-8")
    if sys.platform != "win32":
        (datei.with_suffix(".neu")).symlink_to(fremd)  # ein Verweis an der Stelle der Zwischendatei
    with session_factory()() as db:
        schluessel_datei_schreiben(db, datei)
    assert fremd.read_text(encoding="utf-8") == "unberührt"
    assert datei.read_text(encoding="utf-8").startswith("wk.") and not datei.with_suffix(".neu").exists()
    if sys.platform != "win32":
        assert (os.stat(datei).st_mode & 0o777) == 0o600


def test_anschluss_exklusiv_unter_windows(monkeypatch):
    from app import cli

    s = cli.einzelplatz_anschluss(0)  # Linux: wie uvicorn
    try:
        assert s.getsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR)
    finally:
        s.close()

    gesetzt = []

    class Steckdose:
        def __init__(self, *a):
            pass

        def setsockopt(self, ebene, name, wert):
            gesetzt.append(name)

        def bind(self, adresse):
            gesetzt.append(adresse)

        def close(self):
            pass
    monkeypatch.setattr(socket, "SO_EXCLUSIVEADDRUSE", -5, raising=False)
    monkeypatch.setattr(socket, "socket", Steckdose)
    cli.einzelplatz_anschluss(8000)
    assert gesetzt == [-5, ("0.0.0.0", 8000)]  # exklusiv, kein SO_REUSEADDR


def test_belegter_anschluss_meldet_sich_verstaendlich():
    from app import cli

    erster = cli.einzelplatz_anschluss(0)
    erster.listen()
    try:
        with pytest.raises(OSError) as fehler:
            cli.einzelplatz_anschluss(erster.getsockname()[1])
        assert "in use" in str(fehler.value).lower() or getattr(fehler.value, "winerror", 0) == 10048
    finally:
        erster.close()
