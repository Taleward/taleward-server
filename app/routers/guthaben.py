"""Guthaben in der App (Schnittstelle 0.4.25). Logik in app/abrechnung.py; hier nur die Wege und wer was sieht.

- Abrechnung aus → alle Wege 404.
- Sichtbar nur das eigene Guthaben und die Töpfe der Organisationen, in denen man Verwalter ist; alles andere 404
  (ohne Anzahl).
- Topf der Organisation: Ändern, „Verarbeiten“ und Gutscheine nur auf der Seite des Servers mit zweitem Faktor →
  403 second_factor_required.
- G14: Titel und Kennungen von Runden, Unterlagen und Auswertungen nur für die heutige SL der Kampagne.
- Bezahlen gibt es hier nicht: Der Server nennt nur seine Aufladeseite (ServerInfo.billing.topUpUrl).
"""
from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy.orm import Session

from app import abrechnung, begrenzung, errors, schemas
from app.access import current_user, membership
from app.db import get_db
from app.models import (Buchung, Campaign, CampaignDocument, CharacterAnalysis, GameSession, Guthaben, Job,
                        KostenAuftrag, Member, Organization, User)

router = APIRouter(tags=["Guthaben"])

ARTEN_API = {"zahlung": "payment", "gutschrift": "credit", "gutschein": "voucher", "verbrauch": "usage",
             "rueckbuchung": "chargeback", "erstattung": "refund", "korrektur": "correction"}
WARTEND_HOECHSTENS = 100


def _an(db: Session = Depends(get_db)) -> None:
    if not abrechnung.an(db):
        raise errors.not_found()


def _guthaben(db: Session, user: User, credit_id: str) -> Guthaben:
    g = next((x for x in abrechnung.sichtbare_guthaben(db, user.id) if x.id == credit_id), None)
    if g is None:
        raise errors.not_found()
    return g


def _verwalten(g: Guthaben) -> None:
    if abrechnung.ist_topf(g):
        raise errors.ApiError(403, "second_factor_required")


def _wartend(db: Session, g: Guthaben) -> list[dict]:
    """Aufträge, die wegen der Abrechnung warten (ohne die, die nur in der Schlange stehen)."""
    return [w for w in abrechnung.wartende_auftraege(db, g) if w["grund"] in abrechnung.GRUND_API]


def credit_out(db: Session, g: Guthaben) -> schemas.CreditOut:
    org = db.get(Organization, g.organization_id) if g.organization_id else None
    return schemas.CreditOut(
        id=g.id, kind="organization" if abrechnung.ist_topf(g) else "account",
        organization_id=g.organization_id if org else None, organization_name=org.name if org else None,
        balance_cents=abrechnung.saldo(db, g.id), status=abrechnung.STAND_API[abrechnung.stand(db, g)],
        approval_threshold_cents=g.freigabe_ab_cent, sheet_max_pages=g.bogen_seiten,
        sheets_per_player=g.boegen_je_spieler, waiting_count=len(_wartend(db, g)),
        can_manage=not abrechnung.ist_topf(g))


@router.get("/credits", response_model=list[schemas.CreditOut], dependencies=[Depends(_an)])
def credits(user: User = Depends(current_user), db: Session = Depends(get_db)):
    abrechnung.offene_abrechnen(db)
    aus = [credit_out(db, g) for g in abrechnung.sichtbare_guthaben(db, user.id)]
    db.commit()  # neu angelegte Guthaben-Zeilen
    return aus


def _verwendung(db: Session, b: Buchung, user: User) -> schemas.CreditUsageOut | None:
    if b.art != "verbrauch":
        return None
    k = db.get(KostenAuftrag, b.job_id) if b.job_id else None
    job = db.get(Job, b.job_id) if b.job_id else None
    art = k.art if k is not None else (job.type if job is not None else None)
    if art not in abrechnung.KOSTENPFLICHTIG:
        art = "document"
    c = db.get(Campaign, k.campaign_id) if k is not None and k.campaign_id else None
    if c is None and job is not None:
        c = abrechnung.kampagne_von_auftrag(db, job)
    nummer = titel = None
    if c is not None and abrechnung.ist_aktive_sl(db, user.id, c):  # G14
        session_id = (k.session_id if k else None) or (job.session_id if job else None)
        document_id = (k.document_id if k else None) or (job.document_id if job else None)
        analysis_id = (k.analysis_id if k else None) or (job.analysis_id if job else None)
        s = db.get(GameSession, session_id) if session_id else None
        nummer = s.number if s is not None and s.campaign_id == c.id else None
        if analysis_id:
            a = db.get(CharacterAnalysis, analysis_id)
            titel = a.title if a is not None and a.campaign_id == c.id else None
        elif document_id:
            d = db.get(CampaignDocument, document_id)
            titel = d.title if d is not None and d.campaign_id == c.id else None
    return schemas.CreditUsageOut(job_kind=art, campaign_id=c.id if c else None, campaign_title=c.title if c else None,
                                  session_number=nummer, document_title=titel)


@router.get("/credits/{creditId}/bookings", response_model=list[schemas.CreditBookingOut], dependencies=[Depends(_an)])
def bookings(creditId: str, limit: int = 50, before: str | None = None, user: User = Depends(current_user),
             db: Session = Depends(get_db)):
    g = _guthaben(db, user, creditId)
    limit = max(1, min(100, limit))
    vor = None
    if before is not None:
        vor = db.get(Buchung, before[:64])
        if vor is None or vor.guthaben_id != g.id:
            raise errors.not_found()
    aus = [schemas.CreditBookingOut(
        id=b.id, kind=ARTEN_API.get(b.art, "correction"), amount_cents=b.betrag_cent, created_at=b.angelegt,
        reason=b.grund if b.art in ("gutschrift", "korrektur") else None, usage=_verwendung(db, b, user))
        for b in abrechnung.buchungen(db, g, limit, vor)]
    db.commit()
    return aus


@router.get("/credits/{creditId}/waiting", response_model=list[schemas.CreditWaitingJobOut],
            dependencies=[Depends(_an)])
def waiting(creditId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _guthaben(db, user, creditId)
    aus = []
    for w in _wartend(db, g)[:WARTEND_HOECHSTENS]:
        job, c = w["job"], w["kampagne"]
        sl = abrechnung.ist_aktive_sl(db, user.id, c)  # G14
        runde, doc, a = (w["runde"], w["unterlage"], w["auswertung"]) if sl else (None, None, None)
        grund = w["grund"]
        aus.append(schemas.CreditWaitingJobOut(
            job_id=job.id, job_kind=job.type, campaign_id=c.id, campaign_title=c.title,
            session_id=runde.id if runde else None, session_number=runde.number if runde else None,
            document_id=doc.id if doc else None, document_title=(a.title if a else (doc.title if doc else None)),
            analysis_id=a.id if a else None, estimate_cents=w["schaetzung"],
            reason=abrechnung.GRUND_API[grund], created_at=job.created_at))
    db.commit()
    return aus


@router.post("/credits/{creditId}/waiting/{jobId}/approve", status_code=204, dependencies=[Depends(_an)])
def approve(creditId: str, jobId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    g = _guthaben(db, user, creditId)
    _verwalten(g)
    job = db.get(Job, jobId[:64])
    if job is None or job.type not in abrechnung.KOSTENPFLICHTIG:
        raise errors.not_found()
    z = abrechnung.zahler(db, abrechnung.kampagne_von_auftrag(db, job))
    if z is None or z.id != g.id:
        raise errors.not_found()
    if abrechnung.warte_grund(db, job) != "freigabe":
        raise errors.conflict("wrong_state", "wrong_state.approve")
    abrechnung.freigeben(db, job, user.id, g)
    db.commit()
    return Response(status_code=204)


@router.patch("/credits/{creditId}/settings", response_model=schemas.CreditOut, dependencies=[Depends(_an)])
def credit_settings(creditId: str, body: schemas.CreditSettingsIn, user: User = Depends(current_user),
                    db: Session = Depends(get_db)):
    g = _guthaben(db, user, creditId)
    _verwalten(g)
    abrechnung.einstellungen_aendern(
        db, g, user.id,
        g.freigabe_ab_cent if body.approval_threshold_cents is None else body.approval_threshold_cents,
        g.bogen_seiten if body.sheet_max_pages is None else body.sheet_max_pages,
        g.boegen_je_spieler if body.sheets_per_player is None else body.sheets_per_player)
    db.commit()
    return credit_out(db, g)


@router.post("/credits/{creditId}/vouchers", response_model=schemas.VoucherOut, dependencies=[Depends(_an)])
def voucher(creditId: str, body: schemas.VoucherIn, request: Request, user: User = Depends(current_user),
            db: Session = Depends(get_db)):
    from app.begrenzung import ZAEHLER
    from app.verwaltung.guthaben import GUTSCHEIN_FENSTER, GUTSCHEIN_JE_ADRESSE, GUTSCHEIN_JE_KONTO

    g = _guthaben(db, user, creditId)
    _verwalten(g)
    # dieselben Zähler wie auf der Seite /guthaben (G10)
    versuch = ZAEHLER.reservieren([(f"gutschein-adr:{begrenzung.adresse(request)}", GUTSCHEIN_JE_ADRESSE,
                                    GUTSCHEIN_FENSTER),
                                   (f"gutschein-konto:{user.id}", GUTSCHEIN_JE_KONTO, GUTSCHEIN_FENSTER)])
    if versuch is None:
        raise errors.ApiError(429, "too_many_requests")
    try:
        betrag = abrechnung.gutschein_einloesen(db, g, body.code, user.id)
    except ValueError:
        db.rollback()
        raise errors.bad_request("voucher_invalid") from None
    ZAEHLER.freigeben(versuch)
    db.commit()
    return schemas.VoucherOut(amount_cents=betrag, credit=credit_out(db, g))


# ---------------------------------------------------------------- Zahlende SL der Kampagne
def billing_out(db: Session, c: Campaign, me: Member) -> schemas.CampaignBillingOut | None:
    """Campaign.billing: nur für die SL und nur bei eingeschaltetem Guthaben, sonst None."""
    if me.role != "gm" or not abrechnung.an(db):
        return None
    if abrechnung.modus(db) == "topf" and c.organization_id:
        return schemas.CampaignBillingOut(paid_by="organization", payer_member_id=None, is_me=False,
                                          offered_to_member_id=None)
    sl = {m.user_id: m.id for m in abrechnung.aktive_sl(db, c)}
    zahlt = abrechnung.zahler_user_id(db, c)
    return schemas.CampaignBillingOut(paid_by="gm" if zahlt else "nobody", payer_member_id=sl.get(zahlt),
                                      is_me=zahlt == me.user_id,
                                      offered_to_member_id=sl.get(c.zahler_angebot_user_id) if zahlt else None)


def _kampagne_als_sl(db: Session, campaign_id: str, user: User) -> tuple[Campaign, Member]:
    """Nur bei Abrechnung „jede SL zahlt“ (bzw. Kampagne ohne Organisation) und nur für aktive SL – sonst 404."""
    c = db.get(Campaign, campaign_id[:64])
    me = membership(db, c.id, user) if c is not None else None
    if c is None or me is None or me.role != "gm" or not abrechnung.an(db) or \
            (abrechnung.modus(db) == "topf" and c.organization_id):
        raise errors.not_found("campaign")
    return c, me


@router.post("/campaigns/{campaignId}/payer/offer", response_model=schemas.CampaignBillingOut)
def payer_offer(campaignId: str, body: schemas.PayerOfferIn, user: User = Depends(current_user),
                db: Session = Depends(get_db)):
    c, me = _kampagne_als_sl(db, campaignId, user)
    if abrechnung.zahler_user_id(db, c) != user.id:
        raise errors.not_found("campaign")
    if body.member_id is None:
        if c.zahler_angebot_user_id is not None:
            abrechnung.protokoll(db, user.id, "zahler_angebot_zurueck", kampagne=c.id, an=c.zahler_angebot_user_id)
        c.zahler_angebot_user_id = None
    else:
        ziel = db.get(Member, body.member_id)
        if ziel is None or ziel.campaign_id != c.id or ziel.user_id is None:
            raise errors.bad_request("invalid_member")
        try:
            abrechnung.zahlung_anbieten(db, c, user.id, ziel.user_id)
        except ValueError:
            raise errors.bad_request("invalid_member") from None
    db.commit()
    return billing_out(db, c, me)


@router.post("/campaigns/{campaignId}/payer/accept", response_model=schemas.CampaignBillingOut)
def payer_accept(campaignId: str, user: User = Depends(current_user), db: Session = Depends(get_db)):
    c, me = _kampagne_als_sl(db, campaignId, user)
    try:
        abrechnung.zahlung_uebernehmen(db, c, user.id)
    except ValueError:
        db.rollback()
        raise errors.conflict("wrong_state", "wrong_state.payer") from None
    db.commit()
    return billing_out(db, c, me)


# ---------------------------------------------------------------- ServerInfo.billing
def server_billing(db: Session) -> dict | None:
    """{mode, currency, topUpUrl} – topUpUrl nur aus der eingetragenen öffentlichen Adresse, nie aus der Anfrage, und
    nur https (http im Heimnetz); sonst fehlt sie (G15)."""
    if not abrechnung.an(db):
        return None
    from app.einstellungen import mail_adresse
    from app.routers.auth import oeffentliche_herkunft

    aus = {"mode": "pot" if abrechnung.modus(db) == "topf" else "credit", "currency": "EUR"}
    adresse = mail_adresse(db)
    if adresse and oeffentliche_herkunft(adresse):
        aus["topUpUrl"] = adresse + "/guthaben"
    return aus
