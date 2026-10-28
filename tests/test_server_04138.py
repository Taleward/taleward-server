"""Server 0.4.138: Code in sechs Kästchen, Adresse im Namen des Authenticator-Eintrags, Pflicht-Häkchen beim Aufladen,
Bedingungen für Guthaben und Widerrufsbelehrung."""
from pathlib import Path

from tests.test_server_04133 import _aufladen, _einrichten, _vorgang, anbieter  # noqa: F401
from tests.test_verwaltung import admin  # noqa: F401

STATIC = Path(__file__).resolve().parents[1] / "app" / "verwaltung" / "static"


# ---------------------------------------------------------------- Sechs Kästchen und Name des Eintrags
def test_code_seiten_mit_kaesten_und_script(client, world):
    vorlagen = STATIC.parent / "templates"
    for name, wert in (("anmelden_code.html", "1"), ("_zweiter_faktor.html", "nur-ziffern")):
        t = (vorlagen / name).read_text()
        assert f'data-code-kaesten="{wert}"' in t and "/verwaltung/static/code-kaesten.js" in t
        assert 'name="code"' in t and 'autocomplete="one-time-code"' in t  # ohne JavaScript: das eine Feld
    js = client.get("/verwaltung/static/code-kaesten.js")
    assert js.status_code == 200 and "paste" in js.text and "one-time-code" in js.text
    assert "\\u" not in js.text


def test_einrichten_mit_adresse_im_namen(client, dbs, admin, monkeypatch):  # noqa: F811
    from app.einstellungen import meta_schreiben
    from app.verwaltung import router

    adressen = []
    echt = router.qr_bild
    monkeypatch.setattr(router, "qr_bild", lambda a: adressen.append(a) or echt(a))
    meta_schreiben(dbs, "angabe.public_url", "https://test.example.org")
    dbs.commit()
    r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": admin, "password": "geheim123"})
    assert r.status_code == 200 and 'data-code-kaesten="nur-ziffern"' in r.text
    assert adressen and "(test.example.org)" in adressen[-1].replace("%28", "(").replace("%29", ")")
    assert adressen[-1].startswith("otpauth://totp/") and ":chef?" in adressen[-1]


# ---------------------------------------------------------------- Häkchen beim Aufladen
def test_ohne_haekchen_kein_vorgang(client, world, dbs, anbieter):  # noqa: F811
    _einrichten(dbs)
    r, weiter, _gid = _aufladen(client, dbs, sofort=False)
    assert 'name="sofort" value="1" required' in r.text
    assert weiter.status_code == 400 and "Bitte bestätige zuerst" in weiter.text
    assert 'name="sofort"' in weiter.text  # dieselbe Seite noch einmal, mit Häkchen
    assert _vorgang(dbs) is None and not anbieter.anfragen


def test_mit_haekchen_zeitpunkt_und_fassung_am_vorgang(client, world, dbs, anbieter):  # noqa: F811
    from app import rechtstexte

    _einrichten(dbs)
    _r, weiter, _gid = _aufladen(client, dbs)
    assert weiter.status_code == 303
    v = _vorgang(dbs)
    assert v.zustimmung_at is not None and v.zustimmung_fassung == "unveroeffentlicht (Testmodus)"
    _veroeffentlichen(dbs)
    r, weiter, _gid = _aufladen(client, dbs)
    assert 'href="/guthaben-bedingungen"' in r.text and 'href="/widerruf"' in r.text
    assert weiter.status_code == 303
    assert _vorgang(dbs).zustimmung_fassung == rechtstexte.fassung(dbs)
    assert rechtstexte.fassung(dbs).startswith("bedingungen 20")


def test_echtes_geld_nur_mit_veroeffentlichten_texten(world, dbs, monkeypatch):
    import pytest

    from app import abrechnung, zahlung

    _einrichten(dbs)
    monkeypatch.setattr(zahlung, "live", lambda db: True)
    monkeypatch.setattr(zahlung, "bereit", lambda db: ["stripe"])
    from tests.test_abrechnung_0485 import _anna

    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    with pytest.raises(ValueError, match="bedingungen"):
        zahlung.starten(dbs, g, _anna(dbs).id, "stripe", 1000, zugestimmt=True)
    with pytest.raises(ValueError, match="zustimmung"):
        zahlung.starten(dbs, g, _anna(dbs).id, "stripe", 1000)


# ---------------------------------------------------------------- Texte
def _texte(dbs, art):
    from app import rechtstexte

    return {s: rechtstexte.vorlage(dbs, art, s).replace("[", "").replace("]", "") for s in ("de", "en")}


def _veroeffentlichen(dbs):
    from app import rechtstexte

    for art in rechtstexte.ARTEN:
        t = _texte(dbs, art)
        rechtstexte.speichern(dbs, art, t["de"], t["en"])
    dbs.commit()


def test_vorlagen_aus_den_angaben(dbs):
    from app import betreiber, rechtstexte
    from app.einstellungen import speichern

    _einrichten(dbs)
    speichern(dbs, server_operator="Spielkreis Moorbach", server_contact="kasse@moorbach.example")
    betreiber.speichern(dbs, "verein", "Am Moorweg 3\n12345 Moorbach")
    dbs.commit()
    for art in rechtstexte.ARTEN:
        for s in ("de", "en"):
            t = rechtstexte.vorlage(dbs, art, s)
            assert "Spielkreis Moorbach, Am Moorweg 3, 12345 Moorbach" in t and "kasse@moorbach.example" in t
    de = rechtstexte.vorlage(dbs, "bedingungen", "de")
    assert "mindestens 10,00 €" in de and "höchstens bis −1,50 €" in de and "voreingestellt 2,00 €" in de
    assert "§ 19 UStG" in de and rechtstexte.offene_stellen(de) >= 1  # Umsatzsteuer entscheidet der Betreiber
    en = rechtstexte.vorlage(dbs, "bedingungen", "en")
    assert "at least 10.00 €" in en and "down to −1.50 €" in en
    assert "Sie haben das Recht" in rechtstexte.vorlage(dbs, "widerruf", "de")
    assert "You have the right to withdraw" in rechtstexte.vorlage(dbs, "widerruf", "en")


def test_verwaltung_veroeffentlicht_nur_ohne_klammern_und_beide_sprachen(client, dbs, admin):  # noqa: F811
    from app import rechtstexte

    _einrichten(dbs)
    seite = client.get("/verwaltung/abrechnung/rechtstexte")
    assert seite.status_code == 200 and "Noch nicht veröffentlicht" in seite.text and "Sie haben das Recht" in seite.text
    assert "/verwaltung/abrechnung/rechtstexte" in client.get("/verwaltung/abrechnung").text
    assert "/verwaltung/abrechnung/rechtstexte" in client.get("/verwaltung/einstellungen/einrichtung").text
    vorlage = {s: rechtstexte.vorlage(dbs, "bedingungen", s) for s in ("de", "en")}
    r = client.post("/verwaltung/abrechnung/rechtstexte", data={"csrf": admin, "art": "bedingungen",
                                                                "text_de": vorlage["de"], "text_en": vorlage["en"]})
    assert r.status_code == 400 and "eckigen Klammern offen" in r.text and "Deutsch" in r.text
    t = _texte(dbs, "bedingungen")
    r = client.post("/verwaltung/abrechnung/rechtstexte", data={"csrf": admin, "art": "bedingungen",
                                                                "text_de": t["de"], "text_en": ""})
    assert r.status_code == 400 and "Englisch fehlt oder ist zu kurz" in r.text
    assert not rechtstexte.veroeffentlicht(dbs, "bedingungen")
    r = client.post("/verwaltung/abrechnung/rechtstexte", data={"csrf": admin, "art": "bedingungen",
                                                                "text_de": t["de"], "text_en": t["en"]},
                    follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert rechtstexte.veroeffentlicht(dbs, "bedingungen") and not rechtstexte.veroeffentlicht(dbs)
    assert client.get("/verwaltung/abrechnung/rechtstexte").text.count("Veröffentlicht (Stand: ") == 1


def test_oeffentliche_seiten_deutsch_englisch_und_stand(client, dbs):
    from app import rechtstexte

    _einrichten(dbs)
    assert client.get("/widerruf").status_code == 404
    _veroeffentlichen(dbs)
    stand = rechtstexte.stand(dbs, "widerruf")
    de = client.get("/widerruf", headers={"Accept-Language": "de"})
    assert de.status_code == 200 and "<h1>Widerrufsbelehrung</h1>" in de.text
    j, m, t = stand.split("-")
    assert f"Stand: {t}.{m}.{j}" in de.text and "Content-Security-Policy" in de.headers
    en = client.get("/guthaben-bedingungen", headers={"Accept-Language": "en"})
    assert en.status_code == 200 and "<h1>Credit terms</h1>" in en.text and f"Last updated: {stand}" in en.text
    # Gleicher Inhalt noch einmal: Stand bleibt; Zurückziehen: wieder 404
    texte = {s: rechtstexte.text(dbs, "widerruf", s) for s in ("de", "en")}
    assert not rechtstexte.speichern(dbs, "widerruf", texte["de"], texte["en"])
    rechtstexte.zurueckziehen(dbs, "widerruf")
    dbs.commit()
    assert client.get("/widerruf").status_code == 404 and rechtstexte.fassung(dbs) is None


def test_datenschutz_vorlage_mit_zahlungen_nur_bei_abrechnung(dbs):
    from app.datenschutz import vorlage
    from tests.test_abrechnung_0485 import _modus

    assert "## Guthaben und Zahlungen" not in vorlage(dbs)
    _einrichten(dbs, paypal=False)
    t = vorlage(dbs)
    assert "## Guthaben und Zahlungen" in t and "Stripe Payments Europe" in t and "PayPal (Europe)" not in t
    assert "Der Anbieter kann Daten auch in die USA" in t
    _modus(dbs, "aus")
    assert "## Guthaben und Zahlungen" not in vorlage(dbs)
