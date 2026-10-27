"""Server 0.4.131: Betriebsart in den Einstellungen und auf der Übersicht; Einstellungen in zwei Reitern."""
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


def _art(client, admin, art, **mehr):  # noqa: F811
    return client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "betrieb", "betriebsart": art,
                                                           **mehr}, follow_redirects=False)


def test_uebersicht_zeigt_betriebsart(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/").text
    assert "Betriebsart noch nicht gewählt" in seite and 'href="/verwaltung/einstellungen#betrieb"' in seite
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "betrieb.art", "cloud")
    dbs.commit()
    seite = client.get("/verwaltung/").text
    assert "Betriebsart: <strong>Nur Cloud</strong>" in seite
    assert "Operating mode: <strong>Cloud only" in client.get("/verwaltung/", headers={"Accept-Language": "en"}).text


def test_betriebsart_wechseln_mit_folgen(client, dbs, admin):  # noqa: F811
    from app.einrichtung import betriebsart
    from app.einstellungen import extern_konfig, meta_schreiben

    # Nur Cloud ohne Schlüssel → weiter zur Transkription, um ihn einzutragen
    r = _art(client, admin, "cloud")
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/transkription?ok=betrieb_schluessel#extern"
    assert betriebsart(dbs) == "cloud" and extern_konfig(dbs).stunden == 0
    # mit Schlüssel: Cloud-Transkription an, sofort
    meta_schreiben(dbs, "extern.api_key", "geheim-1234")
    dbs.commit()
    r = _art(client, admin, "cloud")
    assert r.headers["location"] == "/verwaltung/einstellungen?ok=gespeichert#betrieb"
    dbs.expire_all()
    assert extern_konfig(dbs).anbieter and extern_konfig(dbs).stunden == 0
    # Ausweichlösung: Wartezeit 12 h, ohne Worker weiter zum Anbinden
    r = _art(client, admin, "beides")
    assert r.headers["location"] == "/verwaltung/transkription?ok=betrieb_worker#koppeln"
    dbs.expire_all()
    assert extern_konfig(dbs).anbieter and extern_konfig(dbs).stunden == 12
    # Lokaler Server: Cloud-Transkription aus
    _art(client, admin, "lokal")
    dbs.expire_all()
    assert betriebsart(dbs) == "lokal" and not extern_konfig(dbs).anbieter
    # Unbekanntes ändert nichts
    r = _art(client, admin, "alles")
    assert r.status_code == 400 and betriebsart(dbs) == "lokal"
    assert "Schlüssel von Mistral eintragen" in client.get("/verwaltung/transkription?ok=betrieb_schluessel").text


def test_reiter_und_ziele(client, dbs, admin):  # noqa: F811
    betrieb = client.get("/verwaltung/einstellungen").text
    assert '<a href="/verwaltung/einstellungen" aria-current="page">Laufender Betrieb</a>' in betrieb
    assert 'id="betrieb"' in betrieb and 'name="server_name"' not in betrieb
    einrichtung = client.get("/verwaltung/einstellungen/einrichtung").text
    assert 'aria-current="page">Einrichtung</a>' in einrichtung and 'name="server_name"' in einrichtung
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "betreiber", "server_name": "Hort",
                                                       "server_operator": "Verein", "min_age": "16"},
                    follow_redirects=False)
    assert r.headers["location"] == "/verwaltung/einstellungen/einrichtung?ok=gespeichert#betreiber"
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "betreiber", "server_name": "",
                                                       "server_operator": "Verein", "min_age": "16"})
    assert r.status_code == 400 and 'name="server_name"' in r.text  # Fehler auf dem richtigen Reiter
    r = client.post("/verwaltung/nutzungsbedingungen/neue-fassung", data={"csrf": admin, "bestaetigung": "falsch"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/verwaltung/einstellungen/einrichtung?nb=falsch")
