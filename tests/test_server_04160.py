"""Server 0.4.160: Landeseiten am PC – „Im Browser öffnen“ vorn, Satz unter „In Taleward öffnen“ (Bot-Chat 08.11.)."""
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

PC = {"User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 Chrome/141.0 Safari/537.36"}
HANDY = {"User-Agent": "Mozilla/5.0 (Linux; Android 15; Pixel 9) AppleWebKit/537.36 Chrome/141.0 Mobile Safari/537.36"}
SATZ = "Nur am Handy mit installierter App. Am PC: Im Browser öffnen."


def _erster_knopf(text: str) -> str:
    stapel = text.split('class="knopfstapel"', 1)[1]
    return stapel.split("</a>", 1)[0]


def test_discord_und_zustimmen_am_pc_browser_vorn(client, dbs, monkeypatch, tmp_path):
    from app import aktualisierung, discord_bot

    discord_bot.schalten(dbs, True)
    dbs.commit()
    monkeypatch.setattr(aktualisierung, "web_ordner", lambda db: tmp_path)
    for pfad, wort in (("/zustimmen/", "Im Browser zustimmen"), ("/discord/", "Im Browser öffnen")):
        pc = client.get(pfad + "c" * 32, headers=PC).text
        assert wort in _erster_knopf(pc) and "tw-btn--primary" in _erster_knopf(pc)
        assert "taleward://" in pc and SATZ in pc  # App-Knopf bleibt, mit Satz
        handy = client.get(pfad + "c" * 32, headers=HANDY).text
        assert "taleward://" in _erster_knopf(handy) and SATZ not in handy
    en = client.get("/discord/" + "c" * 32, headers={**PC, "Accept-Language": "en"}).text
    assert "Only on a phone with the app installed." in en


def test_einladung_am_pc(client, dbs, world, monkeypatch, tmp_path):
    from app import aktualisierung

    monkeypatch.setattr(aktualisierung, "web_ordner", lambda db: tmp_path)
    code = client.post(f"{API}/campaigns/{world['cid']}/invites", headers=world["gm"]).json()["code"]
    pc = client.get(f"/einladung/{code}", headers=PC).text
    assert "Im Browser öffnen" in _erster_knopf(pc) and SATZ in pc
    assert pc.count("Im Browser öffnen") == 2  # Knopf und Satz, kein doppelter Knopf
    handy = client.get(f"/einladung/{code}", headers=HANDY).text
    assert "taleward://einladung" in _erster_knopf(handy) and "Für lange Aufnahmen am Handy" in handy


def test_f3_karte_einmal_und_nicht_im_titel(client, dbs, admin):  # noqa: F811
    r = client.get("/verwaltung/discord")
    titel = r.text.split("<title>", 1)[1].split("</title>", 1)[0]
    assert "csrf" not in titel and "<div" not in titel and "Discord-Bot" in titel
    assert r.text.count('id="spurschluessel"') == 1
