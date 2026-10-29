"""Server 0.4.115: Verwaltung aufgeräumt – Leiste links mit drei Gruppen (am Handy „Menü“), Übersicht sagt zuerst in
Worten, wie es steht, fünf Zahlen in einem Kasten; Einstellungen speichern je Abschnitt, die neue Fassung der
Nutzungsbedingungen steht abgesetzt am Ende; Symbole statt Schriftzeichen."""
import re

from tests.test_verwaltung import admin  # noqa: F401  (Fixture)

HAKEN = chr(0x2713)
KREIS = chr(0x25CB)


def _cloud_abschrift(dbs):
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "extern.anbieter", "mistral")
    meta_schreiben(dbs, "extern.api_key", "sk-test")
    dbs.commit()


def test_uebersicht_ohne_worker(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/").text
    # Ohne Cloud-Abschrift braucht jede Runde einen Worker – das steht oben, rot, mit Weg zum Beheben
    assert ('<p class="lage lage--achtung"><span>Kein Worker bereit – neue Runden bleiben liegen, bis einer läuft.</span> '
            '<a href="/verwaltung/transkription">Worker einrichten</a>') in seite
    assert re.findall(r'<span class="kennzahlen__titel">([^<]+)</span>', seite) == [
        "Worker bereit", "in der Warteschlange", "in Verarbeitung", "fehlgeschlagen", "wartet auf die Spielleitung"]
    assert 'class="raster"' not in seite and "0 Kampagnen · 0 Kapitel veröffentlicht · 2 Konten" in seite
    _cloud_abschrift(dbs)
    assert '<p class="lage"><span>Alles läuft.</span></p>' in client.get("/verwaltung/").text


def test_uebersicht_nennt_fehlgeschlagene(client, world, dbs, admin):  # noqa: F811
    from app.models import GameSession
    from tests.test_step2a import neue_session

    _cloud_abschrift(dbs)
    for _ in range(2):
        s = neue_session(client, world)
        dbs.get(GameSession, s["id"]).state = "failed"
    dbs.commit()
    seite = client.get("/verwaltung/").text
    assert '<p class="lage lage--achtung"><span>2 Runden sind fehlgeschlagen.</span>' in seite
    assert 'href="/verwaltung/warteschlange">Warteschlange ansehen' in seite
    assert re.search(r'<a class="kennzahlen__kachel achtung" href="/verwaltung/warteschlange\?zeigen=fehler">'
                     r'<span class="kennzahlen__titel">fehlgeschlagen</span><strong class="kennzahlen__wert">2</strong>', seite)
    en = client.get("/verwaltung/", headers={"Accept-Language": "en"}).text
    assert "2 sessions failed." in en and "1 campaign · 0 chapters published" in en


def test_leiste_menue_und_symbole(client, admin):  # noqa: F811
    seite = client.get("/verwaltung/einstellungen").text
    leiste = seite[seite.index('<aside class="leiste"'):seite.index("</aside>")]
    assert re.findall(r'<p class="leiste__gruppe">([^<]+)</p>', leiste) == ["Betrieb", "Mitglieder", "Server"]
    assert re.findall(r'href="/verwaltung/(\w*)"', leiste)[1:] == [
        "", "warteschlange", "transkription", "zusammenfassung", "konten", "anmeldung", "einstellungen", "updates",
        "abrechnung"]
    assert 'class="kopf"' not in seite and "abmelden" in leiste  # keine dunkle Kopfleiste mehr
    assert '<details class="menue"><summary>' in seite  # am Handy, ohne JavaScript
    assert seite.count('aria-current="page"') == 3  # Leiste, eingeklapptes Menü und (0.4.131) Reiter
    uebersicht = client.get("/verwaltung/").text
    assert HAKEN not in uebersicht and KREIS not in uebersicht
    assert '<svg class="icon" aria-hidden="true"' in uebersicht  # Einrichtungsliste mit Symbolen
    assert HAKEN not in client.get("/verwaltung/uebergabe").text


def _formulare(seite: str) -> list[str]:
    return re.findall(r'name="bereich" value="(\w+)"', seite)


def test_einstellungen_je_abschnitt(client, admin):  # noqa: F811
    seite = client.get("/verwaltung/einstellungen").text
    # 0.4.131: zwei Reiter – „Laufender Betrieb“ und „Einrichtung“
    assert _formulare(seite) == ["betrieb", "konten", "aufnahmen", "kosten"] and 'id="benachrichtigungen"' in seite
    einrichtung = client.get("/verwaltung/einstellungen/einrichtung").text
    assert _formulare(einrichtung) == ["betreiber", "app"]
    # Neue Fassung der Nutzungsbedingungen ganz am Ende
    assert einrichtung.index('id="app"') < einrichtung.index('id="nutzungsbedingungen"')
    assert '<details class="klappteil" id="nutzungsbedingungen">' in einrichtung  # 0.4.130: zugeklappt
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
