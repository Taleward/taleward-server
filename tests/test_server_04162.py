"""Server 0.4.162: D5.1 (Bereitschaft je laufender Bot-Runde) und Rechtstexte des Discord-Bots auf dem Server."""
from pathlib import Path

from tests.test_server_04143 import _chef, _faktor_an
from tests.test_server_04155 import SL_ID, bot  # noqa: F401 (Fixture)
from tests.test_server_04159 import _runde_mit_schluessel
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


# ---------------------------------------------------------------- D5.1
def test_takt_der_laufenden_runde_zaehlt_fuer_sich(client, bot, dbs, monkeypatch):  # noqa: F811
    from app import discord_bot

    assert discord_bot.BEREITSCHAFT_JE_STUNDE >= 3 * 120  # Takt alle 30 s braucht 120 je Stunde
    monkeypatch.setattr(discord_bot, "BEREITSCHAFT_JE_STUNDE", 20)  # klein, damit die Grenze je Bot-Schlüssel nicht greift
    sid = _runde_mit_schluessel(client, bot)["sessionId"]
    pfad = f"{API}/bot/campaigns/{bot['cid']}/readiness"
    # viele Klicks auf „Zustimmen“ bzw. /taleward bereit (ohne sessionId) schöpfen den Topf der Kampagne aus …
    for _ in range(20):
        assert client.post(pfad, json={"discordUserIds": [SL_ID]}, headers=bot["h"]).status_code == 200
    assert client.post(pfad, json={"discordUserIds": [SL_ID]}, headers=bot["h"]).status_code == 429
    # … der Takt der laufenden Runde läuft trotzdem weiter
    for _ in range(20):
        r = client.post(pfad, json={"discordUserIds": [SL_ID], "sessionId": sid}, headers=bot["h"])
        assert r.status_code == 200
    # eigener Topf hat auch eine Grenze
    r = client.post(pfad, json={"discordUserIds": [SL_ID], "sessionId": sid}, headers=bot["h"])
    assert r.status_code == 429
    # eine fremde/erfundene sessionId zählt in den Topf der Kampagne
    r = client.post(pfad, json={"discordUserIds": [SL_ID], "sessionId": "erfunden"}, headers=bot["h"])
    assert r.status_code == 429


# ---------------------------------------------------------------- Rechtstexte des Bots
def _angaben(dbs):
    from app import betreiber
    from app.einstellungen import speichern

    speichern(dbs, server_operator="Spielkreis Moorbach", server_contact="kasse@moorbach.example",
              public_url="https://taleward.moorbach.example")
    betreiber.speichern(dbs, "verein", "Am Moorweg 3\n12345 Moorbach")
    dbs.commit()


def _ausfuellen(text: str) -> str:
    import re

    return re.sub(r"\[[^\]]*\]", "ausgefüllt", text)


def test_vorlagen_aus_den_angaben_ohne_interna(dbs):
    from app import rechtstexte, rechtstexte_bot

    _angaben(dbs)
    for art in rechtstexte_bot.ARTEN:
        for s in ("de", "en"):
            t = rechtstexte.vorlage(dbs, art, s)
            assert "Spielkreis Moorbach" in t and "kasse@moorbach.example" in t and "Am Moorweg 3" in t
            assert "https://taleward.moorbach.example/discord-bot/datenschutz" in t
            assert "taleward.org/discord-bot" not in t and "<" not in t
    ds = rechtstexte.vorlage(dbs, "bot_datenschutz", "de")
    assert rechtstexte.offene_stellen(ds) >= 4  # Aufsicht, Hoster, Ort, Tage
    assert "https://taleward.moorbach.example/datenschutz" in ds and "Stand:" not in ds
    # nichts Internes im öffentlichen Repo
    quelle = (Path(__file__).parent.parent / "app" / "rechtstexte_bot.py").read_text(encoding="utf-8")
    for wort in ("netcup", "Hemer", "Goldacker", "Nürnberg", "Düsseldorf", "Welk", "info@taleward"):
        assert wort not in quelle


def test_veroeffentlichen_nur_ohne_klammern_und_mit_faktor(client, dbs, admin):  # noqa: F811
    from app import discord_bot, rechtstexte

    _angaben(dbs)
    discord_bot.schalten(dbs, True)
    dbs.commit()
    assert client.get("/discord-bot/datenschutz").status_code == 404  # noch nichts veröffentlicht
    seite = client.get("/verwaltung/discord/rechtstexte").text
    assert "[Name und Anschrift des Hosters des Bots]" in seite
    assert "/verwaltung/discord/rechtstexte" in client.get("/verwaltung/discord").text
    de = rechtstexte.vorlage(dbs, "bot_datenschutz", "de")
    en = rechtstexte.vorlage(dbs, "bot_datenschutz", "en")
    daten = {"csrf": admin, "art": "bot_datenschutz", "aktion": "veroeffentlichen", "text_de": _ausfuellen(de),
             "text_en": _ausfuellen(en)}
    # ohne frischen zweiten Faktor: nichts
    client.post("/verwaltung/discord/rechtstexte", data=daten, follow_redirects=False)
    dbs.expire_all()
    assert not rechtstexte.veroeffentlicht(dbs, "bot_datenschutz")
    _faktor_an(client, dbs, _chef(dbs).id)
    # mit offener Klammer: 400, nichts veröffentlicht
    r = client.post("/verwaltung/discord/rechtstexte", data={**daten, "text_de": de})
    assert r.status_code == 400 and "eckigen Klammern" in r.text
    dbs.expire_all()
    assert not rechtstexte.veroeffentlicht(dbs, "bot_datenschutz")
    r = client.post("/verwaltung/discord/rechtstexte", data=daten, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert rechtstexte.veroeffentlicht(dbs, "bot_datenschutz")
    seite = client.get("/discord-bot/datenschutz")
    assert seite.status_code == 200 and "Spielkreis Moorbach" in seite.text and "Stand:" in seite.text
    assert "<script" not in seite.text.split("<article", 1)[1].split("</article>", 1)[0]
    # Fuß erst, wenn beide veröffentlicht sind
    assert "/discord-bot/nutzungsbedingungen" not in client.get("/discord-bot/datenschutz").text
    nb = {"csrf": admin, "art": "bot_nutzung", "aktion": "veroeffentlichen",
          "text_de": _ausfuellen(rechtstexte.vorlage(dbs, "bot_nutzung", "de")),
          "text_en": _ausfuellen(rechtstexte.vorlage(dbs, "bot_nutzung", "en"))}
    assert client.post("/verwaltung/discord/rechtstexte", data=nb, follow_redirects=False).status_code == 303
    assert client.get("/discord-bot/nutzungsbedingungen").status_code == 200
    fuss = client.get("/discord-bot/datenschutz").text.split("</article>", 1)[1]
    assert "/discord-bot/datenschutz" in fuss and "/discord-bot/nutzungsbedingungen" in fuss
    # Bot aus → kein Link im Fuß
    discord_bot.schalten(dbs, False)
    dbs.commit()
    assert "/discord-bot/nutzungsbedingungen" not in client.get("/discord-bot/datenschutz").text
    # Englisch über den Sprachwechsel
    en_seite = client.get("/discord-bot/datenschutz", headers={"Accept-Language": "en"}).text
    assert "Who is responsible" in en_seite
    # HTML im Text wird maskiert
    boese = {**nb, "text_de": nb["text_de"] + "\n\n<script>alert(1)</script>"}
    client.post("/verwaltung/discord/rechtstexte", data=boese, follow_redirects=False)
    assert "<script>alert" not in client.get("/discord-bot/nutzungsbedingungen").text
    # Zurückziehen → wieder 404
    client.post("/verwaltung/discord/rechtstexte", data={"csrf": admin, "art": "bot_nutzung", "aktion": "zurueckziehen"},
                follow_redirects=False)
    assert client.get("/discord-bot/nutzungsbedingungen").status_code == 404


def test_guthaben_rechtstexte_unberuehrt(dbs):
    from app import rechtstexte

    assert rechtstexte.ARTEN == ("bedingungen", "widerruf")
    assert rechtstexte.fassung(dbs) is None
