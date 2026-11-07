"""Server 0.4.158: Verlauf der Discord-Anbindung (Schnittstelle 0.4.33) und S64 (Erstatten nicht doppelt)."""
import json
from datetime import timedelta

from app.db import utcnow
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_server_04155 import (  # noqa: F401 (Fixture)
    GAST_ID,
    _runde,
    _zustimmen_gast,
    bot,
)
from tests.test_server_04157 import _fall, anbieter  # noqa: F401 (Fixture)
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


# ---------------------------------------------------------------- Discord-Verlauf (0.4.33)
def test_verlauf_nur_fuer_die_sl_und_ohne_namen(client, bot, dbs):  # noqa: F811
    b = bot
    sid = _runde(client, b)
    _zustimmen_gast(client, b, sid)
    ga = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": GAST_ID}, headers=b["h"]).json()
    assert client.delete(f"{API}/bot/sessions/{sid}/tracks/{ga['attendeeId']}", headers=b["h"]).status_code == 204
    r = client.get(f"{API}/campaigns/{b['cid']}/discord/log", headers=b["gm"])
    assert r.status_code == 200
    eintraege = r.json()["entries"]
    arten = [e["kind"] for e in eintraege]
    assert arten[-1] == "connected" and "recording_started" in arten and "consent_guest" in arten
    assert "track_discarded" in arten  # der Bot hat die Spur verworfen
    assert all(set(e) <= {"at", "kind", "sessionId"} for e in eintraege)
    assert any(e.get("sessionId") == sid for e in eintraege)
    text = json.dumps(eintraege)
    assert "Gwen" not in text and GAST_ID not in text and "Tafelrunde" not in text
    # Spielende und Fremde: 404
    assert client.get(f"{API}/campaigns/{b['cid']}/discord/log", headers=b["pl"]).status_code == 404
    assert client.get(f"{API}/campaigns/gibt-es-nicht/discord/log", headers=b["gm"]).status_code == 404
    # Runde weg → ohne sessionId
    from app.models import GameSession

    client.post(f"{API}/bot/sessions/{sid}/events", json={"type": "discarded", "at": utcnow().isoformat()},
                headers=b["h"])
    dbs.expire_all()
    assert dbs.get(GameSession, sid) is None
    eintraege = client.get(f"{API}/campaigns/{b['cid']}/discord/log", headers=b["gm"]).json()["entries"]
    assert eintraege[0]["kind"] == "recording_discarded" and "sessionId" not in eintraege[0]


def test_verlauf_hoechstens_200_und_wartung_nach_12_monaten(client, bot, dbs):  # noqa: F811
    from app import discord_bot
    from app.models import DiscordProtokoll

    b = bot
    for _ in range(210):
        discord_bot.protokoll(dbs, b["cid"], "aufnahme_beendet")
    discord_bot.protokoll(dbs, b["cid"], "etwas_neues")  # unbekannt: nicht in der Antwort
    alt = DiscordProtokoll(campaign_id=b["cid"], was="verbunden", at=utcnow() - timedelta(days=400))
    dbs.add(alt)
    dbs.commit()
    alt_id = alt.id
    eintraege = client.get(f"{API}/campaigns/{b['cid']}/discord/log", headers=b["gm"]).json()["entries"]
    assert len(eintraege) == 200 and {e["kind"] for e in eintraege} == {"recording_stopped"}
    discord_bot.wartung(dbs)
    dbs.commit()
    dbs.expire_all()
    assert dbs.get(DiscordProtokoll, alt_id) is None


# ---------------------------------------------------------------- S64: Erstatten nicht doppelt
def test_zweiter_klick_ohne_meldung_erstattet_nicht_noch_einmal(client, dbs, admin, anbieter, monkeypatch):  # noqa: F811
    from app import erstattung, zahlung
    from app.models import Bezahlvorgang

    g = _fall(dbs)
    _faktor_an(client, dbs, _chef(dbs).id)
    pfad = "/verwaltung/abrechnung/erstattung/ausfuehren"
    assert client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1"}).status_code == 200
    angestossen = len(anbieter.anfragen)
    # keine Meldung, 25 Stunden später (Doppelklick-Schlüssel beim Anbieter abgelaufen)
    for v in dbs.query(Bezahlvorgang).all():
        if v.erstattung_offen_at is not None:
            v.erstattung_offen_at = utcnow() - timedelta(hours=25)
    dbs.commit()
    seite = client.get("/verwaltung/abrechnung", params={"erstattung": "erna"}).text
    assert "Meldung fehlt" in seite and 'name="erneut"' in seite
    r = client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1"})
    assert r.status_code == 409 and "Erneut anstoßen" in r.text
    assert len(anbieter.anfragen) == angestossen  # nichts ging an den Anbieter
    # ausdrücklich erneut → geht noch einmal hinaus
    r = client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1", "erneut": "1"})
    assert r.status_code == 200 and len(anbieter.anfragen) > angestossen
    # Meldung kommt → Sperre weg
    dbs.expire_all()
    v = next(v for v in dbs.query(Bezahlvorgang).all() if v.zahlung_ref == "pi_neuneuneu1")
    assert v.erstattung_offen_cent == 545
    assert zahlung._gegenbuchen(dbs, v, "erstattung", 545, "stripe:evt_1")
    v.erstattet_cent = 545  # wie bei charge.refunded
    dbs.commit()
    assert v.erstattung_offen_at is None
    assert all(t.zahlung_ref != "pi_neuneuneu1" for t in erstattung.aufteilen(dbs, g).teile)


def test_fehlgeschlagene_erstattung_sperrt_nicht(dbs, anbieter):  # noqa: F811
    from app import erstattung
    from app.models import Bezahlvorgang

    g = _fall(dbs)
    anbieter.stripe = 400
    e = erstattung.erstatten(dbs, g, "test")
    assert "pi_neuneuneu1" in e.fehlgeschlagen
    v = next(v for v in dbs.query(Bezahlvorgang).all() if v.zahlung_ref == "pi_neuneuneu1")
    assert v.erstattung_offen_at is None
    anbieter.stripe = 200
    e = erstattung.erstatten(dbs, g, "test")
    assert "pi_neuneuneu1" not in e.wartet and not e.fehlgeschlagen
