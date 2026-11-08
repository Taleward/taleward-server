"""Server 0.4.155: Schnittstelle 0.4.32 – Discord-Bot (S63, Prüfkriterien B13 des Sicherheits-Chats)."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.db import utcnow
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

GILDE = "111111111111111111"
SL_ID = "222222222222222222"
BEN_ID = "333333333333333333"
GAST_ID = "444444444444444444"
FREMD_ID = "555555555555555555"
TWSE = "application/vnd.taleward.spur-verschluesselt"  # 0.4.34: der Bot lädt nur verschlüsselt hoch


def _discord(dbs, user_name, did):
    from app.models import AuthMethod, User

    u = dbs.scalar(select(User).where(User.username == user_name))
    dbs.add(AuthMethod(user_id=u.id, kind="oidc:discord", secret=did))
    dbs.commit()


@pytest.fixture()
def bot(client, world, dbs):
    """Bot eingeschaltet, ein Schlüssel, Anna ist mit SL_ID verknüpft und hat Rabenfels mit der Gilde verbunden."""
    from app import discord_bot

    discord_bot.schalten(dbs, True)
    roh, _k = discord_bot.schluessel_erzeugen(dbs, "Bot", "test")
    dbs.commit()
    _discord(dbs, "anna", SL_ID)
    h = {"X-Taleward-Bot": roh}
    r = client.post(f"{API}/bot/links", json={"guildId": GILDE, "discordUserId": SL_ID, "guildName": "Tafelrunde"},
                    headers=h)
    assert r.status_code == 201, r.text
    link = r.json()
    token = link["url"].rsplit("/", 1)[1]
    r = client.post(f"{API}/discord-links/redeem", json={"token": token, "campaignId": world["cid"]},
                    headers=world["gm"])
    assert r.status_code == 200, r.text
    return {"h": h, "roh": roh, **world}


def _runde(client, b):
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/sessions",
                    json={"guildId": GILDE, "discordUserId": SL_ID, "startedAt": utcnow().isoformat()}, headers=b["h"])
    assert r.status_code == 201, r.text
    return r.json()["sessionId"]


def _bereit(client, b, ids, sid=None):
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/readiness",
                    json={"discordUserIds": ids, **({"sessionId": sid} if sid else {})}, headers=b["h"])
    assert r.status_code == 200, r.text
    return r.json()


def _zustimmen_gast(client, b, sid, did=GAST_ID, name="Gwen"):
    r = client.post(f"{API}/bot/sessions/{sid}/consent-links", json={"discordUserId": did, "displayName": "gwen#1"},
                    headers=b["h"])
    assert r.status_code == 201, r.text
    token = r.json()["url"].rsplit("/", 1)[1]
    r = client.post(f"{API}/consent-links/{token}", json={"consent": True, "guestName": name})
    assert r.status_code == 200, r.text
    return r.json()


def test_schalter_aus_alles_404(client, world, dbs):
    from app import discord_bot

    roh, _k = discord_bot.schluessel_erzeugen(dbs, "Bot", "test")
    dbs.commit()
    h = {"X-Taleward-Bot": roh}
    assert client.post(f"{API}/bot/links", json={"guildId": GILDE, "discordUserId": SL_ID, "guildName": "x"},
                       headers=h).status_code == 404
    assert client.post(f"{API}/discord-links/preview", json={"code": "a-b-c"}, headers=world["gm"]).status_code == 404
    assert client.get(f"{API}/info").json().get("discordBot") in (None, False)


def test_schluessel_nur_auf_bot_wegen(client, bot):
    b = bot
    assert client.get(f"{API}/campaigns/{b['cid']}", headers=b["h"]).status_code == 401
    assert client.get(f"{API}/bot/guilds/{GILDE}/campaigns", params={"discordUserId": SL_ID},
                      headers=b["gm"]).status_code == 401
    assert client.get(f"{API}/bot/guilds/{GILDE}/campaigns", params={"discordUserId": SL_ID},
                      headers={"X-Taleward-Bot": "twb_falsch"}).status_code == 401
    r = client.get(f"{API}/bot/guilds/{GILDE}/campaigns", params={"discordUserId": SL_ID}, headers=b["h"])
    assert [c["title"] for c in r.json()["campaigns"]] == ["Rabenfels"]
    # fremde ID oder Gilde: leere Liste
    assert client.get(f"{API}/bot/guilds/{GILDE}/campaigns", params={"discordUserId": FREMD_ID},
                      headers=b["h"]).json()["campaigns"] == []
    assert client.get(f"{API}/info").json()["discordBot"] is True


def test_einloesen_nur_mit_passender_discord_id_und_einmal(client, bot, dbs):
    b = bot
    r = client.post(f"{API}/bot/links", json={"guildId": GILDE, "discordUserId": FREMD_ID, "guildName": "Fremd"},
                    headers=b["h"])
    fremd = r.json()
    v = client.post(f"{API}/discord-links/preview", json={"code": fremd["code"]}, headers=b["gm"]).json()
    assert v == {"linked": False}
    r = client.post(f"{API}/discord-links/redeem", json={"code": fremd["code"], "campaignId": b["cid"]},
                    headers=b["gm"])
    assert r.status_code == 409 and r.json()["code"] == "discord_link_required"
    # Code in anderer Schreibweise gleichwertig, zweites Einlösen 404
    r = client.post(f"{API}/bot/links", json={"guildId": GILDE, "discordUserId": SL_ID, "guildName": "Tafelrunde"},
                    headers=b["h"])
    code = r.json()["code"]
    anders = code.upper().replace("-", " - ")
    v = client.post(f"{API}/discord-links/preview", json={"code": anders}, headers=b["gm"]).json()
    assert v["linked"] is True and v["campaigns"][0]["connectedGuildName"] == "Tafelrunde"
    assert client.post(f"{API}/discord-links/redeem", json={"code": anders, "campaignId": b["cid"]},
                       headers=b["gm"]).status_code == 200
    assert client.post(f"{API}/discord-links/redeem", json={"code": code, "campaignId": b["cid"]},
                       headers=b["gm"]).status_code == 404
    # Spieler sieht die Verbindung nicht
    assert client.get(f"{API}/campaigns/{b['cid']}/discord", headers=b["pl"]).status_code == 404
    assert client.get(f"{API}/campaigns/{b['cid']}/discord", headers=b["gm"]).json()["connection"]["guildName"] == \
        "Tafelrunde"


def test_schluessel_b_sieht_kampagne_von_a_nicht(client, bot, dbs):
    from app import discord_bot

    b = bot
    roh2, _k = discord_bot.schluessel_erzeugen(dbs, "Zweiter", "test")
    dbs.commit()
    h2 = {"X-Taleward-Bot": roh2}
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/readiness", json={"discordUserIds": [SL_ID]}, headers=h2)
    assert r.status_code == 404
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/sessions",
                    json={"guildId": GILDE, "discordUserId": SL_ID, "startedAt": utcnow().isoformat()}, headers=h2)
    assert r.status_code == 404


def test_bereitschaft_mitglied_gast_und_widerruf_in_der_app(client, bot, dbs):
    b = bot
    _discord(dbs, "ben", BEN_ID)
    sid = _runde(client, b)
    z = _bereit(client, b, [SL_ID, BEN_ID, GAST_ID], sid)
    assert {k: v["state"] for k, v in z["states"].items()} == {SL_ID: "consent_required", BEN_ID: "consent_required",
                                                               GAST_ID: "link_required"}
    assert z["sessionState"] == "recording"
    # Ben stimmt in der App zu
    assert client.put(f"{API}/campaigns/{b['cid']}/recording-consent", json={"granted": True},
                      headers=b["pl"]).status_code in (200, 204)
    assert _bereit(client, b, [BEN_ID], sid)["states"][BEN_ID]["state"] == "ready"
    a = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": BEN_ID}, headers=b["h"]).json()
    assert a["attendeeId"] and _bereit(client, b, [BEN_ID], sid)["states"][BEN_ID]["attendeeId"] == a["attendeeId"]
    # ohne Zustimmung keine Aufnahme
    r = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": SL_ID}, headers=b["h"])
    assert r.status_code == 409 and r.json()["code"] == "consent_missing"
    # Gast über den Link
    g = _zustimmen_gast(client, b, sid)
    assert g["consentSource"] == "link" and g["guestName"] == "Gwen"
    assert _bereit(client, b, [GAST_ID], sid)["states"][GAST_ID]["state"] == "ready"
    ga = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": GAST_ID}, headers=b["h"]).json()
    # Upload beider Spuren beginnt, Ben widerruft in der App → seine Spur ist weg, weitere Teile 409
    r = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": [
        {"fileName": "ben.twse", "sizeBytes": 10, "mimeType": TWSE, "trackAttendeeId": a["attendeeId"]},
        {"fileName": "gast.twse", "sizeBytes": 10, "mimeType": TWSE, "trackAttendeeId": ga["attendeeId"]}]},
        headers=b["h"])
    assert r.status_code == 201, r.text
    up = r.json()
    ben_datei = up["files"][0]["fileId"]
    r = client.put(f"{API}/uploads/{up['uploadId']}/files/{ben_datei}/chunks/0", content=b"0123456789",
                   headers={**b["h"], "Content-Type": "application/octet-stream"})
    assert r.status_code == 204, r.text
    assert client.put(f"{API}/campaigns/{b['cid']}/recording-consent", json={"granted": False},
                      headers=b["pl"]).status_code in (200, 204)
    from app import storage
    from app.models import UploadFile

    assert dbs.get(UploadFile, ben_datei) is None
    assert not storage.chunk_path(up["uploadId"], ben_datei, 0).exists()
    assert _bereit(client, b, [BEN_ID], sid)["states"][BEN_ID]["state"] == "consent_required"


def test_upload_ohne_zustimmung_und_mit_member_id(client, bot, dbs):
    b = bot
    sid = _runde(client, b)
    g = _zustimmen_gast(client, b, sid)
    r = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": [
        {"fileName": "g.ogg", "sizeBytes": 10, "mimeType": "audio/ogg", "trackAttendeeId": g["id"]}]}, headers=b["h"])
    assert r.status_code == 409  # noch nicht als Spur aufgenommen
    r = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": [
        {"fileName": "g.ogg", "sizeBytes": 10, "mimeType": "audio/ogg", "trackMemberId": b["gm_member"]}]},
        headers=b["h"])
    assert r.status_code == 400


def test_gast_widerruf_verwirft_spur(client, bot, dbs):
    from app.models import Attendee

    b = bot
    sid = _runde(client, b)
    _zustimmen_gast(client, b, sid)
    ga = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": GAST_ID}, headers=b["h"]).json()
    assert client.delete(f"{API}/bot/sessions/{sid}/tracks/{ga['attendeeId']}", headers=b["h"]).status_code == 204
    assert dbs.get(Attendee, ga["attendeeId"]) is None
    assert _bereit(client, b, [GAST_ID], sid)["states"][GAST_ID]["state"] == "link_required"


def test_verwerfen_mit_sl_notiz_bleibt_ohne_aufnahme(client, bot, dbs):
    from app.models import GameSession

    b = bot
    sid = _runde(client, b)
    assert client.put(f"{API}/sessions/{sid}/gm-note", json={"text": "Wichtig"}, headers=b["gm"]).status_code == 204
    r = client.post(f"{API}/bot/sessions/{sid}/events", json={"type": "discarded", "at": utcnow().isoformat()},
                    headers=b["h"])
    assert r.status_code == 204
    dbs.expire_all()
    assert dbs.get(GameSession, sid).state == "no_recording"
    sid2 = _runde(client, b)
    client.post(f"{API}/bot/sessions/{sid2}/events", json={"type": "discarded", "at": utcnow().isoformat()},
                headers=b["h"])
    dbs.expire_all()
    assert dbs.get(GameSession, sid2) is None


def test_zweite_laufende_runde_und_ereigniszeiten(client, bot, dbs):
    b = bot
    sid = _runde(client, b)
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/sessions",
                    json={"guildId": GILDE, "discordUserId": SL_ID, "startedAt": utcnow().isoformat()}, headers=b["h"])
    assert r.status_code == 409 and r.json()["code"] == "already_recording"
    spaet = (utcnow() + timedelta(hours=7)).isoformat()
    assert client.post(f"{API}/bot/sessions/{sid}/events", json={"type": "pause", "at": spaet},
                       headers=b["h"]).status_code == 400
    assert client.post(f"{API}/bot/sessions/{sid}/events", json={"type": "interrupted", "at": utcnow().isoformat()},
                       headers=b["h"]).status_code == 204
    s = client.get(f"{API}/sessions/{sid}", headers=b["gm"]).json()
    assert s["state"] == "recording" and s["interrupted"] is True and s["source"] == "discord"


def test_verbindung_loest_sich_wenn_sl_discord_trennt(client, bot, dbs):
    from app.models import AuthMethod

    b = bot
    for m in dbs.scalars(select(AuthMethod).where(AuthMethod.kind == "oidc:discord")):
        dbs.delete(m)
    dbs.commit()
    assert client.get(f"{API}/campaigns/{b['cid']}/discord", headers=b["gm"]).json()["connection"] is None
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/readiness", json={"discordUserIds": [SL_ID]}, headers=b["h"])
    assert r.status_code == 404


def test_widerrufener_schluessel_laesst_verbindung_ruhen(client, bot, dbs):
    from app import discord_bot
    from app.models import BotKey

    b = bot
    for k in dbs.scalars(select(BotKey)):
        discord_bot.schluessel_widerrufen(dbs, k)
    dbs.commit()
    assert client.get(f"{API}/bot/guilds/{GILDE}/campaigns", params={"discordUserId": SL_ID},
                      headers=b["h"]).status_code == 401


def test_wartung_ohne_ton_und_gaeste_vergessen(client, bot, dbs):
    from app import discord_bot
    from app.models import Attendee, GameSession

    b = bot
    sid = _runde(client, b)
    _zustimmen_gast(client, b, sid)
    s = dbs.get(GameSession, sid)
    s.created_at = utcnow() - timedelta(hours=31)
    dbs.commit()
    assert discord_bot.wartung(dbs) == 1
    dbs.commit()
    assert dbs.get(GameSession, sid).state == "no_recording"
    assert all(a.discord_hash is None for a in dbs.scalars(select(Attendee).where(Attendee.session_id == sid)))


def test_zustimmungslink_als_mitglied_braucht_passende_id(client, bot, dbs):
    b = bot
    sid = _runde(client, b)
    r = client.post(f"{API}/bot/sessions/{sid}/consent-links", json={"discordUserId": BEN_ID}, headers=b["h"])
    token = r.json()["url"].rsplit("/", 1)[1]
    ansicht = client.get(f"{API}/consent-links/{token}").json()
    assert ansicht["campaignTitle"] == "Rabenfels" and ansicht["memberAllowed"] is True
    r = client.post(f"{API}/consent-links/{token}", json={"consent": True}, headers=b["pl"])
    assert r.status_code == 409 and r.json()["code"] == "discord_link_required"
    _discord(dbs, "ben", FREMD_ID)
    r = client.post(f"{API}/consent-links/{token}", json={"consent": True}, headers=b["pl"])
    assert r.status_code == 409 and r.json()["code"] == "discord_mismatch"


def test_verwaltung_schalter_und_schluessel(client, dbs, admin):  # noqa: F811
    from app import discord_bot
    from app.models import BotKey
    from tests.test_server_04143 import _chef, _faktor_an

    r = client.get("/verwaltung/discord")
    assert r.status_code == 200 and "Discord-Bot" in r.text
    # ohne zweiten Faktor: weder einschalten noch Schlüssel
    client.post("/verwaltung/discord/schalter", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert not discord_bot.an(dbs)
    _faktor_an(client, dbs, _chef(dbs).id)
    r = client.post("/verwaltung/discord/schalter", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert discord_bot.an(dbs)
    r = client.post("/verwaltung/discord/schluessel", data={"csrf": admin, "name": "Bot A"})
    assert r.status_code == 200 and "twb_" in r.text
    roh = r.text.split("twb_")[1].split("<")[0]
    k = dbs.scalar(select(BotKey))
    assert discord_bot.schluessel_pruefen(dbs, "twb_" + roh).id == k.id and roh not in k.hash
    assert "twb_" not in client.get("/verwaltung/discord").text  # nur einmal sichtbar
    client.post(f"/verwaltung/discord/schluessel/{k.id}/widerrufen", data={"csrf": admin})
    dbs.expire_all()
    assert discord_bot.schluessel_pruefen(dbs, "twb_" + roh) is None
    # Ausschalten geht immer
    client.post("/verwaltung/discord/schalter", data={"csrf": admin, "an": "0"})
    dbs.expire_all()
    assert not discord_bot.an(dbs)


def test_landeseiten(client, dbs):
    from app import discord_bot

    assert client.get("/zustimmen/" + "a" * 32).status_code == 404
    discord_bot.schalten(dbs, True)
    dbs.commit()
    r = client.get("/zustimmen/" + "a" * 32)
    assert r.status_code == 200 and "taleward://zustimmen?url=" in r.text
    r = client.get("/discord/" + "b" * 32)
    assert r.status_code == 200 and "taleward://discord?url=" in r.text


def test_drei_woerter_liste():
    from app import discord_bot

    assert len(set(discord_bot.WOERTER)) >= 1024
    assert discord_bot.code_normal("Wald-Krug - SEEHOF") == "waldkrugseehof"


def test_landeseite_mit_web_app(client, dbs, monkeypatch, tmp_path):
    from app import aktualisierung, discord_bot

    discord_bot.schalten(dbs, True)
    dbs.commit()
    monkeypatch.setattr(aktualisierung, "web_ordner", lambda db: tmp_path)
    r = client.get("/zustimmen/" + "c" * 32)
    assert "/app/#/link?url=" in r.text and "%2Fzustimmen%2F" in r.text
