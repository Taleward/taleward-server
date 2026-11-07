"""Discord-Bot (Schnittstelle 0.4.32, S63 mit B1–B13).

Ein Bot nimmt Online-Runden im Sprachkanal je Person als eigene Spur auf. Hier liegt alles, was der Server dafür
prüft; die Wege stehen in app/routers/discord.py.

Verbindlich:
- Alles standardmäßig aus (Schalter `discord.bot` in der Verwaltung). Aus → alle Bot-Wege 404.
- Bot-Schlüssel (B1): mindestens 32 Byte Zufall, gespeichert nur als SHA-256, einmal angezeigt, widerrufbar; nur
  `/bot/…` und der Upload eigener Runden. Erzeugen und Widerrufen nur in der Verwaltung mit frischem zweitem Faktor.
- Verbindung Kampagne ↔ Gilde nur über einen Link bzw. Drei-Wörter-Code vom Bot (B3: ≥ 128 Bit bzw. drei Wörter aus
  WOERTER, nur Prüfsumme, einmalig, 15 Minuten), eingelöst von der SL mit genau der Discord-ID des Befehls (B4, frisch
  geprüft). Die Verbindung gehört dem Schlüssel, der den Link ausgestellt hat (Y4), und gilt nur, solange die
  verbindende Person aktive SL ist und ihr Konto noch mit dieser Discord-ID verknüpft ist (B5) – sonst löst sie sich.
- Bereitschaft je Discord-ID nur ready | consent_required | link_required, ohne Namen (B11).
- Gäste stimmen nur über einen eigenen Link für diese Runde zu (consentSource link, B6); ihre Discord-ID liegt nur als
  Prüfsumme an der Runde und nur, bis die Aufnahme hochgeladen ist (B7).
- Widerruf (B8): ein Mitglied, das in der App widerruft, ist sofort nicht mehr ready, verliert seine Spur samt
  angekommener Teile; ein Gast über DELETE …/tracks.
- Mengen (B9, B10): 12 Spuren, 6 Stunden, 10 Bot-Runden je Kampagne und Tag, Grenzen je Schlüssel und Kampagne.
- Protokoll je Kampagne (B12) ohne Audio und ohne Anzeigenamen von Gästen.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from datetime import datetime, timedelta, timezone

from sqlalchemy import exists, func, select, update
from sqlalchemy.orm import Session

from app import aliasse, errors
from app.db import utcnow
from app.models import (Attendee, AuthMethod, BotKey, Campaign, Comment, ConsentLink, DiscordConnection, DiscordLink,
                        DiscordProtokoll, GameSession, GmNote, Member, Upload, UploadFile, User)

SCHALTER = "discord.bot"
LINK_MINUTEN = 15
LINKS_JE_STUNDE = 10  # je Discord-ID
ZUSTIMMUNGSLINKS_JE_RUNDE = 5  # je Discord-ID und Runde
BEREITSCHAFT_JE_STUNDE = 120  # je Kampagne
BEREITSCHAFT_IDS = 25
RUNDEN_JE_TAG = 10  # je Kampagne
MAX_SPUREN = 12
MAX_STUNDEN = 6
OHNE_TON_STUNDEN = 30
SCHLUESSEL_JE_MINUTE = 600  # Anfragen je Bot-Schlüssel (Sicherung gegen Schleifen)
SNOWFLAKE = re.compile(r"^[0-9]{15,21}$")

# Drei-Wörter-Code: 32 × 32 = 1024 Wörter (Zusammensetzungen), fest im Server, Zufall über `secrets`.
_VORN = ("wald", "moor", "fels", "stern", "mond", "sonnen", "wind", "regen", "nebel", "eichen", "birken", "tannen",
         "see", "fluss", "quell", "berg", "tal", "heide", "feuer", "eis", "schnee", "rosen", "linden", "apfel", "hasel",
         "falken", "raben", "fuchs", "otter", "kiesel", "silber", "kupfer")
_HINTEN = ("krug", "turm", "feld", "stein", "weg", "tor", "haus", "hof", "brunnen", "garten", "laterne", "mantel",
           "faden", "korb", "kranz", "pfad", "steg", "bogen", "glocke", "horn", "kerze", "buch", "karte", "ring",
           "stab", "schild", "kelch", "rad", "segel", "anker", "feder", "schale")
WOERTER = tuple(v + h for v in _VORN for h in _HINTEN)


def an(db: Session) -> bool:
    from app.einstellungen import meta_lesen

    return meta_lesen(db, SCHALTER) == "1"


def schalten(db: Session, wert: bool) -> None:
    from app.einstellungen import meta_schreiben

    meta_schreiben(db, SCHALTER, "1" if wert else "0")


def _z(t: datetime | None) -> str | None:
    if t is None:
        return None
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t.isoformat().replace("+00:00", "Z")


def _utc(t: datetime | None) -> datetime | None:
    if t is not None and t.tzinfo is None:
        return t.replace(tzinfo=timezone.utc)
    return t


def _hash(wert: str) -> str:
    return hashlib.sha256(wert.encode()).hexdigest()


def id_hash(discord_id: str) -> str:
    """Prüfsumme einer Discord-ID (Gäste, B7) – mit eigenem Präfix, damit sie nicht mit Geheimnissen verwechselt wird."""
    return _hash("discord-id:" + discord_id)


def code_normal(code: str) -> str:
    return re.sub(r"[^a-zäöüß]", "", (code or "").casefold())[:64]


def _snowflake(wert: str) -> str:
    if not SNOWFLAKE.fullmatch(wert or ""):
        raise errors.bad_request("validation_error")
    return wert


def protokoll(db: Session, campaign_id: str, was: str, **daten) -> None:
    db.add(DiscordProtokoll(campaign_id=campaign_id, was=was, daten=json.dumps(daten, ensure_ascii=False) if daten
                            else None))


# 0.4.33 (Server 0.4.158): Verlauf für die SL – interne Namen → Arten der Schnittstelle. Unbekanntes bleibt draußen.
VERLAUF_ARTEN = {
    "verbunden": "connected", "verbindung_geloest": "disconnected", "verbindung_geloest_selbst": "disconnected_auto",
    "verbindung_ersetzt": "replaced", "aufnahme_gestartet": "recording_started", "aufnahme_beendet": "recording_stopped",
    "aufnahme_verworfen": "recording_discarded", "spur_verworfen": "track_discarded", "spur_widerrufen": "track_revoked",
    "zustimmung_mitglied": "consent_member", "zustimmung_gast": "consent_guest",
}
VERLAUF_HOECHSTENS = 200
VERLAUF_TAGE = 365  # Wartung löscht ältere Einträge (Sicherheits-Chat 08.11.)


def verlauf(db: Session, c: Campaign) -> list[dict]:
    """Neueste zuerst, höchstens 200; nur Zeitpunkt, Art und ggf. die Runde – nie Namen, Kennungen oder Mitglieds-IDs."""
    aus: list[dict] = []
    for z in db.scalars(select(DiscordProtokoll).where(DiscordProtokoll.campaign_id == c.id,
                                                       DiscordProtokoll.was.in_(VERLAUF_ARTEN))
                        .order_by(DiscordProtokoll.at.desc(), DiscordProtokoll.id.desc()).limit(VERLAUF_HOECHSTENS)):
        eintrag = {"at": _z(z.at), "kind": VERLAUF_ARTEN[z.was]}
        try:
            runde = (json.loads(z.daten) if z.daten else {}).get("runde")
        except (ValueError, AttributeError):
            runde = None
        if isinstance(runde, str):
            s = db.get(GameSession, runde)
            if s is not None and s.campaign_id == c.id:  # die SL sieht jede Runde ihrer Kampagne
                eintrag["sessionId"] = s.id
        aus.append(eintrag)
    return aus


# ---------------------------------------------------------------- Bot-Schlüssel (B1)
def schluessel_erzeugen(db: Session, name: str, von: str) -> tuple[str, BotKey]:
    klartext = "twb_" + secrets.token_urlsafe(32)
    k = BotKey(name=aliasse.zeichen_bereinigen(name)[:80] or "Discord-Bot", hash=_hash(klartext), created_by=von)
    db.add(k)
    db.flush()
    return klartext, k


def schluessel_pruefen(db: Session, roh: str | None) -> BotKey | None:
    if not roh or len(roh) > 200:
        return None
    h = _hash(roh)
    k = db.scalar(select(BotKey).where(BotKey.hash == h, BotKey.revoked_at.is_(None)))
    if k is None or not hmac.compare_digest(k.hash, h):
        return None
    return k


def schluessel_widerrufen(db: Session, k: BotKey) -> None:
    k.revoked_at = utcnow()


def schluessel_liste(db: Session) -> list[BotKey]:
    return list(db.scalars(select(BotKey).order_by(BotKey.created_at.desc())))


# ---------------------------------------------------------------- Verknüpfung Konto ↔ Discord
def discord_id_von(db: Session, user_id: str) -> str | None:
    return db.scalar(select(AuthMethod.secret).where(AuthMethod.user_id == user_id, AuthMethod.kind == "oidc:discord"))


def konto_zu(db: Session, discord_id: str) -> User | None:
    m = db.scalar(select(AuthMethod).where(AuthMethod.kind == "oidc:discord", AuthMethod.secret == discord_id))
    return db.get(User, m.user_id) if m else None


# ---------------------------------------------------------------- Verbindung Kampagne ↔ Gilde
def verbindung(db: Session, c: Campaign | str) -> DiscordConnection | None:
    """Gültige Verbindung oder None. Löst sie, wenn die verbindende Person nicht mehr aktive SL ist, ihr Konto nicht
    mehr mit der Discord-ID verknüpft ist (B5) oder der Schlüssel widerrufen wurde (dann ruht sie: Y4)."""
    cid = c if isinstance(c, str) else c.id
    v = db.get(DiscordConnection, cid)
    if v is None:
        return None
    m = db.get(Member, v.connected_by_member_id) if v.connected_by_member_id else None
    gueltig = (m is not None and m.campaign_id == cid and m.role == "gm" and m.left_at is None and m.user_id
               and discord_id_von(db, m.user_id) == v.discord_user_id)
    if not gueltig:
        loesen(db, v, "verbindung_geloest_selbst")
        return None
    k = db.get(BotKey, v.bot_key_id)
    if k is None or k.revoked_at is not None:
        return None  # ruht, bis die SL neu verbindet
    return v


def loesen(db: Session, v: DiscordConnection, was: str = "verbindung_geloest") -> None:
    """Verbindung lösen; laufende Bot-Runden der Kampagne enden (der Bot erfährt es über sessionState)."""
    for s in db.scalars(select(GameSession).where(GameSession.campaign_id == v.campaign_id,
                                                  GameSession.state == "recording")):
        runde_beenden(db, s)
    protokoll(db, v.campaign_id, was)
    db.delete(v)
    db.flush()


def verbindung_out(db: Session, v: DiscordConnection) -> dict:
    return {"guildName": v.guild_name, "connectedAt": _z(v.connected_at), "connectedBy": v.connected_by_member_id}


# ---------------------------------------------------------------- Verbindungslinks (Bot → SL)
def link_anlegen(db: Session, k: BotKey, guild_id: str, discord_user_id: str, guild_name: str,
                 basis: str) -> dict:
    from app.begrenzung import ZAEHLER

    guild_id, discord_user_id = _snowflake(guild_id), _snowflake(discord_user_id)
    if not ZAEHLER.versuch(f"discord-link:{discord_user_id}", LINKS_JE_STUNDE, 3600):
        raise errors.ApiError(429, "too_many_requests")
    # ein neuer Link macht den alten ungültig
    db.execute(DiscordLink.__table__.delete().where(DiscordLink.discord_user_id == discord_user_id,
                                                    DiscordLink.guild_id == guild_id))
    token = secrets.token_urlsafe(24)  # 192 Bit
    code = "-".join(secrets.choice(WOERTER) for _ in range(3))
    bis = utcnow() + timedelta(minutes=LINK_MINUTEN)
    db.add(DiscordLink(token_hash=_hash(token), code_hash=_hash(code_normal(code)), bot_key_id=k.id,
                       guild_id=guild_id, guild_name=aliasse.zeichen_bereinigen(guild_name)[:100] or "Discord",
                       discord_user_id=discord_user_id, expires_at=bis))
    return {"url": f"{basis.rstrip('/')}/discord/{token}", "code": code, "expiresAt": _z(bis)}


def _link_finden(db: Session, token: str | None, code: str | None) -> DiscordLink:
    if bool(token) == bool(code):
        raise errors.bad_request("validation_error")
    h = _hash(token) if token else _hash(code_normal(code or ""))
    spalte = DiscordLink.token_hash if token else DiscordLink.code_hash
    link = db.scalar(select(DiscordLink).where(spalte == h, DiscordLink.used_at.is_(None)))
    if link is None or _utc(link.expires_at) < utcnow():
        raise errors.not_found()
    k = db.get(BotKey, link.bot_key_id)
    if k is None or k.revoked_at is not None:
        raise errors.not_found()
    return link


def link_versuch(request, user: User) -> None:
    from app import begrenzung
    from app.begrenzung import ZAEHLER

    if ZAEHLER.reservieren([(f"discord-einloesen:{user.id}", 10, 900),
                            (f"discord-einloesen-adr:{begrenzung.adresse(request)}", 30, 900)]) is None:
        raise errors.ApiError(429, "too_many_requests")


def link_vorschau(db: Session, user: User, token: str | None, code: str | None) -> dict:
    link = _link_finden(db, token, code)
    if discord_id_von(db, user.id) != link.discord_user_id:
        return {"linked": False}
    kampagnen = []
    for m in db.scalars(select(Member).where(Member.user_id == user.id, Member.role == "gm", Member.left_at.is_(None))):
        c = db.get(Campaign, m.campaign_id)
        if c is None or c.archived_at is not None:
            continue
        v = verbindung(db, c)
        kampagnen.append({"campaignId": c.id, "title": c.title, "connectedGuildName": v.guild_name if v else None})
    kampagnen.sort(key=lambda x: x["title"].casefold())
    return {"linked": True, "guildName": link.guild_name, "expiresAt": _z(link.expires_at), "campaigns": kampagnen}


def link_einloesen(db: Session, user: User, token: str | None, code: str | None, campaign_id: str) -> DiscordConnection:
    link = _link_finden(db, token, code)
    m = db.scalar(select(Member).where(Member.campaign_id == campaign_id, Member.user_id == user.id,
                                       Member.left_at.is_(None)))
    if m is None or m.role != "gm":
        raise errors.not_found("campaign")
    if discord_id_von(db, user.id) != link.discord_user_id:  # B4: frisch prüfen
        raise errors.conflict("discord_link_required")
    # einmalig und atomar
    n = db.execute(update(DiscordLink).where(DiscordLink.id == link.id, DiscordLink.used_at.is_(None))
                   .values(used_at=utcnow())).rowcount
    if n != 1:
        raise errors.not_found()
    alt = db.get(DiscordConnection, campaign_id)
    if alt is not None:
        loesen(db, alt, "verbindung_ersetzt")
    v = DiscordConnection(campaign_id=campaign_id, guild_id=link.guild_id, guild_name=link.guild_name,
                          bot_key_id=link.bot_key_id, discord_user_id=link.discord_user_id,
                          connected_by_member_id=m.id, connected_at=utcnow())
    db.add(v)
    protokoll(db, campaign_id, "verbunden", von=m.id)
    db.flush()
    return v


# ---------------------------------------------------------------- Bot-Wege: Prüfungen
def _kampagne_fuer_bot(db: Session, k: BotKey, campaign_id: str) -> tuple[Campaign, DiscordConnection]:
    c = db.get(Campaign, campaign_id)
    v = verbindung(db, c) if c is not None else None
    if v is None or v.bot_key_id != k.id or c.archived_at is not None:
        raise errors.not_found("campaign")
    return c, v


def _runde_fuer_bot(db: Session, k: BotKey, session_id: str) -> tuple[GameSession, Campaign, DiscordConnection]:
    s = db.get(GameSession, session_id)
    if s is None or s.bot_key_id != k.id:
        raise errors.not_found("session")
    c, v = _kampagne_fuer_bot(db, k, s.campaign_id)
    return s, c, v


def kampagnen_der_gilde(db: Session, k: BotKey, guild_id: str, discord_user_id: str) -> list[dict]:
    guild_id, discord_user_id = _snowflake(guild_id), _snowflake(discord_user_id)
    aus = []
    for v in list(db.scalars(select(DiscordConnection).where(DiscordConnection.guild_id == guild_id,
                                                             DiscordConnection.discord_user_id == discord_user_id,
                                                             DiscordConnection.bot_key_id == k.id))):
        if verbindung(db, v.campaign_id) is None:
            continue
        c = db.get(Campaign, v.campaign_id)
        if c is None or c.archived_at is not None:
            continue
        laufend = db.scalar(select(GameSession.id).where(GameSession.campaign_id == c.id,
                                                         GameSession.state == "recording",
                                                         GameSession.bot_key_id == k.id).limit(1))
        aus.append({"campaignId": c.id, "title": c.title, "language": c.language or "de",
                    "pauseOnUnconsented": pause_bei_fremden(c), "liveSessionId": laufend})
    return sorted(aus, key=lambda x: x["title"].casefold())


def pause_bei_fremden(c: Campaign) -> bool:
    return bool(c.discord_pause)


def bot_loesen(db: Session, k: BotKey, guild_id: str, campaign_id: str, discord_user_id: str) -> None:
    _snowflake(guild_id), _snowflake(discord_user_id)
    _c, v = _kampagne_fuer_bot(db, k, campaign_id)
    if v.guild_id != guild_id or v.discord_user_id != discord_user_id:
        raise errors.not_found("campaign")
    loesen(db, v)


# ---------------------------------------------------------------- Bereitschaft
def _mitglied_zu(db: Session, c: Campaign, discord_id: str) -> Member | None:
    u = konto_zu(db, discord_id)
    if u is None:
        return None
    return db.scalar(select(Member).where(Member.campaign_id == c.id, Member.user_id == u.id,
                                          Member.left_at.is_(None)))


def _anwesend(db: Session, s: GameSession, m: Member | None, discord_id: str) -> Attendee | None:
    if m is not None:
        return db.scalar(select(Attendee).where(Attendee.session_id == s.id, Attendee.member_id == m.id))
    return db.scalar(select(Attendee).where(Attendee.session_id == s.id, Attendee.discord_hash == id_hash(discord_id),
                                            Attendee.consent_source == "link"))


def zustand(db: Session, c: Campaign, s: GameSession | None, discord_id: str) -> tuple[str, Attendee | None]:
    m = _mitglied_zu(db, c, discord_id)
    if m is not None:
        if m.recording_consent_at is None:
            return "consent_required", None
        a = _anwesend(db, s, m, discord_id) if s is not None else None
        return "ready", a if a is not None and a.mit_spur else None
    if s is not None and s.state == "recording":
        a = _anwesend(db, s, None, discord_id)
        if a is not None and a.consent:
            return "ready", a if a.mit_spur else None
    return "link_required", None


def bereitschaft(db: Session, k: BotKey, campaign_id: str, ids: list[str], session_id: str | None) -> dict:
    from app.begrenzung import ZAEHLER

    c, _v = _kampagne_fuer_bot(db, k, campaign_id)
    if not ids or len(ids) > BEREITSCHAFT_IDS:
        raise errors.bad_request("validation_error")
    for i in ids:
        _snowflake(i)
    if not ZAEHLER.versuch(f"discord-bereit:{c.id}", BEREITSCHAFT_JE_STUNDE, 3600):
        raise errors.ApiError(429, "too_many_requests")
    s = None
    if session_id:
        s = db.get(GameSession, session_id[:64])
        if s is None or s.campaign_id != c.id or s.bot_key_id != k.id:
            s = None
    zustaende = {}
    for i in dict.fromkeys(ids):
        z, a = zustand(db, c, s, i)
        zustaende[i] = {"state": z, "attendeeId": a.id if a is not None else None}
    return {"states": zustaende, "sessionState": s.state if s is not None else None}


# ---------------------------------------------------------------- Bot-Runden
def runde_anlegen(db: Session, k: BotKey, campaign_id: str, guild_id: str, discord_user_id: str,
                  started_at: datetime) -> GameSession:
    from app.services import next_session_number, set_state

    c, v = _kampagne_fuer_bot(db, k, campaign_id)
    if v.guild_id != guild_id or v.discord_user_id != discord_user_id:
        raise errors.not_found("campaign")
    if db.scalar(select(GameSession.id).where(GameSession.campaign_id == c.id, GameSession.state == "recording")
                 .limit(1)):
        raise errors.conflict("already_recording")
    heute = utcnow() - timedelta(hours=24)
    if (db.scalar(select(func.count()).select_from(GameSession).where(
            GameSession.campaign_id == c.id, GameSession.bot_key_id.is_not(None),
            GameSession.created_at >= heute)) or 0) >= RUNDEN_JE_TAG:
        raise errors.ApiError(429, "too_many_requests")
    start = _utc(started_at)
    if abs((start - utcnow()).total_seconds()) > 3600:
        raise errors.bad_request("validation_error")
    s = GameSession(campaign_id=c.id, number=next_session_number(db, c.id), played_at=start, state="recording",
                    source="discord", bot_key_id=k.id, recording_started_at=start, interrupted=False)
    set_state(s, "recording")
    db.add(s)
    db.flush()
    protokoll(db, c.id, "aufnahme_gestartet", runde=s.id)
    return s


def runde_beenden(db: Session, s: GameSession) -> None:
    """Bot-Runde ohne Ton: no_recording (Notizen bleiben, Hochladen nur noch von Hand durch die SL)."""
    from app.services import set_state

    set_state(s, "no_recording")
    gaeste_vergessen(db, s)


def _hat_inhalt(db: Session, s: GameSession) -> bool:
    return bool(db.scalar(select(exists().where(GmNote.session_id == s.id, func.length(GmNote.text) > 0)))
                or db.scalar(select(exists().where(Comment.session_id == s.id))))


def ereignis(db: Session, k: BotKey, session_id: str, typ: str, at: datetime) -> None:
    s, c, _v = _runde_fuer_bot(db, k, session_id)
    start = _utc(s.recording_started_at or s.played_at)
    at = _utc(at)
    if not (start - timedelta(minutes=1) <= at <= start + timedelta(hours=MAX_STUNDEN)):
        raise errors.bad_request("validation_error")
    if s.state not in ("recording",):
        raise errors.conflict("wrong_state", "wrong_state.bot")
    ereignisse = json.loads(s.bot_ereignisse or "[]")
    ereignisse.append({"type": typ, "at": _z(at)})
    s.bot_ereignisse = json.dumps(ereignisse[-500:])
    if typ == "interrupted":
        s.interrupted = True
    elif typ == "stopped":
        s.bot_gestoppt_at = at
        protokoll(db, c.id, "aufnahme_beendet", runde=s.id)
    elif typ == "discarded":
        protokoll(db, c.id, "aufnahme_verworfen", runde=s.id)
        angekommen = db.scalar(select(exists().where(Upload.session_id == s.id)))
        if angekommen or _hat_inhalt(db, s):  # Y1: nichts löschen, was die SL schon geschrieben hat
            runde_beenden(db, s)
        else:
            from app.routers.sessions import runde_entfernen

            runde_entfernen(db, s)


def aufnehmen(db: Session, k: BotKey, session_id: str, discord_id: str) -> Attendee:
    s, c, _v = _runde_fuer_bot(db, k, session_id)
    _snowflake(discord_id)
    if s.state != "recording":
        raise errors.conflict("wrong_state", "wrong_state.bot")
    z, _a = zustand(db, c, s, discord_id)
    if z != "ready":
        raise errors.conflict("consent_missing", "consent_missing.bot")
    m = _mitglied_zu(db, c, discord_id)
    a = _anwesend(db, s, m, discord_id)
    if a is not None and a.mit_spur:
        return a
    spuren = db.scalar(select(func.count()).select_from(Attendee).where(Attendee.session_id == s.id,
                                                                        Attendee.mit_spur.is_(True))) or 0
    if spuren >= MAX_SPUREN:
        raise errors.conflict("too_many_tracks")
    if a is None:  # nur Mitglieder – Gäste entstehen über ihren Zustimmungslink
        pos = db.scalar(select(func.max(Attendee.position)).where(Attendee.session_id == s.id))
        a = Attendee(session_id=s.id, member_id=m.id, position=(pos or 0) + 1, consent=True, consent_source="app",
                     consent_at=utcnow())
        db.add(a)
    a.mit_spur = True
    db.flush()
    return a


def spur_verwerfen(db: Session, s: GameSession, a: Attendee, grund: str = "spur_verworfen") -> None:
    """Spur verwerfen (Widerruf): angekommene Teile löschen, nie mehr annehmen, Person aus den Anwesenden nehmen."""
    from app import storage

    for f in db.scalars(select(UploadFile).join(Upload, Upload.id == UploadFile.upload_id)
                        .where(Upload.session_id == s.id, UploadFile.track_attendee_id == a.id)):
        storage.delete_upload_file(f.upload_id, f.id)
        db.delete(f)
    protokoll(db, s.campaign_id, grund, runde=s.id)
    db.delete(a)
    db.flush()


def bot_spur_verwerfen(db: Session, k: BotKey, session_id: str, attendee_id: str) -> None:
    s, _c, _v = _runde_fuer_bot(db, k, session_id)
    a = db.get(Attendee, attendee_id[:64])
    if a is None or a.session_id != s.id:
        raise errors.not_found()
    spur_verwerfen(db, s, a)


def widerruf_in_der_app(db: Session, m: Member) -> None:
    """B8: Ein Mitglied widerruft seine Zustimmung – laufende bzw. noch nicht verarbeitete Bot-Runden der Kampagne
    verlieren seine Spur sofort."""
    for a, s in db.execute(select(Attendee, GameSession).join(GameSession, GameSession.id == Attendee.session_id)
                           .where(Attendee.member_id == m.id, GameSession.bot_key_id.is_not(None),
                                  GameSession.state.in_(("recording", "uploading")))):
        spur_verwerfen(db, s, a, "spur_widerrufen")


def gaeste_vergessen(db: Session, s: GameSession) -> None:
    """B7: Discord-Prüfsummen von Gästen nur, solange die Runde aufnimmt bzw. bis der Upload abgeschlossen ist."""
    for a in db.scalars(select(Attendee).where(Attendee.session_id == s.id, Attendee.discord_hash.is_not(None))):
        a.discord_hash = None
    db.execute(ConsentLink.__table__.delete().where(ConsentLink.session_id == s.id))


# ---------------------------------------------------------------- Zustimmungslinks
def zustimmungslink(db: Session, k: BotKey, session_id: str, discord_id: str, anzeigename: str | None,
                    basis: str) -> dict:
    s, _c, _v = _runde_fuer_bot(db, k, session_id)
    _snowflake(discord_id)
    if s.state != "recording":
        raise errors.not_found("session")
    h = id_hash(discord_id)
    schon = db.scalar(select(func.count()).select_from(ConsentLink).where(ConsentLink.session_id == s.id,
                                                                          ConsentLink.discord_hash == h)) or 0
    if schon >= ZUSTIMMUNGSLINKS_JE_RUNDE:
        raise errors.ApiError(429, "too_many_requests")
    # ältere Links dieser Person werden ungültig (bleiben zum Zählen stehen)
    db.execute(update(ConsentLink).where(ConsentLink.session_id == s.id, ConsentLink.discord_hash == h,
                                         ConsentLink.used_at.is_(None)).values(used_at=utcnow()))
    token = secrets.token_urlsafe(24)
    bis = utcnow() + timedelta(minutes=LINK_MINUTEN)
    db.add(ConsentLink(token_hash=_hash(token), session_id=s.id, discord_hash=h, expires_at=bis,
                       anzeigename=aliasse.zeichen_bereinigen(anzeigename or "")[:40] or None))
    return {"url": f"{basis.rstrip('/')}/zustimmen/{token}", "expiresAt": _z(bis)}


def zustimmungslink_finden(db: Session, token: str) -> tuple[ConsentLink, GameSession, Campaign]:
    z = db.scalar(select(ConsentLink).where(ConsentLink.token_hash == _hash(token or ""), ConsentLink.used_at.is_(None)))
    if z is None or _utc(z.expires_at) < utcnow():
        raise errors.not_found()
    s = db.get(GameSession, z.session_id)
    if s is None or s.state != "recording" or not an(db):
        raise errors.not_found()
    c = db.get(Campaign, s.campaign_id)
    if verbindung(db, c) is None:
        raise errors.not_found()
    return z, s, c


def zustimmungslink_ansehen(db: Session, token: str) -> dict:
    z, _s, c = zustimmungslink_finden(db, token)
    v = db.get(DiscordConnection, c.id)
    sl = db.get(Member, v.connected_by_member_id) if v else None
    from app.services import member_label

    return {"campaignTitle": c.title, "gmName": member_label(sl) if sl else "", "language": c.language or "de",
            "expiresAt": _z(z.expires_at), "memberAllowed": True, "guestAllowed": True,
            "suggestedGuestName": z.anzeigename}


def zustimmen(db: Session, token: str, user: User | None, gastname: str | None, figur: str | None) -> Attendee:
    from app.services import set_recording_consent

    z, s, c = zustimmungslink_finden(db, token)
    if user is not None:
        m = db.scalar(select(Member).where(Member.campaign_id == c.id, Member.user_id == user.id,
                                           Member.left_at.is_(None)))
        if m is not None:
            eigene = discord_id_von(db, user.id)
            if eigene is None:
                raise errors.conflict("discord_link_required")
            if id_hash(eigene) != z.discord_hash:
                raise errors.conflict("discord_mismatch")
            if not _verbrauchen(db, z):
                raise errors.not_found()
            if m.recording_consent_at is None:
                from app.access import terms_verlangen

                terms_verlangen(user, db)  # wie PUT …/recording-consent
                set_recording_consent(db, m, True)  # gilt für alle Aufnahmen der Kampagne bis zum Widerruf (Y2)
            a = db.scalar(select(Attendee).where(Attendee.session_id == s.id, Attendee.member_id == m.id))
            if a is None:
                pos = db.scalar(select(func.max(Attendee.position)).where(Attendee.session_id == s.id))
                a = Attendee(session_id=s.id, member_id=m.id, position=(pos or 0) + 1, consent=True,
                             consent_source="app", consent_at=utcnow())
                db.add(a)
            protokoll(db, c.id, "zustimmung_mitglied", runde=s.id)
            db.flush()
            return a
    name = aliasse.zeichen_bereinigen(gastname or "")[:40].strip()
    if not name:
        raise errors.bad_request("invalid_attendee")
    figur = aliasse.zeichen_bereinigen(figur or "")[:60].strip() or None
    if not _verbrauchen(db, z):
        raise errors.not_found()
    a = db.scalar(select(Attendee).where(Attendee.session_id == s.id, Attendee.discord_hash == z.discord_hash,
                                         Attendee.consent_source == "link"))
    if a is None:
        pos = db.scalar(select(func.max(Attendee.position)).where(Attendee.session_id == s.id))
        a = Attendee(session_id=s.id, position=(pos or 0) + 1, discord_hash=z.discord_hash)
        db.add(a)
    a.guest_name, a.character_name, a.consent, a.consent_source, a.consent_at = name, figur, True, "link", utcnow()
    a.consent_fassung = CONSENT_FASSUNG
    protokoll(db, c.id, "zustimmung_gast", runde=s.id)  # ohne Namen (B12)
    db.flush()
    return a


CONSENT_FASSUNG = "aufnahme-1"


def _verbrauchen(db: Session, z: ConsentLink) -> bool:
    return db.execute(update(ConsentLink).where(ConsentLink.id == z.id, ConsentLink.used_at.is_(None))
                      .values(used_at=utcnow())).rowcount == 1


# ---------------------------------------------------------------- Upload mit Bot-Schlüssel
def upload_pruefen(db: Session, k: BotKey, s: GameSession, files) -> dict[str, Attendee]:
    """B9/Y3: nur Runden dieses Schlüssels in recording, nur trackAttendeeId, nur Spuren mit Zustimmung."""
    c, _v = _kampagne_fuer_bot(db, k, s.campaign_id)
    if s.bot_key_id != k.id:
        raise errors.not_found("session")
    if s.state != "recording":
        raise errors.conflict("invalid_state", "invalid_state.upload")
    if not files or len(files) > MAX_SPUREN:
        raise errors.bad_request("track_member_invalid")
    spuren: dict[str, Attendee] = {}
    for f in files:
        if f.track_member_id or not f.track_attendee_id:
            raise errors.bad_request("track_member_invalid")
        a = db.get(Attendee, f.track_attendee_id)
        if a is None or a.session_id != s.id or a.id in spuren:
            raise errors.bad_request("track_member_invalid")
        spuren[a.id] = a
    for a in spuren.values():
        zustimmung_pruefen(db, c, a)
    return spuren


def zustimmung_pruefen(db: Session, c: Campaign, a: Attendee) -> None:
    if not a.mit_spur or not a.consent:
        raise errors.conflict("consent_missing", "consent_missing.bot")
    if a.member_id:
        m = db.get(Member, a.member_id)
        if m is None or m.recording_consent_at is None or m.left_at is not None:
            raise errors.conflict("consent_revoked", "consent_revoked.bot")
    elif a.consent_source != "link":
        raise errors.conflict("consent_missing", "consent_missing.bot")


def teil_pruefen(db: Session, up: Upload, f: UploadFile) -> None:
    """Bei jedem Teil: Zustimmung der Spur noch da? Sonst Spur verwerfen und 409."""
    a = db.get(Attendee, f.track_attendee_id) if f.track_attendee_id else None
    s = db.get(GameSession, up.session_id)
    c = db.get(Campaign, s.campaign_id)
    try:
        if a is None:
            raise errors.conflict("consent_revoked", "consent_revoked.bot")
        zustimmung_pruefen(db, c, a)
    except errors.ApiError:
        if a is not None:
            spur_verwerfen(db, s, a, "spur_widerrufen")
        db.commit()
        raise errors.conflict("consent_revoked", "consent_revoked.bot") from None


def abschluss_pruefen(db: Session, s: GameSession, up: Upload) -> None:
    """Beim Abschluss: Spuren ohne Zustimmung verlieren alle Teile; bleibt keine Spur, 409 consent_revoked."""
    c = db.get(Campaign, s.campaign_id)
    for f in list(up.files):
        a = db.get(Attendee, f.track_attendee_id) if f.track_attendee_id else None
        try:
            if a is None:
                raise errors.conflict("consent_revoked", "consent_revoked.bot")
            zustimmung_pruefen(db, c, a)
        except errors.ApiError:
            if a is not None:
                spur_verwerfen(db, s, a, "spur_widerrufen")
            else:
                from app import storage

                storage.delete_upload_file(up.id, f.id)
                db.delete(f)
                db.flush()
    db.refresh(up)
    if not up.files:
        db.commit()
        raise errors.conflict("consent_revoked", "consent_revoked.bot")


def erlaubte_sekunden(s: GameSession) -> int:
    """B9: Höchstlänge der Spuren einer Bot-Runde – nie über MAX_STUNDEN, und nicht deutlich länger als die Zeit
    zwischen Startton und Ende (gemeldetes Ende, sonst jetzt); 10 % und 10 Minuten Spielraum."""
    start = _utc(s.recording_started_at or s.played_at)
    ende = _utc(s.bot_gestoppt_at) or utcnow()
    spanne = max(0.0, (ende - start).total_seconds())
    return int(min(MAX_STUNDEN * 3600, spanne * 1.1 + 600))


def sl_konto(db: Session, s: GameSession) -> User:
    """Konto der SL, die die Kampagne verbunden hat (für Antworten, die ein Konto brauchen)."""
    v = db.get(DiscordConnection, s.campaign_id)
    m = db.get(Member, v.connected_by_member_id) if v and v.connected_by_member_id else None
    u = db.get(User, m.user_id) if m and m.user_id else None
    if u is None:
        raise errors.not_found()
    return u


# ---------------------------------------------------------------- Wartung
def wartung(db: Session) -> int:
    """Bot-Runden ohne Upload nach OHNE_TON_STUNDEN → no_recording; abgelaufene Links weg."""
    grenze = utcnow() - timedelta(hours=OHNE_TON_STUNDEN)
    n = 0
    for s in db.scalars(select(GameSession).where(GameSession.state == "recording",
                                                  GameSession.bot_key_id.is_not(None),
                                                  GameSession.created_at < grenze)):
        runde_beenden(db, s)
        n += 1
    jetzt = utcnow()
    db.execute(DiscordLink.__table__.delete().where(DiscordLink.expires_at < jetzt - timedelta(days=1)))
    db.execute(ConsentLink.__table__.delete().where(ConsentLink.expires_at < jetzt - timedelta(days=2),
                                                    ~exists().where(GameSession.id == ConsentLink.session_id,
                                                                    GameSession.state == "recording")))
    db.execute(DiscordProtokoll.__table__.delete().where(DiscordProtokoll.at < jetzt - timedelta(days=VERLAUF_TAGE)))
    return n
