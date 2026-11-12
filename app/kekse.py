"""0.4.165: Namen und Regeln der Taleward-Cookies an einer Stelle.

Unter https tragen alle Cookies den Vorsatz ``__Host-``: Der Browser nimmt sie dann nur mit ``Secure``, ``Path=/`` und
ohne ``Domain`` an. So kann keine andere Seite unter derselben Domain (etwa eine Zusatz-App auf einer Subdomain) ein
gleichnamiges Cookie unterschieben. Im Heimnetz bzw. am PC ohne https bleibt alles wie bisher: Name ohne Vorsatz und
der bisherige Pfad.

Gelesen wird nur unter dem erwarteten Namen; schickt der Browser denselben Namen mehrfach mit verschiedenen Werten,
gilt keiner (abgemeldet statt geraten).
"""
from __future__ import annotations

from fastapi import Request, Response
from starlette.requests import cookie_parser

VORSATZ = "__Host-"

# Bis 0.4.164 unter https ohne Vorsatz gesetzt – werden einmal gelöscht, sobald der Browser sie noch mitschickt
ALTE_PFADE = {"tw_verwaltung": "/verwaltung", "tw_verwaltung_code": "/verwaltung", "tw_guthaben": "/guthaben",
              "tw_guthaben_code": "/guthaben", "tw_geraet": "/", "tw_sprache": "/"}
ALTER_OIDC_PFAD = "/auth/oidc/"


def https(request: Request) -> bool:
    return request.url.scheme == "https"


def name(basis: str, sicher: bool) -> str:
    return VORSATZ + basis if sicher else basis


def _alle(request: Request) -> list[tuple[str, str]]:
    """Alle Cookies so, wie der Browser sie schickt – auch doppelte Namen (das Wörterbuch der Anfrage behält nur einen)."""
    paare: list[tuple[str, str]] = []
    for teil in (request.headers.get("cookie") or "").split(";"):
        paare.extend(cookie_parser(teil).items())
    return paare


def lesen(request: Request, basis: str, sicher: bool | None = None) -> str | None:
    """Wert des Cookies unter dem für diese Verbindung erwarteten Namen; fehlt er oder kommt er mit verschiedenen
    Werten mehrfach: None."""
    gesucht = name(basis, https(request) if sicher is None else sicher)
    werte = {w for n, w in _alle(request) if n == gesucht}
    return werte.pop() if len(werte) == 1 else None  # gleicher Wert mehrfach schadet nicht, verschiedene gelten nicht


def setzen(antwort: Response, request: Request, basis: str, wert: str, *, pfad: str, sicher: bool | None = None,
           **optionen) -> None:
    sicher = https(request) if sicher is None else sicher
    antwort.set_cookie(name(basis, sicher), wert, path="/" if sicher else pfad, secure=sicher, **optionen)


def loeschen(antwort: Response, request: Request, basis: str, *, pfad: str, sicher: bool | None = None) -> None:
    sicher = https(request) if sicher is None else sicher
    antwort.delete_cookie(name(basis, sicher), path="/" if sicher else pfad, secure=sicher, httponly=True)


def alte_aufraeumen(request: Request, antwort: Response) -> None:
    """Unter https: Cookies mit den alten Namen (ohne Vorsatz) einmal löschen. Die laufende Sitzung endet dadurch – so
    gewollt (einmal neu anmelden)."""
    if not https(request) or not request.headers.get("cookie"):
        return
    for n in {n for n, _ in _alle(request)}:
        pfad = ALTE_PFADE.get(n) or (ALTER_OIDC_PFAD if n.startswith("tw_oidc_") else None)
        if pfad:
            antwort.delete_cookie(n, path=pfad, secure=True, httponly=True)
