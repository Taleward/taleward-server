"""Server 0.4.115: Verwaltung aufgeräumt – Übersicht sagt zuerst in Worten, wie es steht; Einstellungen speichern je
Abschnitt, die neue Fassung der Nutzungsbedingungen steht abgesetzt am Ende; Menü am Handy eingeklappt; Symbole statt
Schriftzeichen."""
import re

from tests.test_verwaltung import admin  # noqa: F401  (Fixture)

HAKEN = chr(0x2713)
KREIS = chr(0x25CB)


def test_uebersicht_alles_laeuft(client, admin):  # noqa: F811
    seite = client.get("/verwaltung/").text
    assert '<p class="lage">Alles läuft.</p>' in seite
    assert '<dl class="kennzahlen">' in seite and 'class="raster"' not in seite


def test_uebersicht_nennt_fehlgeschlagene(client, world, dbs, admin):  # noqa: F811
    from app.models import GameSession
    from tests.test_step2a import neue_session

    for _ in range(2):
        s = neue_session(client, world)
        dbs.get(GameSession, s["id"]).state = "failed"
    dbs.commit()
    seite = client.get("/verwaltung/").text
    assert '<p class="lage lage--achtung">2 Runden sind fehlgeschlagen.' in seite
    assert 'href="/verwaltung/warteschlange">Warteschlange ansehen' in seite
    assert re.search(r'<div class="achtung"><dt>fehlgeschlagen</dt><dd>2</dd>', seite)
    en = client.get("/verwaltung/", headers={"Accept-Language": "en"}).text
    assert "2 sessions failed." in en


def test_menue_am_handy_und_symbole(client, admin):  # noqa: F811
    seite = client.get("/verwaltung/einstellungen").text
    assert '<details class="menue"><summary>' in seite and "Menü · Einstellungen" in seite
    assert seite.count('aria-current="page"') == 2  # in der Leiste und im eingeklappten Menü
    uebersicht = client.get("/verwaltung/").text
    assert HAKEN not in uebersicht and KREIS not in uebersicht
    assert '<svg class="icon" aria-hidden="true"' in uebersicht  # Einrichtungsliste mit Symbolen
    assert HAKEN not in client.get("/verwaltung/uebergabe").text


def _formulare(seite: str) -> list[str]:
    return re.findall(r'name="bereich" value="(\w+)"', seite)


def test_einstellungen_je_abschnitt(client, admin):  # noqa: F811
    seite = client.get("/verwaltung/einstellungen").text
    assert _formulare(seite) == ["betreiber", "konten", "aufnahmen", "kosten", "app"]
    # Neue Fassung der Nutzungsbedingungen ganz am Ende, nach den Benachrichtigungen
    assert seite.index('id="benachrichtigungen"') < seite.index('id="nutzungsbedingungen"')
    assert 'class="karte karte--achtung" id="nutzungsbedingungen"' in seite
    # Nur das Kostenlimit – Name und Betreiber fehlen im Formular und bleiben, wie sie sind
    vorher = client.get("/api/v1/info").json()
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "kosten", "limit_euro": "12"},
                    follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].endswith("?ok=gespeichert#kosten")
    nachher = client.get("/api/v1/info").json()
    assert (nachher["name"], nachher["operator"]) == (vorher["name"], vorher["operator"])
    assert 'name="limit_euro" value="12"' in client.get("/verwaltung/einstellungen").text
    # Konten-Abschnitt ändert nur die Registrierung
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "konten", "registrierung": "closed"})
    assert r.status_code == 200 and 'value="closed" selected' in r.text
    assert 'name="limit_euro" value="12"' in r.text
    # Betreiber-Abschnitt prüft weiter Pflichtfelder
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "betreiber", "server_name": "",
                                                       "server_operator": "X", "min_age": "16"})
    assert r.status_code == 400
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "betreiber", "server_name": "Hort",
                                                       "server_operator": "Verein", "min_age": "16"})
    assert r.status_code == 200 and client.get("/api/v1/info").json()["name"] == "Hort"
    # Unbekannter Abschnitt speichert nichts
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "alles", "limit_euro": "99"})
    assert r.status_code == 400 and 'name="limit_euro" value="12"' in r.text


def test_app_abschnitt_prueft_nur_seine_links(client, dbs, admin):  # noqa: F811
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "app",
                                                       "app_download_url": "javascript:alert(1)"})
    assert r.status_code == 400
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "app",
                                                       "app_download_url": "https://example.org/app.apk",
                                                       "privacy_policy_url": "javascript:alert(1)"})
    assert r.status_code == 200  # Feld eines anderen Abschnitts wird nicht angefasst
    from app.einstellungen import angaben

    a = angaben(dbs)
    assert a.app_download_url == "https://example.org/app.apk" and a.privacy_policy_url != "javascript:alert(1)"
