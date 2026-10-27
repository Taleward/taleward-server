"""Seite „Guthaben“ für Spielleitungen (0.4.85, nur bei eingeschalteter Abrechnung – sonst 404).

Unter /guthaben sieht die SL ihr Guthaben und ihre Buchungen, löst Gutscheine ein, bestätigt teure Aufträge
(„Verarbeiten“), stellt ihren Betrag dafür und die Grenzen für Charakterbögen ein und gibt die Zahlung einer Kampagne
ab bzw. übernimmt sie. Im Topf-Modus sehen die Verwalter der Organisation hier auch deren Guthaben.

Anmeldung mit Benutzername und Passwort (wie „Konto löschen“), eigenes kurzlebiges Cookie nur für diesen Pfad. Konten
nur mit Anmeldung über einen Dienst setzen sich in der App unter Konto ein Passwort. Hat das Konto einen zweiten Faktor
eingeschaltet (Verwalter), kommt danach der Code aus der Authenticator-App (0.4.87).
"""
from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import timedelta

import jwt
from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse, RedirectResponse
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import abrechnung, begrenzung, errors, zahlung
from app.config import get_settings
from app.db import get_db, server_id, utcnow
from app.models import AuthMethod, Campaign, Guthaben, Job, Member, User
from app.security import verify_password
from app.verwaltung.router import _seite, tr

router = APIRouter(prefix="/guthaben", include_in_schema=False)
COOKIE = "tw_guthaben"
COOKIE_CODE = "tw_guthaben_code"
CODE_MINUTEN = 5
STUNDEN = 2
GUTSCHEIN_FENSTER = 15 * 60
GUTSCHEIN_JE_KONTO, GUTSCHEIN_JE_ADRESSE = 10, 30

MELDUNGEN = {
    "gespeichert": "Gespeichert.",
    "gutschein": "Der Gutschein ist eingelöst.",
    "verarbeiten": "Bestätigt. Der Auftrag startet, sobald er an der Reihe ist.",
    "angeboten": "Die andere Spielleitung sieht das Angebot auf dieser Seite und kann es annehmen.",
    "uebernommen": "Du zahlst jetzt für diese Kampagne.",
}
# 0.4.133: Die Bestätigungsseite schickt das Formular an den Zahlungsanbieter weiter – nur an diese Adressen
FORM_ZIELE = "https://checkout.stripe.com https://www.paypal.com https://www.sandbox.paypal.com"


class _Abgemeldet(Exception):
    pass


def _aud() -> str:
    return f"{server_id()}:guthaben"


def _csrf(wert: str) -> str:
    return hmac.new(get_settings().jwt_secret.encode(), b"guthaben-csrf:" + wert.encode(),
                    hashlib.sha256).hexdigest()[:40]


class _Aus(Exception):
    """Guthaben ist auf diesem Server aus – Seite statt Fehlermeldung als Daten."""


def _an(db: Session = Depends(get_db)) -> None:
    if not abrechnung.an(db):
        raise _Aus()


def _person(request: Request, db: Session) -> User | None:
    wert = request.cookies.get(COOKIE)
    if not wert:
        return None
    try:
        daten = jwt.decode(wert, get_settings().jwt_secret, algorithms=["HS256"], audience=_aud())
    except jwt.PyJWTError:
        return None
    user = db.get(User, daten.get("sub"))
    if user is None or user.token_version != daten.get("tv") or user.setup_account:
        return None
    from app.einstellungen import meta_lesen

    # 0.4.89: Wer den zweiten Faktor einschaltet (bzw. sich überall abmeldet), beendet auch Sitzungen hier
    abgemeldet = meta_lesen(db, f"verwaltung.abgemeldet.{user.id}")
    if abgemeldet and abgemeldet.isdigit() and int(daten.get("iat") or 0) < int(abgemeldet):
        return None
    request.state.csrf = _csrf(wert)
    return user


def _angemeldet(request: Request, db: Session = Depends(get_db)) -> User:
    user = _person(request, db)
    if user is None:
        raise _Abgemeldet()
    return user


def _csrf_pruefen(request: Request, csrf: str = Form("")) -> None:
    wert = request.cookies.get(COOKIE, "")
    if not wert or not hmac.compare_digest(csrf, _csrf(wert)):
        raise errors.forbidden()


def _zurueck(ok: str | None = None, anker: str = "") -> RedirectResponse:
    return RedirectResponse("/guthaben" + (f"?ok={ok}" if ok else "") + (f"#{anker}" if anker else ""),
                            status_code=303)


def _euro_lesen(text: str) -> int | None:
    t = (text or "").strip().replace("€", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        wert = round(float(t) * 100)
    except ValueError:
        return None
    return wert if 0 <= wert <= 10_000_000 else None


def _zahl(text: str, standard: int) -> int:
    try:
        return int((text or "").strip())
    except ValueError:
        return standard


# ---------------------------------------------------------------- Anmelden
def _anmeldeseite(request: Request, db: Session, fehler: str = "", name: str = "", status: int = 200) -> HTMLResponse:
    antwort = _seite(request, "guthaben_anmelden.html", None, db, fehler=fehler, konto=name)
    antwort.status_code = status
    return antwort


@router.post("/anmelden", dependencies=[Depends(_an)])
def anmelden(request: Request, username: str = Form(""), password: str = Form(""), db: Session = Depends(get_db)):
    from app.begrenzung import ZAEHLER
    from app.routers.auth import LOGIN_FENSTER, LOGIN_JE_ADRESSE, LOGIN_JE_NAME

    name = username.strip().lower()[:64]
    versuch = ZAEHLER.reservieren([(f"login-adr:{begrenzung.adresse(request)}", LOGIN_JE_ADRESSE, LOGIN_FENSTER),
                                   (f"login-name:{name}", LOGIN_JE_NAME, LOGIN_FENSTER)])
    if versuch is None:
        return _anmeldeseite(request, db, tr(request)("Zu viele Versuche. Bitte in einer Viertelstunde noch einmal."),
                             name, 429)
    user = db.scalar(select(User).where(User.username == name)) if name else None
    methode = db.scalar(select(AuthMethod).where(AuthMethod.user_id == user.id, AuthMethod.kind == "password")) \
        if user is not None else None
    if not verify_password(password, methode.secret if methode else None) or user.setup_account:
        return _anmeldeseite(request, db, tr(request)("Benutzername oder Passwort stimmen nicht."), name, 401)
    ZAEHLER.freigeben(versuch)
    from app.verwaltung import zweiter_faktor

    if zweiter_faktor.aktiv(db, user.id):  # erst noch der Code
        antwort = _code_seite(request, db)
        antwort.set_cookie(COOKIE_CODE, _token(user, f"{server_id()}:guthaben-code", timedelta(minutes=CODE_MINUTEN)),
                           max_age=CODE_MINUTEN * 60, httponly=True, samesite="strict",
                           secure=request.url.scheme == "https", path="/guthaben")
        return antwort
    return _sitzung(request, user)


def _token(user: User, aud: str, dauer: timedelta) -> str:
    jetzt = utcnow()
    return jwt.encode({"sub": user.id, "tv": user.token_version, "aud": aud, "iat": jetzt, "exp": jetzt + dauer,
                       "jti": secrets.token_hex(8)}, get_settings().jwt_secret, algorithm="HS256")


def _sitzung(request: Request, user: User) -> RedirectResponse:
    antwort = _zurueck()
    antwort.set_cookie(COOKIE, _token(user, _aud(), timedelta(hours=STUNDEN)), max_age=STUNDEN * 3600, httponly=True,
                       samesite="strict", secure=request.url.scheme == "https", path="/guthaben")
    antwort.delete_cookie(COOKIE_CODE, path="/guthaben")
    return antwort


def _code_seite(request: Request, db: Session, fehler: str | None = None, status: int = 200) -> HTMLResponse:
    antwort = _seite(request, "anmelden_code.html", None, db, fehler=fehler, ziel="/guthaben/anmelden/code",
                     zurueck="/guthaben")
    antwort.status_code = status
    return antwort


@router.post("/anmelden/code", dependencies=[Depends(_an)])
def anmelden_code(request: Request, code: str = Form(""), db: Session = Depends(get_db)):
    """Zweiter Schritt der Anmeldung: gleiche Prüfung und gleiche Fehlversuch-Grenze wie in der Verwaltung."""
    from app.begrenzung import ZAEHLER
    from app.verwaltung import zweiter_faktor
    from app.verwaltung.router import _versuch

    wert = request.cookies.get(COOKIE_CODE)
    try:
        daten = jwt.decode(wert or "", get_settings().jwt_secret, algorithms=["HS256"],
                           audience=f"{server_id()}:guthaben-code")
    except jwt.PyJWTError:
        return _zurueck()
    user = db.get(User, daten.get("sub"))
    if user is None or user.token_version != daten.get("tv") or user.setup_account:
        return _zurueck()
    versuch = _versuch(begrenzung.adresse(request), f"code:{user.id}")
    if versuch is None:
        return _code_seite(request, db, tr(request)("Zu viele Fehlversuche. Bitte in ein paar Minuten erneut versuchen."),
                           429)
    if not zweiter_faktor.pruefen(db, user.id, code):
        return _code_seite(request, db, tr(request)(
            "Der Code stimmt nicht oder wurde schon benutzt. Nimm den nächsten aus deiner App."), 400)
    ZAEHLER.freigeben(versuch)
    db.commit()
    return _sitzung(request, user)


@router.post("/abmelden", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def abmelden():
    antwort = _zurueck()
    antwort.delete_cookie(COOKIE, path="/guthaben")
    return antwort


# ---------------------------------------------------------------- Übersicht
def _sl_kampagnen(db: Session, user: User) -> list[dict]:
    aus = []
    for m in db.scalars(select(Member).where(Member.user_id == user.id, Member.role == "gm", Member.left_at.is_(None))):
        c = db.get(Campaign, m.campaign_id)
        if c is None or c.archived_at is not None:
            continue
        zahlt = abrechnung.zahler_user_id(db, c)
        andere = [x for x in abrechnung.aktive_sl(db, c) if x.user_id != user.id]
        namen = {x.user_id: (db.get(User, x.user_id).display_name if db.get(User, x.user_id) else "?") for x in andere}
        aus.append({"c": c, "ich_zahle": zahlt == user.id, "zahler": namen.get(zahlt) if zahlt else None,
                    "ohne_zahler": zahlt is None, "angebot_an_mich": c.zahler_angebot_user_id == user.id,
                    "andere": namen, "angeboten_an": namen.get(c.zahler_angebot_user_id or "")})
    return sorted(aus, key=lambda k: k["c"].title.lower())


@router.get("", response_class=HTMLResponse, dependencies=[Depends(_an)])
@router.get("/", response_class=HTMLResponse, dependencies=[Depends(_an)])
def seite(request: Request, db: Session = Depends(get_db)):
    user = _person(request, db)
    if user is None:
        return _anmeldeseite(request, db)
    ok = request.query_params.get("ok")
    return _hauptseite(request, db, user, meldung=tr(request)(MELDUNGEN[ok]) if ok in MELDUNGEN else None)


def _ohne_fremde_titel(db: Session, user: User, g: Guthaben) -> list[dict]:
    """G14: Runde, Unterlage und Auswertung (Titel) nur für die heutige SL der Kampagne – Verwalter eines Topfs, die dort
    nicht SL sind, sehen nur Kampagne, Art und Betrag."""
    aus = []
    for w in abrechnung.wartende_auftraege(db, g):
        w["ist_auswertung"] = w["auswertung"] is not None
        if not abrechnung.ist_aktive_sl(db, user.id, w["kampagne"]):
            w["runde"] = w["unterlage"] = w["auswertung"] = None
        aus.append(w)
    return aus


def _hauptseite(request: Request, db: Session, user: User, meldung: str | None = None, fehler: str | None = None,
                status: int = 200, gutschein_offen: bool = False) -> HTMLResponse:
    from app.models import Organization

    abrechnung.offene_abrechnen(db)
    toepfe = []
    for g in abrechnung.sichtbare_guthaben(db, user.id):
        org = db.get(Organization, g.organization_id) if g.art == "organisation" else None
        toepfe.append({"g": g, "name": (org.name if org else "?") if g.art == "organisation" else None,
                       "saldo": abrechnung.saldo(db, g.id), "stand": abrechnung.stand(db, g),
                       "buchungen": abrechnung.buchungen(db, g, 50), "wartend": _ohne_fremde_titel(db, user, g),
                       "offen": zahlung.offene(db, g) if g.id else []})
    db.commit()  # neu angelegte Guthaben-Zeilen
    antwort = _seite(request, "guthaben.html", None, db, person=user, toepfe=toepfe,
                     kampagnen=_sl_kampagnen(db, user), euro=abrechnung.euro, topf=abrechnung.modus(db) == "topf",
                     meldung=meldung, fehler=fehler, zahlwege_an=zahlung.bereit(db), betraege=zahlung.betraege(db),
                     testmodus=not zahlung.live(db), gutschein_offen=gutschein_offen)
    antwort.status_code = status
    return antwort


def _eigenes(db: Session, user: User, guthaben_id: str) -> Guthaben:
    g = db.get(Guthaben, guthaben_id)
    if g is None or not abrechnung.darf_sehen(db, user.id, g):
        raise errors.not_found()
    return g


def _fehler(request: Request, db: Session, user: User, text: str, gutschein: bool = False) -> HTMLResponse:
    return _hauptseite(request, db, user, fehler=text, status=400, gutschein_offen=gutschein)


@router.post("/gutschein", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def gutschein(request: Request, code: str = Form(""), guthaben_id: str = Form(""), user: User = Depends(_angemeldet),
              db: Session = Depends(get_db)):
    from app.begrenzung import ZAEHLER

    g = _eigenes(db, user, guthaben_id) if guthaben_id else abrechnung.guthaben_konto(db, user.id)
    versuch = ZAEHLER.reservieren([(f"gutschein-adr:{begrenzung.adresse(request)}", GUTSCHEIN_JE_ADRESSE,
                                    GUTSCHEIN_FENSTER),
                                   (f"gutschein-konto:{user.id}", GUTSCHEIN_JE_KONTO, GUTSCHEIN_FENSTER)])
    if versuch is None:
        return _fehler(request, db, user, tr(request)("Zu viele Versuche. Bitte in einer Viertelstunde noch einmal."))
    try:
        abrechnung.gutschein_einloesen(db, g, code, user.id)
    except ValueError as e:
        db.rollback()
        text = tr(request)("Diesen Gutschein hast du aus derselben Aktion schon eingelöst.") if str(e) == "aktion" \
            else tr(request)("Der Code stimmt nicht, ist abgelaufen oder schon eingelöst.")
        return _fehler(request, db, user, text, gutschein=True)
    ZAEHLER.freigeben(versuch)
    db.commit()
    return _zurueck("gutschein")


@router.post("/einstellungen", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def einstellungen(request: Request, guthaben_id: str = Form(""), freigabe_ab: str = Form(""),
                  bogen_seiten: str = Form(""), boegen_je_spieler: str = Form(""), user: User = Depends(_angemeldet),
                  db: Session = Depends(get_db)):
    g = _eigenes(db, user, guthaben_id)
    betrag = _euro_lesen(freigabe_ab)
    if betrag is None:
        return _fehler(request, db, user, tr(request)("Bitte einen Betrag in Euro eingeben, z. B. 2,00."))
    abrechnung.einstellungen_aendern(db, g, user.id, betrag, _zahl(bogen_seiten, g.bogen_seiten),
                                     _zahl(boegen_je_spieler, g.boegen_je_spieler))
    db.commit()
    return _zurueck("gespeichert", "einstellungen")


@router.post("/auftraege/{job_id}/verarbeiten", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def verarbeiten(job_id: str, user: User = Depends(_angemeldet), db: Session = Depends(get_db)):
    job = db.get(Job, job_id)
    if job is None:
        raise errors.not_found()
    g = abrechnung.zahler(db, abrechnung.kampagne_von_auftrag(db, job), anlegen=True)
    if g is None or not abrechnung.darf_sehen(db, user.id, g):
        raise errors.not_found()
    abrechnung.freigeben(db, job, user.id, g)
    db.commit()
    return _zurueck("verarbeiten")


# ---------------------------------------------------------------- Aufladen (0.4.133)
def _zahl_oder_none(text: str) -> int | None:
    try:
        return int((text or "").strip())
    except ValueError:
        return None


@router.post("/aufladen", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def aufladen(request: Request, guthaben_id: str = Form(""), betrag: str = Form(""), anbieter: str = Form(""),
             user: User = Depends(_angemeldet), db: Session = Depends(get_db)):
    """Schritt 1: Betrag und Gebühr zeigen, bevor es zum Anbieter geht."""
    g = _eigenes(db, user, guthaben_id)
    netto = _zahl_oder_none(betrag)
    if anbieter not in zahlung.bereit(db) or netto not in zahlung.betraege(db) or g.gesperrt_at is not None:
        return _fehler(request, db, user, tr(request)("Bitte einen Betrag und eine Zahlweise wählen."))
    antwort = _seite(request, "guthaben_aufladen.html", None, db, schritt="pruefen", guthaben_id=g.id, netto=netto,
                     brutto=zahlung.brutto(db, anbieter, netto), anbieter=anbieter, euro=abrechnung.euro,
                     testmodus=not zahlung.live(db))
    from app.verwaltung.router import SICHERHEITS_KOPFZEILEN

    antwort.headers["Content-Security-Policy"] = SICHERHEITS_KOPFZEILEN["Content-Security-Policy"].replace(
        "form-action 'self'", f"form-action 'self' {FORM_ZIELE}")
    return antwort


@router.post("/aufladen/weiter", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def aufladen_weiter(request: Request, guthaben_id: str = Form(""), betrag: str = Form(""), anbieter: str = Form(""),
                    user: User = Depends(_angemeldet), db: Session = Depends(get_db)):
    """Schritt 2: Vorgang anlegen und zum Anbieter weiterleiten."""
    from app.verwaltung.router import sprache_von

    g = _eigenes(db, user, guthaben_id)
    netto = _zahl_oder_none(betrag)
    _ = tr(request)
    try:
        url = zahlung.starten(db, g, user.id, anbieter, netto if netto is not None else -1, sprache_von(request))
    except ValueError as e:
        db.rollback()
        text = {"grenze": _("Für heute sind genug Aufladungen begonnen. Bitte morgen noch einmal."),
                "gesperrt": _("Dieses Guthaben ist gerade gesperrt.")}.get(str(e),
                                                                           _("Bitte einen Betrag und eine Zahlweise wählen."))
        return _fehler(request, db, user, text)
    except zahlung.ZahlungFehler:
        text = _("Der Zahlungsanbieter ist gerade nicht erreichbar. Es wurde nichts abgebucht. Bitte später noch einmal.")
        return _fehler(request, db, user, text)
    return RedirectResponse(url, status_code=303)


def _ende(request: Request, db: Session, titel: str, text: str) -> HTMLResponse:
    return _seite(request, "guthaben_aufladen.html", None, db, schritt="ende", titel=titel, text=text)


def _vorgang(db: Session, v: str):
    from app.models import Bezahlvorgang

    return db.get(Bezahlvorgang, (v or "")[:64]) if v else None


@router.get("/aufladen/fertig", response_class=HTMLResponse, dependencies=[Depends(_an)])
def aufladen_fertig(request: Request, v: str = "", db: Session = Depends(get_db)):
    """Rücksprung nach der Zahlung. Ohne Anmeldung (das Cookie kommt beim Rücksprung nicht mit) – zeigt nur den Stand."""
    _ = tr(request)
    vorgang = _vorgang(db, v)
    if vorgang is not None and vorgang.zustand == "bezahlt":
        return _ende(request, db, _("Danke"), _("{betrag} sind gutgeschrieben.", betrag=abrechnung.euro(vorgang.netto_cent)))
    text = _("Sobald der Zahlungsanbieter die Zahlung bestätigt, steht der Betrag in deinem Guthaben. Das dauert meist nur ein paar Sekunden, bei Lastschrift auch einige Tage.")  # noqa: E501
    return _ende(request, db, _("Danke"), text)


@router.get("/aufladen/paypal", dependencies=[Depends(_an)])
def aufladen_paypal(request: Request, v: str = "", token: str = "", db: Session = Depends(get_db)):
    """Rücksprung von PayPal: Der Server bucht ab und schreibt nur gut, was PayPal bestätigt."""
    vorgang = _vorgang(db, v)
    if vorgang is None or vorgang.anbieter != "paypal":
        return RedirectResponse("/guthaben/aufladen/fertig", status_code=303)
    try:
        zahlung.paypal_zurueck(db, vorgang, token[:100])
        db.commit()
    except Exception:  # noqa: BLE001 – kommt die Abbuchung später per Meldung, ist nichts verloren
        db.rollback()
    return RedirectResponse(f"/guthaben/aufladen/fertig?v={vorgang.id}", status_code=303)


@router.get("/aufladen/abbruch", response_class=HTMLResponse, dependencies=[Depends(_an)])
def aufladen_abbruch(request: Request, v: str = "", db: Session = Depends(get_db)):
    _ = tr(request)
    vorgang = _vorgang(db, v)
    if vorgang is not None and vorgang.zustand == "offen":
        vorgang.zustand = "abgebrochen"
        db.commit()
    return _ende(request, db, _("Abgebrochen"), _("Die Zahlung wurde abgebrochen. Es wurde nichts abgebucht."))


def _kampagne_als_sl(db: Session, user: User, campaign_id: str) -> Campaign:
    c = db.get(Campaign, campaign_id)
    if c is None or user.id not in {m.user_id for m in abrechnung.aktive_sl(db, c)}:
        raise errors.not_found()
    return c


@router.post("/kampagnen/{campaign_id}/abgeben", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def abgeben(request: Request, campaign_id: str, an_user_id: str = Form(""), user: User = Depends(_angemeldet),
            db: Session = Depends(get_db)):
    c = _kampagne_als_sl(db, user, campaign_id)
    try:
        abrechnung.zahlung_anbieten(db, c, user.id, an_user_id)
    except ValueError:
        db.rollback()
        return _fehler(request, db, user, tr(request)("Bitte eine andere Spielleitung dieser Kampagne wählen."))
    db.commit()
    return _zurueck("angeboten", "kampagnen")


@router.post("/kampagnen/{campaign_id}/uebernehmen", dependencies=[Depends(_an), Depends(_csrf_pruefen)])
def uebernehmen(request: Request, campaign_id: str, bestaetigt: str = Form(""), user: User = Depends(_angemeldet),
                db: Session = Depends(get_db)):
    c = _kampagne_als_sl(db, user, campaign_id)
    if not bestaetigt:
        return _fehler(request, db, user, tr(request)("Bitte die Übernahme mit dem Haken bestätigen."))
    try:
        abrechnung.zahlung_uebernehmen(db, c, user.id)
    except ValueError:
        db.rollback()
        return _fehler(request, db, user, tr(request)("Die Zahlung lässt sich gerade nicht übernehmen."))
    db.commit()
    return _zurueck("uebernommen", "kampagnen")


def einbinden(app) -> None:
    app.include_router(router)

    @app.exception_handler(_Abgemeldet)
    async def _abgemeldet(_req: Request, _exc: _Abgemeldet):
        return RedirectResponse("/guthaben", status_code=303)

    @app.exception_handler(_Aus)
    async def _aus(req: Request, _exc: _Aus):
        _ = tr(req)
        quelle = app.dependency_overrides.get(get_db, get_db)()
        db = next(quelle)
        try:
            return _seite(req, "hinweis_seite.html", None, db, status_code=404, titel=_("Guthaben"),
                          text=_("Auf diesem Server gibt es kein Guthaben. Die Kosten trägt, wer den Server betreibt – du musst hier nichts tun."))
        finally:
            quelle.close()
