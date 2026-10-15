"""Verwaltung → „Abrechnung“ (0.4.85): Schalter, Preise des Betreibers, Übersicht der Guthaben, Gutschrift von Hand und
Gutscheine. Gutschrift und Gutscheine nur mit eingeschaltetem zweiten Faktor, mit Rückfrage, Obergrenze und Protokoll.
Bei ausgeschalteter Abrechnung lassen sich nur Schalter und Preise ändern."""
from __future__ import annotations

import json
from dataclasses import fields
from datetime import datetime, time, timezone

from fastapi import APIRouter, Depends, Form, Request
from fastapi.responses import HTMLResponse
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app import abrechnung, errors
from app.db import get_db
from app.models import AbrechnungProtokoll, Buchung, Guthaben, Gutschein, Organization, User
from app.verwaltung.router import _seite, _zurueck, csrf_pruefen, tr, verwalter

router = APIRouter(prefix="/verwaltung", include_in_schema=False)

# Felder der Preise in der Reihenfolge der Seite: (Name, Beschriftung, Euro-Betrag?)
PREISFELDER = [
    ("aufschlag_prozent", "Aufschlag in Prozent (auf alle Kosten)", False),
    ("worker_transkription_h", "Worker: Transkription je Stunde Aufnahme (Euro)", True),
    ("worker_kapitel_h", "Worker: Kapitel je Stunde Aufnahme (Euro)", True),
    ("worker_korrektur", "Worker: Korrektur per Hinweis (Euro)", True),
    ("worker_unterlage_seite", "Worker: SL-Unterlage je Seite (Euro)", True),
    ("cloud_transkription_h", "Cloud, nur Schätzung: Transkription je Stunde Aufnahme (Euro, 0 = automatisch)", True),
    ("cloud_kapitel_h", "Cloud, nur Schätzung: Kapitel je Stunde Aufnahme (Euro, 0 = automatisch)", True),
    ("cloud_korrektur", "Cloud, nur Schätzung: Korrektur per Hinweis (Euro, 0 = automatisch)", True),
    ("cloud_unterlage_seite", "Cloud, nur Schätzung: SL-Unterlage je Seite (Euro, 0 = automatisch)", True),
    ("deckel", "So weit darf ein Auftrag laut Schätzung ins Minus führen (Euro)", True),
    ("warnung_ab", "„Guthaben knapp“ unter (Euro)", True),
    ("freigabe_standard", "Neue Guthaben: Aufträge darüber warten auf „Verarbeiten“ (Euro)", True),
    ("gutschrift_max", "Höchstens je Gutschrift von Hand bzw. je Gutschein (Euro)", True),
]


def _euro_lesen(text: str) -> int | None:
    t = (text or "").strip().replace("€", "").replace(" ", "").replace(".", "").replace(",", ".")
    try:
        wert = round(float(t) * 100)
    except ValueError:
        return None
    return wert if 0 <= wert <= 10_000_000 else None


def _euro_feld(cent: int) -> str:
    return f"{cent // 100},{cent % 100:02d}"


def _name(db: Session, g: Guthaben) -> str:
    if g.art == "organisation":
        org = db.get(Organization, g.organization_id) if g.organization_id else None
        return org.name if org else "?"
    u = db.get(User, g.user_id) if g.user_id else None
    return f"{u.display_name} ({u.username})" if u else "–"


def _kurz(daten: dict) -> str:
    """Protokolleintrag lesbar: bei alt/neu nur, was sich geändert hat."""
    alt, neu = daten.get("alt"), daten.get("neu")
    if isinstance(alt, dict) and isinstance(neu, dict):
        return ", ".join(f"{k}: {alt.get(k)} → {v}" for k, v in neu.items() if alt.get(k) != v)
    if "alt" in daten and "neu" in daten:
        return f"{alt} → {neu}"
    return ", ".join(f"{k}: {v}" for k, v in daten.items() if v is not None)


def _faktor(db: Session, user: User) -> bool:
    from app.verwaltung import zweiter_faktor

    return zweiter_faktor.aktiv(db, user.id)


def _seite_abrechnung(request: Request, user: User, db: Session, status: int = 200, **ctx) -> HTMLResponse:
    if abrechnung.an(db):
        abrechnung.offene_abrechnen(db)
    p = abrechnung.preise(db)
    werte = {f.name: getattr(p, f.name) for f in fields(p)}
    konten = []
    for g in db.scalars(select(Guthaben).order_by(Guthaben.angelegt)):
        konten.append({"g": g, "name": _name(db, g), "saldo": abrechnung.saldo(db, g.id)})
    aktionen = db.execute(select(Gutschein.aktion, Gutschein.betrag_cent, func.count(),
                                 func.count(Gutschein.eingeloest_at), func.max(Gutschein.gueltig_bis))
                          .group_by(Gutschein.aktion, Gutschein.betrag_cent).order_by(Gutschein.aktion)).all()
    storniert = set(db.scalars(select(Buchung.gegen_buchung_id).where(Buchung.gegen_buchung_id.is_not(None))))
    gutschriften = [{"b": b, "name": _name(db, db.get(Guthaben, b.guthaben_id))}
                    for b in db.scalars(select(Buchung).where(Buchung.art == "gutschrift")
                                        .order_by(Buchung.angelegt.desc()).limit(20)) if b.id not in storniert]
    protokoll = []
    for z in db.scalars(select(AbrechnungProtokoll).order_by(AbrechnungProtokoll.angelegt.desc()).limit(30)):
        von = db.get(User, z.von)
        protokoll.append({"z": z, "von": von.display_name if von else z.von, "daten": _kurz(json.loads(z.daten or "{}"))})
    orgs = list(db.scalars(select(Organization).order_by(Organization.name)))
    # Cloud-Arten ohne eigenen Schätzwert und ohne bisherige Läufe – die würden mit 0 € geschätzt
    namen = {"transcribe": tr(request)("Transkription"), "summarize": tr(request)("Kapitel"),
             "revise": tr(request)("Korrektur per Hinweis"), "document": tr(request)("SL-Unterlage")}
    ohne = [namen[a] for a in abrechnung.KOSTENPFLICHTIG if abrechnung.preis_je_einheit(db, a, "cloud") <= 0]
    # 0.4.93: was „0 = automatisch“ gerade bedeutet (Mittelwert der Läufe, sonst aus den Preisen des Anbieters)
    automatisch = {a: abrechnung.euro_genau(abrechnung._mittelwert_cloud(db, a) or abrechnung.preis_aus_anbieter(db, a))
                   for a in abrechnung.KOSTENPFLICHTIG}
    daten = {"bestaetigen": None, "codes": None, "cloud_ohne_schaetzung": ", ".join(ohne), "automatisch": automatisch,
             **ctx}
    antwort = _seite(request, "abrechnung.html", user, db, modus=abrechnung.modus(db), preisfelder=PREISFELDER,
                     werte=werte, euro_feld=_euro_feld, euro=abrechnung.euro, konten=konten, aktionen=aktionen,
                     gutschriften=gutschriften, protokoll=protokoll, faktor=_faktor(db, user), orgs=orgs,
                     an=abrechnung.an(db), **daten)
    antwort.status_code = status
    return antwort


@router.get("/abrechnung", response_class=HTMLResponse)
def seite(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    return _seite_abrechnung(request, user, db)


@router.post("/abrechnung/modus", dependencies=[Depends(csrf_pruefen)])
def modus(request: Request, modus: str = Form(""), user: User = Depends(verwalter), db: Session = Depends(get_db)):
    if modus not in abrechnung.MODI:
        return _seite_abrechnung(request, user, db, 400, fehler=tr(request)("Bitte eine der Möglichkeiten wählen."))
    if modus != "aus" and not _faktor(db, user):  # 0.4.87; Ausschalten geht immer – die Notbremse
        return _ohne_faktor(request, user, db)
    if modus != abrechnung.modus(db):
        abrechnung.modus_schreiben(db, modus, user.id)
        db.commit()
    return _zurueck("/abrechnung", "gespeichert")


@router.post("/abrechnung/preise", dependencies=[Depends(csrf_pruefen)])
async def preise(request: Request, user: User = Depends(verwalter), db: Session = Depends(get_db)):
    if not _faktor(db, user):  # 0.4.87: Aufschlag und Preise nur mit zweitem Faktor
        return _ohne_faktor(request, user, db)
    form = await request.form()
    p = abrechnung.preise(db)
    for name, _text, ist_euro in PREISFELDER:
        roh = str(form.get(name, ""))
        if ist_euro:
            wert = _euro_lesen(roh)
        else:
            try:
                wert = int(roh.strip())
            except ValueError:
                wert = None
            wert = wert if wert is not None and 0 <= wert <= 1000 else None
        if wert is None:
            return _seite_abrechnung(request, user, db, 400, fehler=tr(request)(
                "Bitte überall eine Zahl eingeben (Euro mit Komma, z. B. 1,50)."))
        setattr(p, name, wert)
    abrechnung.preise_schreiben(db, p, user.id)
    db.commit()
    return _zurueck("/abrechnung", "gespeichert")


def _ziel(db: Session, ziel_konto: str, ziel_org: str) -> Guthaben | None:
    if ziel_org:
        return abrechnung.guthaben_organisation(db, ziel_org) if db.get(Organization, ziel_org) else None
    u = db.scalar(select(User).where(User.username == ziel_konto.strip().lower())) if ziel_konto.strip() else None
    return abrechnung.guthaben_konto(db, u.id) if u is not None and not u.setup_account else None


def _ohne_faktor(request: Request, user: User, db: Session) -> HTMLResponse:
    return _seite_abrechnung(request, user, db, 403, fehler=tr(request)(
        "Das geht nur mit eingeschaltetem zweiten Faktor (Anmeldung → Dein zweiter Faktor)."))


def _vorbedingungen(request: Request, user: User, db: Session) -> HTMLResponse | None:
    if not abrechnung.an(db):
        raise errors.not_found()
    if not _faktor(db, user):
        return _ohne_faktor(request, user, db)
    return None


@router.post("/abrechnung/gutschrift", dependencies=[Depends(csrf_pruefen)])
def gutschrift(request: Request, ziel_konto: str = Form(""), ziel_org: str = Form(""), betrag: str = Form(""),
               grund: str = Form(""), bestaetigt: str = Form(""), user: User = Depends(verwalter),
               db: Session = Depends(get_db)):
    sperre = _vorbedingungen(request, user, db)
    if sperre is not None:
        return sperre
    _ = tr(request)
    g = _ziel(db, ziel_konto, ziel_org)
    cent = _euro_lesen(betrag)
    grenze = abrechnung.preise(db).gutschrift_max
    if g is None:
        return _seite_abrechnung(request, user, db, 400, fehler=_("Dieses Konto gibt es nicht."))
    if not cent or cent > grenze:
        return _seite_abrechnung(request, user, db, 400, fehler=_(
            "Bitte einen Betrag über 0 und höchstens {betrag} eingeben.", betrag=abrechnung.euro(grenze)))
    if not " ".join(grund.split()):
        return _seite_abrechnung(request, user, db, 400, fehler=_("Bitte einen Grund angeben."))
    if not bestaetigt:  # Rückfrage
        db.rollback()
        return _seite_abrechnung(request, user, db, bestaetigen={
            "name": _name(db, g), "betrag": abrechnung.euro(cent), "grund": grund.strip()[:500],
            "ziel_konto": ziel_konto, "ziel_org": ziel_org, "roh": betrag})
    abrechnung.gutschreiben(db, g, cent, grund, user.id)
    db.commit()
    return _zurueck("/abrechnung", "gutschrift")


@router.post("/abrechnung/storno/{buchung_id}", dependencies=[Depends(csrf_pruefen)])
def storno(request: Request, buchung_id: str, bestaetigt: str = Form(""), user: User = Depends(verwalter),
           db: Session = Depends(get_db)):
    sperre = _vorbedingungen(request, user, db)
    if sperre is not None:
        return sperre
    if not bestaetigt:
        return _seite_abrechnung(request, user, db, 400, fehler=tr(request)(
            "Bitte das Stornieren mit dem Haken bestätigen."))
    try:
        abrechnung.stornieren(db, buchung_id, user.id)
    except ValueError:
        db.rollback()
        return _seite_abrechnung(request, user, db, 400, fehler=tr(request)(
            "Diese Gutschrift lässt sich nicht (mehr) stornieren."))
    db.commit()
    return _zurueck("/abrechnung", "storno")


@router.post("/abrechnung/gutscheine", dependencies=[Depends(csrf_pruefen)])
def gutscheine(request: Request, anzahl: str = Form(""), betrag: str = Form(""), aktion: str = Form(""),
               gueltig_bis: str = Form(""), bestaetigt: str = Form(""), user: User = Depends(verwalter),
               db: Session = Depends(get_db)):
    sperre = _vorbedingungen(request, user, db)
    if sperre is not None:
        return sperre
    _ = tr(request)
    cent = _euro_lesen(betrag)
    try:
        n = int(anzahl.strip())
    except ValueError:
        n = 0
    bis = None
    if gueltig_bis.strip():
        try:
            bis = datetime.combine(datetime.strptime(gueltig_bis.strip(), "%Y-%m-%d").date(), time(23, 59, 59),
                                   tzinfo=timezone.utc)
        except ValueError:
            return _seite_abrechnung(request, user, db, 400, fehler=_("Das Datum ist ungültig."))
    if not bestaetigt:
        return _seite_abrechnung(request, user, db, 400, fehler=_("Bitte das Erzeugen mit dem Haken bestätigen."))
    try:
        codes = abrechnung.gutscheine_erzeugen(db, n, cent or 0, aktion, bis, user.id)
    except ValueError:
        db.rollback()
        return _seite_abrechnung(request, user, db, 400, fehler=_(
            "Bitte 1 bis 100 Gutscheine, einen Betrag bis {betrag} und einen Namen für die Aktion angeben.",
            betrag=abrechnung.euro(abrechnung.preise(db).gutschrift_max)))
    db.commit()
    return _seite_abrechnung(request, user, db, codes={"liste": codes, "betrag": abrechnung.euro(cent or 0),
                                                       "aktion": aktion.strip()})
