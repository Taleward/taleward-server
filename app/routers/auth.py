"""Anmeldung, Serverinfo, eigenes Konto, Organisationen."""
from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app import begrenzung, errors, mail, schemas, slfreigabe
from app.links import betreiber_link
from app.access import current_user
from app.einstellungen import angaben, mail_adresse
from app.extern import anbieter
from app.konto import registrierung
from app.db import get_db, server_id, utcnow
from app.models import AuthMethod, Organization, OrgMember, User
from app.security import create_token, verify_password
from app.services import user_out

API_VERSION = "0.4.26"

router = APIRouter(tags=["Auth"])


@router.get("/info", response_model=schemas.ServerInfoOut)
def info(request: Request, db: Session = Depends(get_db)):
    from app import aktualisierung, anmeldedienste, aufbewahrung, mail, nutzungsbedingungen
    from app.einstellungen import oeffentliche_adresse
    from app.einrichtung import betriebsart
    from app import cloudanbieter
    from app.einstellungen import llm_konfig
    from app.routers.guthaben import server_billing
    from app.services import cloud_anbieter

    a = angaben(db)  # .env, überschreibbar in der Verwaltung
    basis = oeffentliche_adresse(db, request)
    extern = anbieter(db)
    k = llm_konfig(db)
    dienste = anmeldedienste.eingerichtet(db)
    # Neueste App: von Hand in der Verwaltung eingetragen, sonst die freigegebene Fassung aus den Updates
    # (Datei liegt auf diesem Server – die App fragt nie direkt bei GitHub)
    neu = None if a.app_latest_version else aktualisierung.angebot(db, "app", oeffentliche_adresse(db, request))
    cloud = cloud_anbieter(k) if k.art == "api" and k.api_key else None
    return schemas.ServerInfoOut(
        name=a.server_name, operator=a.server_operator, contact=a.server_contact, api_version=API_VERSION,
        registration=registrierung(db), gm_approval=slfreigabe.gm_approval(db), auth_methods=["password"] + (["oidc"] if dienste else []),
        privacy_policy_url=a.privacy_policy_url if betreiber_link(a.privacy_policy_url, nur_https=False) else None,
        min_age=a.min_age, external_transcription=extern, min_app_version=a.app_min_version,
        latest_app_version=a.app_latest_version or (neu and neu["version"]),
        app_download_url=a.app_download_url or (neu and neu["url"]),
        release_notes=a.app_release_notes or (neu and neu["notes"]),
        app_download_sha256=neu and neu.get("sha256"), app_download_size_bytes=neu and neu.get("sizeBytes"),
        external_transcription_mode=(("primary" if betriebsart(db) == "cloud" else "fallback") if extern else None),
        cloud_summary=cloud,
        cloud_summary_info=cloudanbieter.info_fuer(cloud, k.api_url),
        external_transcription_info=cloudanbieter.info_fuer(extern),
        auth_providers=[schemas.AuthProviderOut(id=d, name=anmeldedienste.DIENSTE[d]["name"]) for d in dienste],
        password_reset=mail.kann_senden(db) and mail_adresse(db) is not None,
        audio_retention=schemas.AudioRetentionOut(**aufbewahrung.lesen(db).api()),
        public_url=oeffentliche_herkunft(a.public_url), server_id=server_id(),
        # 0.4.22: Nutzungsbedingungen (immer) und Impressum (Vorlage, Link oder null) dieses Servers
        terms_url=nutzungsbedingungen.adresse(db, basis), terms_version=nutzungsbedingungen.version(db),
        legal_notice_url=nutzungsbedingungen.impressum_adresse(db, basis),
        billing=server_billing(db),  # 0.4.25
    )


def oeffentliche_herkunft(url: str | None) -> str | None:
    """0.4.16: eingetragene öffentliche Adresse als Herkunft (Schema, Host, Port). http nur für private Adressen –
    sonst null, die App bleibt dann bei ihrer eigenen Adresse."""
    import ipaddress
    from urllib.parse import urlsplit

    if not url:
        return None
    try:
        teile = urlsplit(url.strip())
        host, port = teile.hostname, teile.port
    except ValueError:
        return None
    if teile.scheme not in ("http", "https") or not host or teile.username or teile.password:
        return None
    if teile.scheme == "http":
        try:
            if not ipaddress.ip_address(host).is_private:
                return None
        except ValueError:
            if not (host == "localhost" or host.endswith((".local", ".lan", ".home.arpa", ".fritz.box"))):
                return None
    netz = f"[{host}]" if ":" in host else host
    return f"{teile.scheme}://{netz}" + (f":{port}" if port else "")


LOGIN_FENSTER = 15 * 60
LOGIN_JE_ADRESSE, LOGIN_JE_NAME = 30, 10  # Fehlversuche im Fenster


@router.post("/auth/login", response_model=schemas.LoginResponse)
def login(body: schemas.LoginRequest, request: Request, db: Session = Depends(get_db)):
    from app.begrenzung import ZAEHLER

    name = body.username.strip().lower()
    adresse = begrenzung.adresse(request)
    # 0.4.62: vor dem Passwortvergleich zählen (Adresse vor Name), bei Erfolg zurücknehmen
    versuch = ZAEHLER.reservieren([(f"login-adr:{adresse}", LOGIN_JE_ADRESSE, LOGIN_FENSTER),
                                   (f"login-name:{name}", LOGIN_JE_NAME, LOGIN_FENSTER)])
    if versuch is None:
        raise errors.ApiError(429, "too_many_requests")  # 0.4.9 (vorher 401 mit demselben code)
    user = db.scalar(select(User).where(User.username == name))
    methode = None
    if user is not None:
        methode = db.scalar(select(AuthMethod).where(AuthMethod.user_id == user.id, AuthMethod.kind == "password"))
    if not verify_password(body.password, methode.secret if methode else None):
        raise errors.BAD_CREDENTIALS
    ZAEHLER.freigeben(versuch)
    if user.setup_account:  # admin/admin: nur für die Ersteinrichtung in der Verwaltung
        raise errors.ApiError(401, "setup_account_only")
    methode.last_used_at = utcnow()
    mail.sprache_merken(user, request)
    from app import konto_mail

    konto_mail.vormerken(db, user, "anmeldung", ort=konto_mail.ort_aus(request))
    db.commit()
    token, expires = create_token(user.id, user.token_version, server_id())
    return schemas.LoginResponse(access_token=token, expires_at=expires, user=user_out(user))


@router.post("/auth/register", status_code=201, response_model=schemas.LoginResponse)
def register(body: schemas.RegisterRequest, request: Request, db: Session = Depends(get_db)):
    """Konto anlegen und gleich anmelden (Einladung, Registrierungscode oder offen, 0.4.23). Beitreten macht die App
    danach mit /campaigns/join."""
    from app.konto import registrieren

    adresse = begrenzung.adresse(request)
    if body.registration_token is not None:  # 0.4.0: nach Anmeldung mit einem Dienst
        from app.routers.anmeldung import registrieren_mit_dienst

        u = registrieren_mit_dienst(db, adresse, body)
    else:
        if body.username is None or body.password is None:
            raise errors.bad_request("validation_error", "validation_error.empty", field="username/password")
        u = registrieren(db, adresse, body.invite_code, body.username, body.display_name, body.password,
                         body.accept_privacy, body.age_confirmed, accept_terms=body.accept_terms,
                         terms_version=body.terms_version, registration_code=body.registration_code)
    mail.sprache_merken(u, request)
    db.commit()
    token, expires = create_token(u.id, u.token_version, server_id())
    return schemas.LoginResponse(access_token=token, expires_at=expires, user=user_out(u))


@router.get("/me", response_model=schemas.UserOut)
def me(user: User = Depends(current_user)):
    return user_out(user)


@router.put("/me/terms", response_model=schemas.UserOut)
def terms_zustimmen(body: schemas.TermsAcceptIn, user: User = Depends(current_user), db: Session = Depends(get_db)):
    """0.4.22: Einer neuen Fassung der Nutzungsbedingungen zustimmen. version muss die geltende sein (409
    terms_changed); eine Zustimmung lässt sich nicht einzeln zurücknehmen (dafür: Konto löschen)."""
    from app import nutzungsbedingungen

    if body.accept is not True:
        raise errors.bad_request("consent_required", "consent_required.terms")
    if body.version != nutzungsbedingungen.version(db):
        raise errors.conflict("terms_changed")
    if (user.terms_accepted_version or 0) < body.version:
        user.terms_accepted_version, user.terms_accepted_at = body.version, utcnow()
        db.commit()
        db.refresh(user)
    return user_out(user)


@router.post("/me/gm-request", response_model=schemas.UserOut, responses={202: {"model": schemas.UserOut}})
def sl_freigabe_anfragen(request: Request, response: Response, user: User = Depends(current_user),
                         db: Session = Depends(get_db)):
    """0.4.23: SL-Freigabe anfragen (app/slfreigabe.py). 202 = Anfrage liegt vor, 200 = nichts zu tun."""
    from app.einstellungen import oeffentliche_adresse

    response.status_code = slfreigabe.anfragen(db, user, errors.sprache(request), oeffentliche_adresse(db, request))
    db.commit()
    db.refresh(user)
    return user_out(user)


@router.get("/me/export")
def export(request: Request, user: User = Depends(current_user), db: Session = Depends(get_db)):
    import json

    from app.konto import exportieren

    daten = exportieren(db, user, str(request.base_url).rstrip("/"))
    name = f"taleward-export-{user.username}-{utcnow():%Y%m%d}.json"
    return Response(json.dumps(daten, ensure_ascii=False, indent=2), media_type="application/json",
                    headers={"Content-Disposition": f'attachment; filename="{name}"', "Cache-Control": "no-store"})


@router.delete("/me", status_code=204)
def delete_me(body: schemas.DeleteMeRequest, user: User = Depends(current_user), db: Session = Depends(get_db)):
    from app.konto import loeschen

    loeschen(db, user, body.password, body.confirm_username)
    db.commit()
    return Response(status_code=204)


@router.get("/organizations", tags=["Kampagnen"], response_model=list[schemas.OrganizationOut])
def organizations(user: User = Depends(current_user), db: Session = Depends(get_db)):
    rows = db.execute(
        select(Organization, OrgMember.role).join(OrgMember, OrgMember.organization_id == Organization.id)
        .where(OrgMember.user_id == user.id).order_by(Organization.name)
    ).all()
    return [schemas.OrganizationOut(id=o.id, name=o.name, my_role=role) for o, role in rows]
