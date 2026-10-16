"""Server 0.4.105: Wer den Server betreibt (Datenschutz-Vorlage, Impressum), Seiten statt Fehlerdaten, Verwaltung."""
import html
import re

from tests.test_verwaltung import admin, csrf_von  # noqa: F401 (Fixture)


def _text(seite: str) -> str:
    return html.unescape(re.search(r'<textarea name="text"[^>]*>(.*?)</textarea>', seite, re.S).group(1))


def _betreiber(client, csrf, **w):
    daten = {"csrf": csrf, "verein_name": "Nebelfeld-Runde", "server_contact": "kontakt@nebelfeld.example",
             "min_age": "16", "art": "privat", "anschrift": "Lindenweg 3\n12345 Nebelfeld"} | w
    return client.post("/verwaltung/assistent/verein", data=daten, follow_redirects=False)


def test_betreiber_fuellt_datenschutz_vorlage(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/assistent/verein").text
    assert "Wer betreibt diesen Server?" in seite and 'value="privat"' in seite and "1. " in seite
    assert _betreiber(client, admin, art="kaputt").status_code == 400
    assert _betreiber(client, admin).headers["location"] == "/verwaltung/assistent/betrieb"
    text = _text(client.get("/verwaltung/assistent/datenschutz").text)
    assert "Nebelfeld-Runde\nLindenweg 3\n12345 Nebelfeld\nkontakt@nebelfeld.example" in text
    assert "um unsere Runden zu dokumentieren" in text and "Vereins" not in text and "[Anschrift]" not in text
    # Verein ohne Anschrift: Platzhalter bleibt zum Ausfüllen, Zusatzangaben gehen nur bei Verein/Firma
    _betreiber(client, admin, art="verein", anschrift="", vertretung="Vorstand: Ada Kiesel", registereintrag="VR 4711")
    from app import betreiber

    b = betreiber.lesen(dbs)
    assert (b.art, b.anschrift, b.vertretung, b.register) == ("verein", "", "Vorstand: Ada Kiesel", "VR 4711")
    assert "[Anschrift]" in _text(client.get("/verwaltung/assistent/datenschutz").text)
    _betreiber(client, admin, art="privat", vertretung="soll weg", registereintrag="soll weg")
    assert betreiber.lesen(dbs).vertretung == "" and betreiber.lesen(dbs).register == ""


def test_impressum(client, dbs, admin):  # noqa: F811
    _betreiber(client, admin, art="verein", vertretung="Vorstand: Ada Kiesel", registereintrag="VR 4711")
    r = client.get("/impressum")
    assert r.status_code == 404 and "kein Impressum veröffentlicht" in r.text and "{" not in r.text  # keine Fehlerdaten
    seite = client.get("/verwaltung/assistent/impressum").text
    assert "keine Rechtsauskunft" in seite
    text = _text(seite)
    assert "Nebelfeld-Runde\nLindenweg 3" in text and "Vorstand: Ada Kiesel" in text and "VR 4711" in text
    assert "betreibt diesen Server nicht" in text
    r = client.post("/verwaltung/assistent/impressum", data={"csrf": admin, "aktion": "veroeffentlichen",
                                                              "text": "[offen]" + text}, follow_redirects=False)
    assert r.status_code == 400 and "eckigen Klammern" in r.text
    r = client.post("/verwaltung/assistent/impressum", data={"csrf": admin, "aktion": "veroeffentlichen",
                                                              "text": text + "\n\n<b>x</b>"}, follow_redirects=False)
    assert r.headers["location"] == "/verwaltung/assistent/sicherung"
    oeffentlich = client.get("/impressum")
    assert oeffentlich.status_code == 200 and "<h1>Impressum</h1>" in oeffentlich.text and "&lt;b&gt;" in oeffentlich.text
    # Eigener Link statt eigenem Text
    r = client.post("/verwaltung/assistent/impressum", data={"csrf": admin, "aktion": "link",
                                                              "eigener_link": "http://unsicher.example"})
    assert r.status_code == 400
    client.post("/verwaltung/assistent/impressum", data={"csrf": admin, "aktion": "link",
                                                          "eigener_link": "https://nebelfeld.example/impressum"})
    r = client.get("/impressum", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "https://nebelfeld.example/impressum"
    client.post("/verwaltung/assistent/impressum", data={"csrf": admin, "aktion": "ohne"})
    assert client.get("/impressum").status_code == 404
    # Englische Vorlage
    client.cookies.set("tw_sprache", "en")
    assert "# Legal notice" in _text(client.get("/verwaltung/assistent/impressum").text)


def test_seiten_statt_fehlerdaten(client, dbs):
    r = client.get("/datenschutz")
    assert r.status_code == 404 and "noch kein Datenschutzhinweis" in r.text and '"code"' not in r.text
    r = client.get("/guthaben")
    assert r.status_code == 404 and "kein Guthaben" in r.text and '"code"' not in r.text
    assert client.post("/guthaben/anmelden", data={"username": "x", "password": "y"}).status_code == 404


def test_einstellungen_betreiber_ohne_doppelte_felder(client, dbs, admin):  # noqa: F811
    from app import betreiber
    from app.models import Organization

    seite = client.get("/verwaltung/einstellungen").text
    assert 'name="org_name"' not in seite and 'name="betreiber_anschrift"' in seite
    assert "App-Fassung von Hand festlegen" in seite
    r = client.post("/verwaltung/einstellungen", data={
        "csrf": admin, "server_name": "Nebelfeld", "server_operator": "Nebelfeld-Runde", "min_age": "16",
        "betreiber_art": "firma", "betreiber_anschrift": "Lindenweg 3\r\n\r\n12345 Nebelfeld", "betreiber_register": "HRB 1"},
        follow_redirects=False)
    assert r.status_code == 303
    b = betreiber.lesen(dbs)
    assert (b.art, b.anschrift, b.register) == ("firma", "Lindenweg 3\n12345 Nebelfeld", "HRB 1")
    orgs = dbs.query(Organization).all()
    assert len(orgs) != 1 or orgs[0].name == "Nebelfeld-Runde"


def test_verwaltung_ohne_datenbankpfad_und_mit_tippflaechen(client, dbs, admin):  # noqa: F811
    seite = client.get("/verwaltung/").text
    assert "chronik.db" not in seite and "storagebox" not in seite.lower()
    css = client.get("/verwaltung/static/verwaltung.css").text
    assert "--focus-ring:var(--ink)" in css and "--focus-ring:var(--siegel)" not in css
    assert css.count("min-height:48px") >= 4


def test_sicherung_im_einzelplatz_nur_ein_satz(client, dbs, admin, monkeypatch):  # noqa: F811
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "taleward_einzelplatz", True)
    seite = client.get("/verwaltung/").text
    assert "im Taleward-Programm unter Einstellungen → Sicherung" in seite and "Jetzt sichern" not in seite
