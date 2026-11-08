"""Server 0.4.156: echtes Geld nur über `chronik zahlung-live` (Weg zum ersten echten Geld, Punkt 7)."""
from typer.testing import CliRunner

from app.einstellungen import meta_lesen, meta_schreiben
from tests.test_server_04133 import _einrichten
from tests.test_server_04138 import _veroeffentlichen
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_server_04155 import bot  # noqa: F401 (Fixture)
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

LIVE = {"stripe_schluessel": "sk_live_abcdefgh", "stripe_webhook": "whsec_live123456", "paypal_id": "live-client-id",
        "paypal_geheim": "live-geheimnis", "paypal_webhook_id": "WH-LIVE-12345"}


def _alles_da(client, dbs):
    _einrichten(dbs)
    _veroeffentlichen(dbs)
    meta_schreiben(dbs, "angabe.public_url", "https://chronik.example.org")
    meta_schreiben(dbs, "impressum.text", "Impressum: Erika Muster, Musterweg 1")
    meta_schreiben(dbs, "datenschutz.text", "Datenschutz: alles erklärt.")
    _faktor_an(client, dbs, _chef(dbs).id)
    dbs.commit()


def test_fehlt_nennt_jede_luecke(client, dbs, admin):  # noqa: F811
    from app import zahlung_live as zl

    luecken = zl.fehlt(dbs, {})
    text = " ".join(luecken)
    for teil in ("Abrechnung ist aus", "Bedingungen für Guthaben", "Widerrufsbelehrung", "Impressum",
                 "Datenschutzhinweis", "Ohne zweiten Faktor: chef", "Live-Schlüssel", "whsec_", "PayPal"):
        assert teil in text, teil
    meta_schreiben(dbs, "impressum.text", "Name: [Dein Name]")
    assert any("Impressum" in z for z in zl.fehlt(dbs))


def test_einschalten_und_zurueck(client, dbs, admin):  # noqa: F811
    from app import zahlung, zahlung_live as zl

    _alles_da(client, dbs)
    assert zl.fehlt(dbs) == []
    assert zl.an(dbs, dict(LIVE, stripe_schluessel="sk_test_abc")) != []  # Testschlüssel zählt nicht
    assert not zahlung.live(dbs)
    alt = meta_lesen(dbs, "zahlung.stripe_schluessel")
    assert zl.an(dbs, LIVE) == []
    dbs.commit()
    assert zahlung.live(dbs) and meta_lesen(dbs, "zahlung.stripe_schluessel") == "sk_live_abcdefgh"
    assert zahlung.bereit(dbs) == ["stripe", "paypal"] and "api-m.paypal.com" in zahlung._paypal_basis(dbs)
    zl.aus(dbs)
    dbs.commit()
    assert not zahlung.live(dbs) and meta_lesen(dbs, "zahlung.stripe_schluessel") == alt
    assert not meta_lesen(dbs, "zahlung.test.stripe_schluessel")


def test_testinstanz_nie_live(client, dbs, admin, monkeypatch):  # noqa: F811
    from app import zahlung_live as zl
    from app.config import get_settings

    _alles_da(client, dbs)
    monkeypatch.setattr(get_settings(), "taleward_testinstanz", True)
    assert any("Testinstanz" in z for z in zl.an(dbs, LIVE))


def test_kommandozeile(client, dbs, admin, monkeypatch):  # noqa: F811
    from app import cli, zahlung

    monkeypatch.setattr(cli, "_db", lambda: dbs)
    r = CliRunner().invoke(cli.app, ["zahlung-live"])
    assert r.exit_code == 0 and "fehlt noch" in r.output
    assert CliRunner().invoke(cli.app, ["zahlung-live", "--an"]).exit_code == 1
    _alles_da(client, dbs)
    eingabe = "\n".join(LIVE[k] for k in ("stripe_schluessel", "stripe_webhook", "paypal_id", "paypal_geheim",
                                          "paypal_webhook_id")) + "\n"
    r = CliRunner().invoke(cli.app, ["zahlung-live", "--an"], input=eingabe)
    assert r.exit_code == 0, r.output
    assert "sk_live_abcdefgh" not in r.output and zahlung.live(dbs)
    r = CliRunner().invoke(cli.app, ["zahlung-live", "--aus"])
    assert r.exit_code == 0 and not zahlung.live(dbs)


def test_bot_spuren_hoechstens_so_lang_wie_die_aufnahme(client, bot, dbs, monkeypatch):  # noqa: F811
    """B9: Länge wird bei Bot-Runden immer gemessen – auch ohne Abrechnung –, höchstens 6 Stunden und nicht deutlich
    länger als Startton bis Ende."""
    from datetime import timedelta

    from app import abrechnung, discord_bot
    from app.db import utcnow
    from app.models import GameSession
    from tests.test_server_04155 import API, BEN_ID, _discord, _runde

    b, h = bot, bot["h"]
    _discord(dbs, "ben", BEN_ID)
    sid = _runde(client, b)
    client.put(f"{API}/campaigns/{b['cid']}/recording-consent", json={"granted": True}, headers=b["pl"])
    a = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": BEN_ID}, headers=h).json()
    s = dbs.get(GameSession, sid)
    s.recording_started_at = utcnow() - timedelta(hours=1)
    dbs.commit()
    import base64

    from app import spuren
    from tests.test_server_04159 import verschluesseln

    daten = verschluesseln(base64.b64decode(spuren.track_key(dbs)["publicKey"]), sid, a["attendeeId"], b"0123456789")
    up = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": [
        {"fileName": "ben.twse", "sizeBytes": len(daten), "mimeType": spuren.MIME,
         "trackAttendeeId": a["attendeeId"]}]}, headers=h).json()
    client.put(f"{API}/uploads/{up['uploadId']}/files/{up['files'][0]['fileId']}/chunks/0", content=daten,
               headers={**h, "Content-Type": "application/octet-stream"})
    assert not abrechnung.an(dbs)
    monkeypatch.setattr(abrechnung, "aufnahme_messen", lambda up: 3 * 3600.0)  # 3 h bei 1 h Aufnahme
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=h)
    assert r.status_code == 409 and r.json()["code"] == "recording_too_long"
    dbs.expire_all()
    assert dbs.get(GameSession, sid).state == "no_recording"
    assert discord_bot.erlaubte_sekunden(dbs.get(GameSession, sid)) <= 6 * 3600


def test_gast_spur_bekommt_den_gastnamen(client, world, dbs, tmp_path):
    """Discord-Spur eines Gastes (trackAttendeeId ohne Mitglied): Worker bekommt „gast:<Anwesender>“, die Stimme wird
    mit dem Gastnamen vorgeschlagen – ohne Stimmprofil."""
    from tests.test_step2a import API, audio_abschnitte, status, worker_starten, worker_token

    w = world
    att = [{"memberId": w["gm_member"], "consent": True, "consentSource": "on_site"},
           {"guestName": "Gwen", "consent": True, "consentSource": "on_site"}]
    s = client.post(f"{API}/campaigns/{w['cid']}/sessions", json={"playedAt": "2026-09-20T18:00:00Z",
                                                                   "attendees": att}, headers=w["gm"]).json()
    gast = next(a["id"] for a in s["attendees"] if a.get("guestName") == "Gwen")
    dateien = audio_abschnitte(tmp_path, (30, 28))
    files = [{"fileName": d.name, "sizeBytes": d.stat().st_size, "mimeType": "audio/mp4", **k}
             for d, k in zip(dateien, ({"trackMemberId": w["gm_member"]}, {"trackAttendeeId": gast}))]
    up = client.post(f"{API}/sessions/{s['id']}/uploads", json={"source": "discord", "files": files},
                     headers=w["gm"]).json()
    for f, d in zip(up["files"], dateien):
        daten = d.read_bytes()
        groesse = up["chunkSizeBytes"]
        for i in range(f["chunkCount"]):
            client.put(f"{API}/uploads/{up['uploadId']}/files/{f['fileId']}/chunks/{i}",
                       content=daten[i * groesse:(i + 1) * groesse],
                       headers={**w["gm"], "Content-Type": "application/octet-stream"})
    assert client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=w["gm"]).status_code == 202
    assert worker_starten(client, worker_token(dbs), tmp_path).einen_auftrag()
    assert status(client, w["gm"], s["id"])["state"] == "summarizing"
    sprecher = client.get(f"{API}/sessions/{s['id']}/speakers", headers=w["gm"]).json()
    gaeste = [x for x in sprecher if x.get("suggestedGuestName")]
    assert [x["suggestedGuestName"] for x in gaeste] == ["Gwen"] and gaeste[0]["source"] == "discord_track"
    from app.models import Speaker

    assert all(sp.embedding is None for sp in dbs.query(Speaker).all() if sp.suggested_guest_name)
