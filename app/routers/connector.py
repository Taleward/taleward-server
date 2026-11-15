"""0.4.168: Connector – Adressen für den Assistenten (Entdeckung, Registrierung, Schlüssel, MCP). Regeln in
app/connector.py; die Zustimmung des Betreibers liegt in der Verwaltung (app/verwaltung/connector_seite.py)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request
from fastapi.responses import JSONResponse, Response

from app import connector
from app.begrenzung import ZAEHLER
from app.db import session_factory

router = APIRouter(include_in_schema=False)
KEIN_SPEICHER = {"Cache-Control": "no-store"}


def _weg() -> Response:
    return Response(status_code=404)


def _host(request: Request) -> str | None:
    return request.client.host if request.client else None


@router.get("/.well-known/oauth-protected-resource")
@router.get("/.well-known/oauth-protected-resource/mcp")
def ressource():
    with session_factory()() as db:
        return JSONResponse(connector.angaben_ressource(db)) if connector.an(db) else _weg()


@router.get("/.well-known/oauth-authorization-server")
@router.get("/.well-known/oauth-authorization-server/mcp")
def anmeldung():
    with session_factory()() as db:
        return JSONResponse(connector.angaben_anmeldung(db)) if connector.an(db) else _weg()


def _fehler(e: connector.Fehler) -> JSONResponse:
    inhalt = {"error": e.kennung}
    if e.text:
        inhalt["error_description"] = e.text
    return JSONResponse(inhalt, status_code=e.status, headers=KEIN_SPEICHER)


@router.post("/connector/register")
async def registrieren(request: Request):
    with session_factory()() as db:
        if not connector.an(db) or not connector.netz_ok(db, _host(request)):
            return _weg()
        if not ZAEHLER.versuch("connector-register", 20, 3600):
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=429)
        try:
            daten = json.loads(await request.body() or b"{}")
            antwort = connector.registrieren(db, daten)
        except ValueError:
            return _fehler(connector.Fehler("invalid_client_metadata"))
        except connector.Fehler as e:
            return _fehler(e)
        db.commit()
        return JSONResponse(antwort, status_code=201, headers=KEIN_SPEICHER)


@router.post("/connector/token")
async def schluessel(request: Request):
    with session_factory()() as db:
        if not connector.an(db) or not connector.netz_ok(db, _host(request)):
            return _weg()
        if not ZAEHLER.versuch("connector-token", 120, 3600):
            return JSONResponse({"error": "temporarily_unavailable"}, status_code=429)
        werte = await request.form()
        try:
            antwort = connector.schluessel_tauschen(db, werte)
        except connector.Fehler as e:
            db.commit()  # ein verbrauchter Code bleibt verbraucht
            return _fehler(e)
        db.commit()
        return JSONResponse(antwort, headers=KEIN_SPEICHER)


def _rpc_fehler(kennung, code: int, text: str) -> dict:
    return {"jsonrpc": "2.0", "id": kennung, "error": {"code": code, "message": text}}


async def _werkzeug(request: Request, name: str, werte: dict, grund: str, cookie: str, zugang_id: str) -> dict:
    if name == "uebersicht":
        with session_factory()() as db:
            text, fehler, status, pfad = connector.uebersicht_text(db), False, 200, ""
    elif name == "seite_lesen":
        ab = werte.get("ab") if isinstance(werte.get("ab"), int) and werte.get("ab") >= 0 else 0
        text, fehler, status, pfad = await connector.aufruf(request.app, grund, cookie, "GET", werte.get("pfad"), ab=ab)
    elif name == "formular_senden":
        felder = werte.get("felder") if isinstance(werte.get("felder"), dict) else {}
        text, fehler, status, pfad = await connector.aufruf(request.app, grund, cookie, "POST", werte.get("pfad"),
                                                            felder)
    else:
        return {"content": [{"type": "text", "text": f"Unbekanntes Werkzeug: {name}"}], "isError": True}
    with session_factory()() as db:
        connector.protokoll(db, {"uebersicht": "uebersicht", "seite_lesen": "lesen",
                                 "formular_senden": "senden"}[name], pfad.split("?")[0], status)
        connector.benutzt(db, zugang_id)
        db.commit()
    return {"content": [{"type": "text", "text": text}], "isError": fehler}


async def _eine(request: Request, anfrage, grund: str, cookie: str, zugang_id: str) -> dict | None:
    if not isinstance(anfrage, dict) or anfrage.get("jsonrpc") != "2.0" or not isinstance(anfrage.get("method"), str):
        return _rpc_fehler(anfrage.get("id") if isinstance(anfrage, dict) else None, -32600, "Invalid Request")
    methode, kennung = anfrage["method"], anfrage.get("id")
    werte = anfrage.get("params") if isinstance(anfrage.get("params"), dict) else {}
    if "id" not in anfrage:
        return None  # Mitteilung (notifications/…): keine Antwort
    if methode == "initialize":
        fassung = werte.get("protocolVersion")
        from app.routers.worker import server_fassung

        ergebnis = {"protocolVersion": fassung if fassung in connector.PROTOKOLLE else connector.PROTOKOLLE[1],
                    "capabilities": {"tools": {"listChanged": False}},
                    "serverInfo": {"name": "taleward-server", "title": "Taleward", "version": server_fassung()},
                    "instructions": connector.HINWEISE}
    elif methode == "ping":
        ergebnis = {}
    elif methode == "tools/list":
        ergebnis = {"tools": connector.WERKZEUGE}
    elif methode == "tools/call":
        argumente = werte.get("arguments") if isinstance(werte.get("arguments"), dict) else {}
        ergebnis = await _werkzeug(request, str(werte.get("name")), argumente, grund, cookie, zugang_id)
    else:
        return _rpc_fehler(kennung, -32601, "Method not found")
    return {"jsonrpc": "2.0", "id": kennung, "result": ergebnis}


@router.api_route("/mcp", methods=["GET", "POST", "DELETE"])
async def mcp(request: Request):
    with session_factory()() as db:
        if not connector.an(db):
            return _weg()
        if not connector.netz_ok(db, _host(request)):
            return Response(status_code=403)
        kopf = request.headers.get("authorization") or ""
        gefunden = connector.zugang(db, kopf[7:].strip() if kopf.lower().startswith("bearer ") else None)
        if gefunden is None:
            ziel = f"{connector.basis(db)}/.well-known/oauth-protected-resource"
            return Response(status_code=401, headers={"WWW-Authenticate": f'Bearer resource_metadata="{ziel}"'})
        z, user = gefunden
        grund, zugang_id = connector.basis(db), z["id"]
        cookie = connector.sitzungs_cookie(db, user, zugang_id)
    if request.method != "POST":
        return Response(status_code=405, headers={"Allow": "POST"})
    try:
        inhalt = json.loads(await request.body())
    except ValueError:
        return JSONResponse(_rpc_fehler(None, -32700, "Parse error"), status_code=400)
    if isinstance(inhalt, list):
        antworten = [a for a in [await _eine(request, x, grund, cookie, zugang_id) for x in inhalt[:20]] if a]
        return JSONResponse(antworten) if antworten else Response(status_code=202)
    antwort = await _eine(request, inhalt, grund, cookie, zugang_id)
    return JSONResponse(antwort) if antwort is not None else Response(status_code=202)
