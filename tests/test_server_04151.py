"""Server 0.4.151: „Passkey hinzufügen“ ohne frischen zweiten Faktor führt zur Bestätigung, statt still zu scheitern."""
from app.einstellungen import meta_schreiben
from tests.test_server_04143 import HTTPS, _chef, _faktor_an, _https_an
from tests.test_verwaltung import admin  # noqa: F401


def test_ohne_frischen_faktor_ein_link_zur_bestaetigung(client, dbs, admin):  # noqa: F811
    chef = _chef(dbs)
    _https_an(dbs)
    _faktor_an(client, dbs, chef.id)  # frisch in dieser Sitzung
    seite = client.get(f"{HTTPS}/verwaltung/anmeldung").text
    assert "data-passkey-einrichten" in seite and 'href="/verwaltung/bestaetigen?weiter=/anmeldung"' not in seite
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", "1")  # nicht mehr frisch
    dbs.commit()
    seite = client.get(f"{HTTPS}/verwaltung/anmeldung").text
    assert "data-passkey-einrichten" not in seite
    assert 'href="/verwaltung/bestaetigen?weiter=/anmeldung"' in seite
    assert "Gerätesperre" not in seite and "fragt der Server zuerst" not in seite
    r = client.get(f"{HTTPS}/verwaltung/bestaetigen?weiter=/anmeldung")
    assert r.status_code == 200 and 'name="weiter" value="/anmeldung"' in r.text
    js = client.get(f"{HTTPS}/verwaltung/static/passkey.js").text
    assert "r.redirected" in js and "window.location.origin" in js and ".innerHTML" not in js
