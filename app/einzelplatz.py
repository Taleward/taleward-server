"""Betriebsart „Einzelplatz“: Server, Worker und App auf einem PC im Heimnetz (Windows-Plan, Schritt 1).

Eingeschaltet mit TALEWARD_EINZELPLATZ=1 – das setzt das Programm, das den Server startet. Dann gilt:

- Nur Anfragen, deren Host-Kopfzeile localhost, 127.0.0.1, [::1] oder eine eigene Heimnetz-Adresse dieses PCs samt
  Port nennt. Sonst 400. So kann eine fremde Webseite im Browser des PCs den Server nicht über einen eigenen
  Domainnamen ansprechen.
- Absender außerhalb privater Adressbereiche bekommen 404 (auch wenn die Netzwerk-Freigabe falsch steht).
- Aus dem Heimnetz gibt es nur die App-Schnittstelle, Einladungen, die Web-App, Downloads und die öffentlichen Seiten.
  Verwaltung, Worker-Schnittstelle und Startweg gibt es nur vom PC selbst (echte Absenderadresse der Verbindung;
  Proxy-Kopfzeilen wertet der Server in dieser Betriebsart nicht aus). Sonst 404.
- Das erste Konto legt das Programm an: nur mit dem einmaligen Startschlüssel, den es beim Start über die Umgebung
  oder eine Datei übergibt. Einen Einrichtungscode gibt es nicht.
- Die öffentliche Adresse ist immer die aktuelle Heimnetz-Adresse des PCs (Einladungen, QR-Code, publicUrl).
"""
from __future__ import annotations

import hashlib
import hmac
import ipaddress
import logging
import os
import re
import socket
import threading
import time
import unicodedata
from pathlib import Path
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_db

log = logging.getLogger("einzelplatz")

SCHLUESSEL_ENV = "TALEWARD_STARTSCHLUESSEL"
SCHLUESSEL_DATEI_ENV = "TALEWARD_STARTSCHLUESSEL_DATEI"
SCHLUESSEL_MIN_BYTES = 32
SCHLUESSEL_FRIST_S = 15 * 60
LOKALE_NAMEN = ("localhost", "127.0.0.1", "::1")
# Aus dem Heimnetz erreichbar (alles andere nur vom PC selbst)
AUS_DEM_NETZ = ("/api/v1/", "/einladung/", "/app/", "/downloads/", "/datenschutz", "/konto/", "/konto-loeschen",
                "/passwort/", "/verwaltung/static/", "/verwaltung/sprache/", "/favicon.ico", "/apple-touch-icon")
AUS_DEM_NETZ_GENAU = ("/app", "/")
# Heimnetz im engen Sinn (RFC 1918, link-lokal, IPv6 ULA). Bewusst nicht ipaddress.is_private: das zählt auch
# Dokumentations- und Testnetze dazu.
HEIMNETZE = tuple(ipaddress.ip_network(n) for n in (
    "10.0.0.0/8", "172.16.0.0/12", "192.168.0.0/16", "169.254.0.0/16", "fc00::/7", "fe80::/10"))
ADRESSEN_TAKT_S = 30.0
ADRESSEN_NACHFRAGE_S = 5.0
# Beschreibung der Schnittstelle gibt es im Einzelplatz nicht (weder im Heimnetz noch auf dem PC)
OHNE_DOKU = ("/api/v1/docs", "/api/v1/docs/oauth2-redirect", "/api/v1/openapi.json")


def an() -> bool:
    return get_settings().taleward_einzelplatz


def port() -> int:
    return get_settings().einzelplatz_port


def programm_worker() -> bool:
    """Gehört der Worker zum Programm (Schlüsseldatei), statt dass der Server selbst einen startet?"""
    s = get_settings()
    return s.taleward_einzelplatz and s.einzelplatz_worker_datei is not None


def standard_datenordner() -> Path:
    """Datenordner im Benutzerordner: unter Windows %LOCALAPPDATA%\\Taleward\\Daten (nur für den Windows-Nutzer)."""
    basis = os.environ.get("LOCALAPPDATA")
    if basis:
        return Path(basis) / "Taleward" / "Daten"
    return Path.home() / ".local" / "share" / "taleward"


# ---------------------------------------------------------------- Adressen dieses PCs
_adressen: dict = {"zeit": -1e9, "werte": frozenset(), "haupt": None}
_adressen_lock = threading.Lock()


def _ip(text: str | None) -> ipaddress.IPv4Address | ipaddress.IPv6Address | None:
    try:
        ip = ipaddress.ip_address((text or "").split("%")[0])
    except ValueError:
        return None
    if ip.version == 6 and ip.ipv4_mapped is not None:
        return ip.ipv4_mapped
    return ip


def privat(text: str | None) -> bool:
    """Absender aus einem privaten Netz (Heimnetz), vom PC selbst oder link-lokal."""
    ip = _ip(text)
    return ip is not None and (ip.is_loopback or _heimnetz(ip))


def vom_pc(text: str | None) -> bool:
    ip = _ip(text)
    return ip is not None and ip.is_loopback


def _heimnetz(ip) -> bool:
    return ip is not None and any(ip.version == n.version and ip in n for n in HEIMNETZE)


def _ermitteln() -> tuple[frozenset[str], str | None]:
    """Eigene Heimnetz-Adressen. Die Hauptadresse ist die, über die der PC ins Netz geht (UDP-„Verbindung“ ohne
    gesendetes Paket); dazu alle Adressen zum eigenen Rechnernamen."""
    werte: set[str] = set()
    haupt = None
    try:
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.connect(("192.0.2.1", 9))  # Dokumentationsnetz, es geht nichts hinaus
            kandidat = _ip(s.getsockname()[0])
            if _heimnetz(kandidat):
                haupt = str(kandidat)
                werte.add(haupt)
    except OSError:
        pass
    try:
        for eintrag in socket.getaddrinfo(socket.gethostname(), None):
            ip = _ip(str(eintrag[4][0]))
            if _heimnetz(ip):
                werte.add(str(ip))
    except OSError:
        pass
    if haupt is None:
        haupt = next((w for w in sorted(werte) if ":" not in w), None)
    return frozenset(werte), haupt


def _faellig(erzwingen: bool = False) -> bool:
    return time.monotonic() - _adressen["zeit"] >= (ADRESSEN_NACHFRAGE_S if erzwingen else ADRESSEN_TAKT_S)


def _auffrischen(erzwingen: bool = False) -> None:
    jetzt = time.monotonic()
    with _adressen_lock:
        alter = jetzt - _adressen["zeit"]
        if alter < (ADRESSEN_NACHFRAGE_S if erzwingen else ADRESSEN_TAKT_S):
            return
        werte, haupt = _ermitteln()
        _adressen.update(zeit=jetzt, werte=werte, haupt=haupt)


def eigene_adressen() -> frozenset[str]:
    _auffrischen()
    return _adressen["werte"]


def heimnetz_adresse() -> str | None:
    _auffrischen()
    return _adressen["haupt"]


def oeffentliche_url() -> str:
    """Adresse, unter der Handys im WLAN diesen PC erreichen (ohne Heimnetz: localhost)."""
    ip = heimnetz_adresse()
    host = (f"[{ip}]" if ":" in ip else ip) if ip else "localhost"
    return f"http://{host}" + ("" if port() == 80 else f":{port()}")


def host_erlaubt(kopfzeile: str | None, nachsehen: bool = True) -> bool:
    """`nachsehen=False`: nur mit den bekannten Adressen vergleichen (die Pforte frischt sie außerhalb des
    Ereignis-Takts auf)."""
    if not kopfzeile:
        return False
    try:
        teile = urlsplit("//" + kopfzeile.strip())
        name, p = (teile.hostname or "").lower(), teile.port or 80
    except ValueError:
        return False
    if p != port() or not name or teile.username is not None or teile.path:
        return False
    if name in LOKALE_NAMEN:
        return True
    ip = _ip(name)
    if not _heimnetz(ip):
        return False
    if str(ip) in (eigene_adressen() if nachsehen else _adressen["werte"]):
        return True
    if nachsehen:
        _auffrischen(erzwingen=True)  # neue Adresse (WLAN gewechselt) – höchstens alle paar Sekunden nachsehen
    return str(ip) in _adressen["werte"]


async def _host_pruefen(kopfzeile: str | None) -> bool:
    """Wie host_erlaubt, aber das Nachsehen der eigenen Adressen (Namensauflösung, kann dauern) läuft in einem Faden."""
    import anyio

    if _faellig():
        await anyio.to_thread.run_sync(_auffrischen)
    if host_erlaubt(kopfzeile, nachsehen=False):
        return True
    if _faellig(erzwingen=True) and host_erlaubt_wenn_bekannt(kopfzeile):
        await anyio.to_thread.run_sync(_auffrischen, True)
        return host_erlaubt(kopfzeile, nachsehen=False)
    return False


def host_erlaubt_wenn_bekannt(kopfzeile: str | None) -> bool:
    """Wäre die Host-Kopfzeile erlaubt, wenn ihre Heimnetz-Adresse zu diesem PC gehört? (Lohnt das Nachsehen?)"""
    try:
        teile = urlsplit("//" + (kopfzeile or "").strip())
        return (teile.port or 80) == port() and teile.username is None and not teile.path and _heimnetz(
            _ip((teile.hostname or "").lower()))
    except ValueError:
        return False


def eigener_datenschutz(url: str | None) -> bool:
    """Ist das die Datenschutzseite dieses Servers (gespeichert mit der Adresse von damals)?"""
    try:
        teile = urlsplit(url or "")
        name = (teile.hostname or "").lower()
    except ValueError:
        return False
    return teile.path == "/datenschutz" and (name in LOKALE_NAMEN or _heimnetz(_ip(name)))


def pfad_aus_dem_netz(pfad: str) -> bool:
    return pfad in AUS_DEM_NETZ_GENAU or pfad.startswith(AUS_DEM_NETZ)


class Pforte:
    """ASGI-Middleware für den Einzelplatz-Betrieb (außen um alles andere gelegt)."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope["type"] not in ("http", "websocket"):
            return await self.app(scope, receive, send)
        client = scope.get("client")
        absender = client[0] if client else None
        roh = scope.get("headers") or []
        kopf = {k.decode("latin-1").lower(): v.decode("latin-1") for k, v in roh}
        pfad = scope.get("path") or "/"
        if not privat(absender):
            return await _antwort(scope, receive, send, 404)
        if sum(1 for k, _ in roh if k.lower() == b"host") != 1 or not await _host_pruefen(kopf.get("host")):
            return await _antwort(scope, receive, send, 400)  # auch bei zwei Host-Kopfzeilen
        if pfad in OHNE_DOKU:
            return await _antwort(scope, receive, send, 404)
        if not vom_pc(absender) and not pfad_aus_dem_netz(pfad):
            return await _antwort(scope, receive, send, 404)
        if pfad == "/" and scope["type"] == "http":
            return await _antwort(scope, receive, send, 307, ort="/app/")  # im Einzelplatz ist die App der Start
        return await self.app(scope, receive, send)


async def _antwort(scope, receive, send, status: int, ort: str | None = None) -> None:
    if scope["type"] == "websocket":
        await send({"type": "websocket.close", "code": 1008})
        return
    kopf = [(b"content-length", b"0"), (b"cache-control", b"no-store"), (b"x-content-type-options", b"nosniff")]
    if ort:
        kopf.append((b"location", ort.encode()))
    await send({"type": "http.response.start", "status": status, "headers": kopf})
    await send({"type": "http.response.body", "body": b""})


# ---------------------------------------------------------------- Geordnet beenden (Programm)
def _stopp_signal() -> None:
    import signal

    signal.raise_signal(signal.SIGINT)  # wie Strg+C: uvicorn fährt geordnet herunter


def stopp_waechter_starten(takt: float = 1.0) -> threading.Event | None:
    """EINZELPLATZ_STOPP_DATEI: Legt das Programm die Datei an, löscht der Server sie und beendet sich geordnet."""
    datei = get_settings().einzelplatz_stopp_datei
    if not an() or datei is None:
        return None
    try:
        datei.unlink(missing_ok=True)  # eine alte Datei vom letzten Mal gilt nicht
    except OSError:
        pass
    ende = threading.Event()

    def schleife():
        while not ende.wait(takt):
            try:
                if datei.exists():
                    datei.unlink(missing_ok=True)
                    log.info("Einzelplatz: Das Programm beendet den Server")
                    _stopp_signal()
                    return
            except OSError:
                continue

    threading.Thread(target=schleife, name="einzelplatz-stopp", daemon=True).start()
    return ende


# ---------------------------------------------------------------- Startschlüssel
_schluessel: dict = {"hash": None, "bis": 0.0}


def schluessel_laden() -> bool:
    """Beim Serverstart: Startschlüssel aus der Umgebung oder der Datei übernehmen. Er wird sofort aus der Umgebung
    entfernt (der Worker-Prozess erbt ihn nicht) und die Datei gelöscht. Gespeichert wird nur die Prüfsumme, im
    Arbeitsspeicher; nach einem Neustart braucht es einen neuen Schlüssel."""
    _schluessel.update(hash=None, bis=0.0)
    wert = os.environ.pop(SCHLUESSEL_ENV, "") or ""
    datei = os.environ.pop(SCHLUESSEL_DATEI_ENV, "") or ""
    if not wert and datei:
        pfad = Path(datei)
        try:
            wert = pfad.read_text(encoding="utf-8")
        except OSError:
            log.warning("Einzelplatz: Startschlüssel-Datei nicht lesbar")
        try:
            pfad.unlink(missing_ok=True)
        except OSError:
            log.warning("Einzelplatz: Startschlüssel-Datei ließ sich nicht löschen")
    wert = wert.strip()
    if not wert:
        return False
    if len(wert.encode("utf-8")) < SCHLUESSEL_MIN_BYTES:
        log.warning("Einzelplatz: Startschlüssel zu kurz – ignoriert")
        return False
    _schluessel.update(hash=hashlib.sha256(wert.encode("utf-8")).digest(), bis=time.monotonic() + SCHLUESSEL_FRIST_S)
    return True


def schluessel_pruefen(wert: str | None) -> bool:
    soll = _schluessel["hash"]
    if soll is None or not wert or time.monotonic() > _schluessel["bis"]:
        return False
    return hmac.compare_digest(soll, hashlib.sha256(wert.encode("utf-8")).digest())


def schluessel_verbrauchen() -> None:
    _schluessel.update(hash=None, bis=0.0)


def zuruecksetzen() -> None:
    """Nur für Tests."""
    schluessel_verbrauchen()
    _adressen.update(zeit=-1e9, werte=frozenset(), haupt=None)


# ---------------------------------------------------------------- Erstes Konto (nur das Programm)
router = APIRouter(prefix="/verwaltung/einzelplatz", include_in_schema=False)
_konto_lock = threading.Lock()


class KontoIn(BaseModel):
    schluessel: str = Field(max_length=512)
    name: str = Field(max_length=128)
    passwort: str = Field(max_length=1024)
    anmeldename: str | None = Field(default=None, max_length=64)
    betriebsart: str | None = None  # cloud | lokal | beides


def anmeldename_aus(name: str) -> str:
    """„Anna Müller“ → „anna.muller“ (nur Zeichen, die überall erlaubt sind)."""
    flach = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    flach = re.sub(r"[^a-z0-9]+", ".", flach).strip(".")[:40]
    return flach if len(flach) >= 3 and flach != "admin" else "spielleitung"


def _fehler(status: int, code: str, **mehr) -> JSONResponse:
    return JSONResponse(status_code=status, content={"code": code, **mehr}, headers={"Cache-Control": "no-store"})


@router.post("/konto")
def erstes_konto(body: KontoIn, request: Request, db: Session = Depends(get_db)):
    """Legt das Konto der SL an (Verwalter dieses Servers). Nur mit gültigem Startschlüssel; ohne ihn gibt es den Weg
    nicht (404). Antworten: 201 {anmeldename}, 400 {code}, 409 already_set_up."""
    herkunft = request.headers.get("origin")
    if herkunft is not None or not vom_pc(request.client.host if request.client else None):
        return _fehler(404, "not_found")  # Browser schicken immer Origin – das Programm nicht
    with _konto_lock:  # zwei gleichzeitige Anfragen mit demselben Schlüssel ergeben nie zwei Konten
        return _erstes_konto(body, db)


def _erstes_konto(body: KontoIn, db: Session) -> JSONResponse:
    from app.cli import _neues_konto
    from app.einrichtung import BETRIEBSARTEN, braucht_einrichtung, code_verbrauchen
    from app.einstellungen import meta_lesen, meta_schreiben
    from app.konto import BENUTZERNAME, passwort_zu_schwach
    from app.models import User
    from app.verwaltung.router import MIN_PASSWORT

    if not schluessel_pruefen(body.schluessel):
        return _fehler(404, "not_found")
    if not braucht_einrichtung(db):
        schluessel_verbrauchen()
        return _fehler(409, "already_set_up")
    name = " ".join(body.name.split())
    if not name:
        return _fehler(400, "name_missing")
    wunsch = (body.anmeldename or "").strip().lower()
    if wunsch and (not BENUTZERNAME.fullmatch(wunsch) or wunsch == "admin"):
        return _fehler(400, "username_invalid")
    basis = wunsch or anmeldename_aus(name)
    anmeldename, n = basis, 1
    while db.scalar(select(User.id).where(User.username == anmeldename)) is not None:
        if wunsch:
            return _fehler(400, "username_taken")
        n += 1
        anmeldename = f"{basis}{n}"
    if len(body.passwort) < MIN_PASSWORT:
        return _fehler(400, "password_too_short", minLength=MIN_PASSWORT)
    if passwort_zu_schwach(body.passwort, anmeldename, name):
        return _fehler(400, "password_too_weak")
    if body.betriebsart is not None and body.betriebsart not in BETRIEBSARTEN:
        return _fehler(400, "validation_error")
    _neues_konto(db, anmeldename, name, body.passwort, admin=True)
    if meta_lesen(db, "angabe.server_name") is None:
        meta_schreiben(db, "angabe.server_name", f"Taleward – {name}"[:128])
    if meta_lesen(db, "angabe.server_operator") is None:
        meta_schreiben(db, "angabe.server_operator", name)
    if body.betriebsart:
        meta_schreiben(db, "betrieb.art", body.betriebsart)
    code_verbrauchen(db)
    db.commit()
    schluessel_verbrauchen()
    log.info("Einzelplatz: erstes Konto angelegt")
    return JSONResponse(status_code=201, content={"anmeldename": anmeldename}, headers={"Cache-Control": "no-store"})
