"""Kampagnen-Einstellungen an einem Ort (Schnittstelle 0.4.25).

Ein Prüfweg für alle Änderungen: PATCH /campaigns/{id}/settings, PATCH /campaigns/{id} (ältere Apps) und
POST /campaigns mit `settings` gehen durch `aendern`. Dort stehen die Zustimmung zu den Nutzungsbedingungen beim
Einschalten, das SL-Recht, die Vorgaben des Betreibers (locks), das Ablehnen offener Auswertungen beim Ausschalten,
das Protokoll und die Fassungsnummer.

Vorgaben des Betreibers: Was der Server gar nicht anbietet, ist gesperrt (`unavailable`, Wert false) – Cloud-Kapitel
ohne eingerichtetes Cloud-Sprachmodell, Cloud-Abschrift ohne Anbieter. Eigene Vorgaben (`operator`) gibt es noch nicht.
Eine Sperre greift nur bei einer Änderung: Ein gespeicherter Wert, den die App unverändert zurückschickt, ist kein Fehler.
"""
from __future__ import annotations

import json

from sqlalchemy import select
from sqlalchemy.orm import Session

from app import errors, schemas
from app.db import utcnow
from app.models import Campaign, EinstellungsProtokoll, Member, User

# Name in der Schnittstelle → Spalte der Kampagne
FELDER = {"allowCloudSummary": "allow_cloud_summary", "allowExternalTranscription": "allow_external_transcription",
          "allowCharacterAnalysis": "allow_character_analysis", "playerProposals": "player_proposals"}
# Einschalten nur mit Zustimmung zu den Nutzungsbedingungen (Cloud und Auswertung)
MIT_BEDINGUNGEN = ("allowCloudSummary", "allowExternalTranscription", "allowCharacterAnalysis")
PROTOKOLL_HOECHSTENS = 100


def sperren(db: Session) -> dict[str, tuple[bool, str]]:
    """Feld → (fester Wert, Grund)."""
    from app.einstellungen import llm_konfig
    from app.extern import anbieter
    from app.services import cloud_anbieter

    aus: dict[str, tuple[bool, str]] = {}
    k = llm_konfig(db)
    if not (k.art == "api" and k.api_key and cloud_anbieter(k)):
        aus["allowCloudSummary"] = (False, "unavailable")
    if not anbieter(db):
        aus["allowExternalTranscription"] = (False, "unavailable")
    return aus


def aendern(db: Session, c: Campaign, me: Member, user: User, werte: dict, *,
            fassung: int | None = None, neu_angelegt: bool = False) -> bool:
    """Einstellungen ändern (der einzige Weg). werte: Schnittstellen-Name → neuer Wert (None = nicht ändern).
    fassung: erwartete Fassung (PATCH …/settings), sonst ohne Prüfung. neu_angelegt: beim Anlegen – ohne Protokoll und
    ohne Hochzählen (die Kampagne beginnt mit Fassung 1). Gibt zurück, ob sich etwas geändert hat."""
    from app.access import require_gm, terms_verlangen

    require_gm(me)
    from app import spielende

    arten = werte.get("counterKinds")
    arten_neu = spielende.arten_pruefen(arten) if arten is not None else None  # 400 vor allem anderen
    werte = {k: v for k, v in werte.items() if k in FELDER and v is not None}
    if fassung is not None and fassung != (c.settings_version or 1):
        raise errors.ApiError(409, "settings_conflict", details={"current": lesen(db, c, me).model_dump(
            mode="json", by_alias=True)})
    if any(v for k, v in werte.items() if k in MIT_BEDINGUNGEN):  # Einschalten nur mit Zustimmung
        terms_verlangen(user, db)
    gesperrt = sperren(db)
    aenderungen = {}
    for feld, wert in werte.items():
        alt = bool(getattr(c, FELDER[feld]))
        if wert == alt and not neu_angelegt:
            continue
        if feld in gesperrt and wert != gesperrt[feld][0]:
            raise errors.ApiError(400, "setting_locked", details={"field": feld})
        if wert != alt:
            aenderungen[feld] = (alt, wert)
    arten_alt = spielende.arten(c)
    if arten_neu is not None and arten_neu != arten_alt:
        # 0.4.28: im Protokoll als Anzahl (oldValue/newValue sind Zahl oder Wahrheitswert)
        aenderungen["counterKinds"] = (len(arten_alt), len(arten_neu))
    if not aenderungen:
        return False
    if aenderungen.get("allowCharacterAnalysis") == (True, False):
        from app import charakterauswertung

        charakterauswertung.abgeschaltet(db, c)  # offene Anfragen gelten als abgelehnt
    for feld, (_alt, neu) in aenderungen.items():
        if feld in FELDER:
            setattr(c, FELDER[feld], neu)
    if "counterKinds" in aenderungen:
        spielende.arten_setzen(db, c, arten_neu)  # entfernte Arten verwerfen ihre Stände (dieselbe Transaktion)
    if neu_angelegt:
        return True
    c.settings_version = (c.settings_version or 1) + 1
    c.settings_updated_at, c.settings_updated_by = utcnow(), me.id
    for feld, (alt, neu) in aenderungen.items():
        db.add(EinstellungsProtokoll(campaign_id=c.id, feld=feld, alt=json.dumps(alt), neu=json.dumps(neu),
                                     fassung=c.settings_version, von_member_id=me.id))
    return True


def lesen(db: Session, c: Campaign, me: Member) -> schemas.CampaignSettingsOut:
    from app.services import _bogen_grenzen

    return schemas.CampaignSettingsOut(
        version=c.settings_version or 1, allow_cloud_summary=bool(c.allow_cloud_summary),
        allow_external_transcription=bool(c.allow_external_transcription),
        allow_character_analysis=bool(c.allow_character_analysis), character_sheet_limits=_bogen_grenzen(db, c),
        player_proposals=c.player_proposals is not False, counter_kinds=spielende_arten(c),
        locks=[schemas.SettingLockOut(field=f, value=w, reason=g) for f, (w, g) in sperren(db).items()],
        updated_at=c.settings_updated_at or c.created_at,
        updated_by_member_id=c.settings_updated_by if me.role == "gm" else None)


def spielende_arten(c: Campaign) -> list[schemas.CounterKind]:
    from app import spielende

    return [schemas.CounterKind(**a) for a in spielende.arten(c)]


def _wert(roh: str | None):
    try:
        return json.loads(roh) if roh is not None else None
    except ValueError:
        return None


def protokoll(db: Session, c: Campaign) -> list[schemas.CampaignSettingsLogOut]:
    zeilen = db.scalars(select(EinstellungsProtokoll).where(EinstellungsProtokoll.campaign_id == c.id)
                        .order_by(EinstellungsProtokoll.angelegt.desc(), EinstellungsProtokoll.fassung.desc())
                        .limit(PROTOKOLL_HOECHSTENS))
    return [schemas.CampaignSettingsLogOut(at=z.angelegt, field=z.feld, old_value=_wert(z.alt), new_value=_wert(z.neu),
                                           version=z.fassung, by_member_id=z.von_member_id,
                                           by_operator=bool(z.vom_betreiber)) for z in zeilen]
