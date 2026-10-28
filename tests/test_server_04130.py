"""Server 0.4.130: Die Verwaltung zeigt nur, was zur Betriebsart passt; der Rest steht zugeklappt. Kürzere Hilfetexte,
neuer Standardname „Taleward“."""
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


def _betrieb(dbs, art):
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "betrieb.art", art)
    dbs.commit()


def test_transkription_nur_cloud_klappt_worker_zu(client, dbs, admin):  # noqa: F811
    _betrieb(dbs, "cloud")
    seite = client.get("/verwaltung/transkription").text
    assert "Transkription über die Cloud" in seite and "Ausweichlösung" not in seite
    assert '<details class="klappteil" id="worker-teil">' in seite  # zu, aber erreichbar
    assert seite.index('id="extern"') < seite.index('id="worker-teil"') < seite.index('id="koppeln"')
    assert 'name="stunden" value="' in seite and 'type="hidden" name="stunden"' in seite  # Wartezeit bleibt erhalten


def test_transkription_lokal_zeigt_alles(client, dbs, admin):  # noqa: F811
    _betrieb(dbs, "beides")
    seite = client.get("/verwaltung/transkription").text
    assert "worker-teil" not in seite and "Cloud als Ausweichlösung" in seite
    assert seite.index('id="koppeln"') < seite.index('id="extern"')
    assert "Wartezeit in Stunden" in seite


def test_cloud_wartezeit_bleibt_beim_speichern(client, dbs, admin):  # noqa: F811
    from app.einstellungen import extern_konfig

    _betrieb(dbs, "cloud")
    r = client.post("/verwaltung/transkription/extern", data={"csrf": admin, "anbieter": "", "stunden": "0"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert extern_konfig(dbs).stunden == 0


def test_zusammenfassung_nur_gewaehlter_anbieter_offen(client, dbs, admin):  # noqa: F811
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "llm.art", "api")
    meta_schreiben(dbs, "llm.api_url", "https://api.mistral.ai/v1")
    meta_schreiben(dbs, "llm.api_modell", "mistral-medium-latest")
    dbs.commit()
    _betrieb(dbs, "cloud")
    seite = client.get("/verwaltung/zusammenfassung").text
    vorn, gruppe = seite.split('<details class="anbieter-gruppe">', 1)
    assert 'data-anbieter="mistral"' in vorn and 'data-anbieter="mistral"' not in gruppe
    assert 'data-anbieter="andere"' in gruppe
    assert 'id="anbieter-url-feld" hidden' in seite  # Adresse nur bei „Anderer“
    assert '<summary>Lokales Modell</summary>' in seite  # zugeklappt
    assert "Erweitert: Weg zum Kapitel, Prüfung, Kosten" in seite
    assert 'name="cent_ein"' in seite.split("Erweitert: Weg zum Kapitel")[1]  # Preise unter „Erweitert“
    assert "Worker mit Sprachmodell" not in seite


def test_zusammenfassung_speichern_aendert_nichts_an_zugeklappten_werten(client, dbs, admin):  # noqa: F811
    """Alles Zugeklappte wird weiter mitgeschickt – Speichern setzt nichts zurück."""
    import re

    seite = client.get("/verwaltung/zusammenfassung").text
    form = seite.split('action="/verwaltung/zusammenfassung"', 1)[1].split("</form>", 1)[0]
    for name in ("weg", "pruefansicht", "lokal_modell", "lokal_kontext", "gegenpruefen_feld", "api_modell_notizen"):
        assert re.search(rf'name="{name}"', form), name


def test_abrechnung_aus_klappt_preise_zu(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/abrechnung").text
    assert '<details class="klappteil" id="preise">' in seite
    assert "<h2>Protokoll</h2>" not in seite
    _betrieb(dbs, "cloud")
    seite = client.get("/verwaltung/abrechnung").text
    assert "Preise für eigene Worker" in seite and 'name="worker_kapitel_h"' in seite
    assert "/guthaben" not in seite.split("<main>", 1)[1]


def test_anmeldung_dienste_zugeklappt_bis_eingerichtet(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/anmeldung").text
    assert '<details class="klappe" id="google"><summary>Google einrichten</summary>' in seite
    r = client.post("/verwaltung/anmeldung/google", data={"csrf": admin, "client_id": "abc"}, follow_redirects=False)
    assert r.status_code == 303
    seite = client.get("/verwaltung/anmeldung").text
    assert '<details class="klappe" id="google" open>' in seite  # halb eingerichtet: offen
    client.post("/verwaltung/anmeldung/google", data={"csrf": admin, "client_id": "abc", "client_secret": "geheim"})
    seite = client.get("/verwaltung/anmeldung").text
    assert '<div class="karte" id="google">' in seite


def test_einstellungen_klappen(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/einstellungen").text
    assert "Registrierungscode (nur für „Mit Registrierungscode“)" in seite
    seite = client.get("/verwaltung/einstellungen/einrichtung").text
    assert '<details class="klappteil" id="app">' in seite
    assert '<details class="klappteil" id="nutzungsbedingungen">' in seite
    _betrieb(dbs, "cloud")
    seite = client.get("/verwaltung/einstellungen").text
    assert "ohne Worker warten" not in seite and 'type="hidden" name="stunden"' in seite


def test_uebersicht_nur_cloud(client, dbs, admin):  # noqa: F811
    _betrieb(dbs, "cloud")
    seite = client.get("/verwaltung/").text
    assert "Die Cloud-Transkription ist nicht eingerichtet" in seite and "Kein Worker bereit" not in seite
    assert "<h2>Lokaler Worker</h2>" not in seite and "Worker bereit" not in seite


def test_updates_ohne_repo_namen_und_ohne_worker_bei_nur_cloud(client, dbs, admin):  # noqa: F811
    _betrieb(dbs, "cloud")
    seite = client.get("/verwaltung/updates").text
    assert "Taleward/taleward-app" not in seite and "Worker (Windows)" not in seite and "App (Android)" in seite


def test_standardname_taleward(client, dbs, admin):  # noqa: F811
    from app.einstellungen import angaben, meta_schreiben

    assert angaben(dbs).server_name == "Taleward"
    meta_schreiben(dbs, "angabe.server_name", "Session-Chronik")  # früher oft nur durch Speichern eingetragen
    dbs.commit()
    assert angaben(dbs).server_name == "Taleward"
    meta_schreiben(dbs, "angabe.server_name", "Nebelfeld")
    dbs.commit()
    assert angaben(dbs).server_name == "Nebelfeld"
    assert "Session-Chronik" not in client.get("/verwaltung/").text


# ---------------------------------------------------------------- Neue Konten (offene Registrierung, Anzeigename)
def test_anzeigename_ohne_steuerzeichen_und_mail_bleibt_heil(client, dbs, admin, monkeypatch):  # noqa: F811
    from tests.test_konto import daten
    from tests.test_server_04128 import mit_adresse, setzen

    from app import mail
    from app.models import User

    gesendet = []
    monkeypatch.setattr(mail, "senden", lambda db, an, betreff, text, html=None: gesendet.append(betreff))
    setzen(dbs, registrierung="open", **{"melden.smtp_host": "smtp.example.org"})
    mit_adresse(dbs, "chef", "chefin@example.org")
    name = "Do" + chr(0x202E) + "ra" + chr(10) + "Betreff: x"
    r = client.post("/api/v1/auth/register", json=daten(username="dreh", displayName=name))
    assert r.status_code == 201, r.text
    dora = dbs.query(User).filter_by(username="dreh").one()
    assert dora.display_name == "Do ra Betreff: x"
    # Ein alter Name mit Zeilenumbruch bricht die Mail an die Verwaltung nicht mehr ab
    dora.display_name = "Alt\nName"
    dbs.commit()
    mit_adresse(dbs, "dreh", "dora@example.org")
    login = client.post("/api/v1/auth/login", json={"username": "dreh", "password": "Drachenfeuer7"}).json()
    r = client.post("/api/v1/me/gm-request", headers={"Authorization": f"Bearer {login['accessToken']}"})
    assert r.status_code == 202 and gesendet and "\n" not in gesendet[0]
    assert "Alt Name" in client.get("/verwaltung/sl-freigaben").text


def test_anfrage_mail_mit_ungueltiger_kopfzeile_gilt_als_versandfehler(client, dbs, admin, monkeypatch):  # noqa: F811
    from tests.test_server_04128 import mit_adresse, setzen

    from app import mail, slfreigabe
    from app.models import User

    def kaputt(db, an, betreff, text, html=None):
        raise ValueError("Header values may not contain linefeed or carriage return characters")

    monkeypatch.setattr(mail, "senden", kaputt)
    setzen(dbs, **{"melden.smtp_host": "smtp.example.org"})
    mit_adresse(dbs, "chef", "chefin@example.org")
    u = dbs.query(User).filter_by(username="chef").one()
    from app.db import utcnow

    u.gm_requested_at = utcnow()
    assert slfreigabe.mail_an_verwaltung(dbs, u, "https://x.example.org") is False and u.gm_mail_fehler


def test_offene_registrierung_hat_eine_obergrenze(client, world, dbs, admin, monkeypatch):  # noqa: F811
    from tests.test_konto import daten, einladung
    from tests.test_server_04128 import setzen

    from app import konto

    monkeypatch.setattr(konto, "OFFEN_GRENZEN", (("registrierung-offen:stunde", 2, 3600.0),
                                                ("registrierung-offen:tag", 5, 86400.0)))
    monkeypatch.setitem(konto.OFFEN_GRENZE_ERREICHT, "zeit", None)
    setzen(dbs, registrierung="open")
    url = "/api/v1/auth/register"
    assert client.post(url, json=daten(username="eins")).status_code == 201
    assert client.post(url, json=daten(username="zwei")).status_code == 201
    r = client.post(url, json=daten(username="drei"))
    assert r.status_code == 429 and r.json()["code"] == "too_many_requests"
    assert "mehr neue Konten über die offene Registrierung" in client.get("/verwaltung/einstellungen").text
    # Mit Einladung zählt die Grenze nicht
    r = client.post(url, json=daten(username="vier", inviteCode=einladung(client, world)))
    assert r.status_code == 201, r.text
