"""Server 0.4.161: Schnittstelle 0.4.35 – Stelle merken über den Bot (M1, M2)."""
from datetime import timedelta

from app.db import utcnow
from tests.test_server_04155 import BEN_ID, GAST_ID, bot  # noqa: F401 (Fixture)
from tests.test_server_04159 import _zwei_spuren
from tests.test_step2a import API

FREMD_ID = "777777777777777777"


def _merken(client, b, sid, did, at=None, note=None):
    body = {"discordUserId": did, "at": (at or utcnow()).isoformat()}
    if note is not None:
        body["note"] = note
    return client.post(f"{API}/bot/sessions/{sid}/bookmarks", json=body, headers=b["h"])


def test_merken_fuer_mitglied_mit_spur(client, bot, dbs):  # noqa: F811
    rd, ben, _gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    r = _merken(client, bot, sid, BEN_ID, note="Der Wirt" + chr(0x202E) + " lügt" + chr(7))
    assert r.status_code == 201, r.text
    assert set(r.json()) == {"offsetMs"} and r.json()["offsetMs"] >= 3500 * 1000  # Start vor einer Stunde
    stellen = client.get(f"{API}/sessions/{sid}/bookmarks", headers=bot["pl"]).json()
    assert len(stellen) == 1 and stellen[0]["source"] == "discord"
    assert stellen[0]["note"] == "Der Wirt lügt"  # Steuer- und Richtungszeichen weg
    # nur die Person selbst – nie die SL
    assert client.get(f"{API}/sessions/{sid}/bookmarks", headers=bot["gm"]).json() == []
    # in der App gesetzt: app
    r = client.post(f"{API}/sessions/{sid}/bookmarks", json={}, headers=bot["pl"])
    assert r.status_code == 201 and r.json()["source"] == "app"


def test_404_und_409_wie_beschrieben(client, bot, dbs):  # noqa: F811
    from app.models import Attendee, GameSession, Member

    rd, ben, gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    assert _merken(client, bot, sid, FREMD_ID).status_code == 404  # niemand
    r = _merken(client, bot, sid, GAST_ID)
    assert r.status_code == 409 and r.json()["code"] == "guest_no_bookmarks"
    assert _merken(client, bot, "gibt-es-nicht", BEN_ID).status_code == 404
    # zu früh / zu spät
    assert _merken(client, bot, sid, BEN_ID, at=utcnow() - timedelta(hours=2)).status_code == 400
    assert _merken(client, bot, sid, BEN_ID, at=utcnow() + timedelta(minutes=10)).status_code == 400
    # Spur verworfen → 404 ohne Grund
    a = dbs.get(Attendee, ben)
    a.mit_spur = False
    dbs.commit()
    r = _merken(client, bot, sid, BEN_ID)
    assert r.status_code == 404 and "consent" not in r.text
    a.mit_spur = True
    dbs.commit()
    # Zustimmung widerrufen → 404
    m = dbs.get(Member, a.member_id)
    m.recording_consent_at = None
    dbs.commit()
    assert _merken(client, bot, sid, BEN_ID).status_code == 404
    # nicht mehr recording → 409
    s = dbs.get(GameSession, sid)
    s.state = "uploading"
    dbs.commit()
    r = _merken(client, bot, sid, BEN_ID)
    assert r.status_code == 409 and r.json()["code"] == "session_not_recording"


def test_fremde_runde_anderer_schluessel(client, bot, dbs):  # noqa: F811
    from app import discord_bot

    rd, _ben, _gast = _zwei_spuren(client, bot, dbs)
    from app.models import User

    schluessel, _k = discord_bot.schluessel_erzeugen(dbs, "zweiter", dbs.query(User).first().id)
    dbs.commit()
    r = client.post(f"{API}/bot/sessions/{rd['sessionId']}/bookmarks",
                    json={"discordUserId": BEN_ID, "at": utcnow().isoformat()}, headers={"X-Taleward-Bot": schluessel})
    assert r.status_code == 404


def test_grenzen(client, bot, dbs, monkeypatch):  # noqa: F811
    from app import spielende

    rd, _ben, _gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    monkeypatch.setattr(spielende, "MERKEN_JE_RUNDE", 2)
    assert _merken(client, bot, sid, BEN_ID).status_code == 201
    assert _merken(client, bot, sid, BEN_ID).status_code == 201
    r = _merken(client, bot, sid, BEN_ID)
    assert r.status_code == 409 and r.json()["code"] == "bookmark_limit"
    monkeypatch.setattr(spielende, "MERKEN_JE_RUNDE", 200)
    monkeypatch.setattr(spielende, "MERKEN_JE_MINUTE", 2)
    codes = [_merken(client, bot, sid, BEN_ID).status_code for _ in range(4)]
    assert 429 in codes


def test_versatz_mit_pause(dbs):
    import json

    from app import spielende
    from app.models import GameSession

    start = utcnow() - timedelta(minutes=60)
    s = GameSession(campaign_id="x", number=1, played_at=start, recording_started_at=start, bot_key_id="k",
                    bot_ereignisse=json.dumps([
                        {"type": "pause", "at": (start + timedelta(minutes=10)).isoformat()},
                        {"type": "resume", "at": (start + timedelta(minutes=15)).isoformat()},
                        {"type": "stopped", "at": (start + timedelta(minutes=50)).isoformat()}]))
    assert spielende.bot_versatz_ms(s, start + timedelta(minutes=5)) == 5 * 60 * 1000
    assert spielende.bot_versatz_ms(s, start + timedelta(minutes=12)) is None  # in der Pause
    assert spielende.bot_versatz_ms(s, start + timedelta(minutes=20)) == 20 * 60 * 1000  # Spuren laufen in Pausen weiter
    assert spielende.bot_versatz_ms(s, start + timedelta(minutes=55)) is None  # nach dem Ende
    assert spielende.bot_versatz_ms(s, start - timedelta(minutes=1)) is None
