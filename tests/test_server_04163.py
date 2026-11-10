"""Server 0.4.163: Kopfzeilen für alle HTML-Seiten, Sprachwechsel auf den Bot-Seiten, englischer Datenschutzhinweis."""
import html
import re

from tests.test_assistent import schluessel_ok  # noqa: F401 (Fixture)
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

SEITEN = ("/discord-bot/datenschutz", "/discord-bot/nutzungsbedingungen", "/discord/" + "a" * 32,
          "/zustimmen/" + "a" * 32, "/impressum", "/nutzungsbedingungen", "/datenschutz", "/einladung/ABCDEFGH")


# ---------------------------------------------------------------- Kopfzeilen
def test_alle_html_seiten_mit_kopfzeilen(client):
    for pfad in SEITEN:
        r = client.get(pfad)
        assert r.headers["content-type"].startswith("text/html"), pfad
        assert "frame-ancestors 'none'" in r.headers.get("content-security-policy", ""), pfad
        assert r.headers.get("x-frame-options") == "DENY", pfad
        assert r.headers.get("x-content-type-options") == "nosniff", pfad
    for pfad in ("/discord/" + "a" * 32, "/zustimmen/" + "a" * 32):  # Geheimnis in der Adresse
        r = client.get(pfad)
        assert r.headers.get("referrer-policy") == "no-referrer" and r.headers.get("cache-control") == "no-store"


def test_schnittstelle_und_beschreibung_unveraendert(client):
    r = client.get(f"{API}/info")
    assert r.headers["content-type"].startswith("application/json")
    assert "x-frame-options" not in r.headers
    from app.main import API_PREFIX
    from app.verwaltung.router import DOKU_PFAD

    assert DOKU_PFAD == f"{API_PREFIX}/docs"  # Swagger lädt fremde Skripte, bekommt die HTML-Kopfzeilen nicht


# ---------------------------------------------------------------- F5
def test_sprachwechsel_bleibt_auf_der_seite(client):
    for pfad in ("/discord-bot/datenschutz", "/discord-bot/nutzungsbedingungen", "/discord/abc", "/zustimmen/abc"):
        r = client.get(f"/verwaltung/sprache/en?weiter={pfad}", follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"] == pfad
        assert "tw_sprache=en" in r.headers["set-cookie"]
    for fremd in ("//boese.example/x", "/discord-bot//boese.example", "https://boese.example/", "/discordx"):
        r = client.get(f"/verwaltung/sprache/en?weiter={fremd}", follow_redirects=False)
        assert r.headers["location"] == "/verwaltung/"


# ---------------------------------------------------------------- Komma (Hinweis Sicherheits-Chat 10.11.)
def test_name_und_anschrift_getrennt(dbs):
    from app import betreiber, rechtstexte
    from app.einstellungen import speichern

    speichern(dbs, server_operator="Spielkreis Moorbach", server_contact="kasse@moorbach.example")
    betreiber.speichern(dbs, "verein", "Am Moorweg 3\r\n12345 Moorbach")
    dbs.commit()
    for s in ("de", "en"):
        assert "Spielkreis Moorbach, Am Moorweg 3, 12345 Moorbach, kasse@moorbach.example" in \
            rechtstexte.vorlage(dbs, "bot_nutzung", s)


# ---------------------------------------------------------------- Datenschutzhinweis auf Englisch
def _feld(seite: str, name: str) -> str:
    return html.unescape(re.search(rf'<textarea name="{name}"[^>]*>(.*?)</textarea>', seite, re.S).group(1))


def _einrichten(client, admin):  # noqa: F811
    client.post("/verwaltung/assistent/verein", data={"csrf": admin, "verein_name": "Spielkreis Moorbach",
                                                      "server_contact": "ds@moorbach.example", "min_age": "16"})
    client.post("/verwaltung/assistent/betrieb/art", data={"csrf": admin, "art": "cloud"})
    client.post("/verwaltung/assistent/betrieb", data={"csrf": admin, "mistral_key": "sk-richtig-123456789",
                                                       "limit_euro": "0"})


def _fertig(text: str) -> str:
    return re.sub(r"\[[^\]]*\]", "filled", text)


def test_englische_vorlage_und_ausgabe(client, dbs, admin, schluessel_ok):  # noqa: F811
    from app import datenschutz, datenschutz_en

    _einrichten(client, admin)
    stand = datenschutz.vorlage_stand(dbs)
    en = datenschutz_en.vorlage(dbs)
    assert "# Privacy notice for" in en and "Mistral AI SAS" in en and "ds@moorbach.example" in en
    assert "## Your rights" in en and "aged 16 and over" in en and "Younger people need their parents' consent" in en
    assert datenschutz.vorlage_stand(dbs) == stand  # die deutsche Vorlage bleibt gleich
    seite = client.get("/verwaltung/assistent/datenschutz").text
    de, en_feld = _feld(seite, "text"), _feld(seite, "text_en")
    assert "## Deine Rechte" in de and "## Your rights" in en_feld
    # offene Klammer im Englischen: nichts gespeichert
    r = client.post("/verwaltung/assistent/datenschutz", data={"csrf": admin, "aktion": "veroeffentlichen",
                                                               "text": _fertig(de), "text_en": en_feld})
    assert r.status_code == 400 and "englischen Fassung" in r.text
    assert client.get("/datenschutz").status_code == 404
    r = client.post("/verwaltung/assistent/datenschutz",
                    data={"csrf": admin, "aktion": "veroeffentlichen", "text": _fertig(de),
                          "text_en": _fertig(en_feld) + "\n\n<script>x</script>"}, follow_redirects=False)
    assert r.status_code == 303
    assert "<h2>Deine Rechte</h2>" in client.get("/datenschutz").text
    englisch = client.get("/datenschutz", headers={"Accept-Language": "en"}).text
    assert "<h2>Your rights</h2>" in englisch and "<script>x" not in englisch
    # Sprachwechsel über den Link
    client.get("/verwaltung/sprache/en?weiter=/datenschutz")
    assert "<h2>Your rights</h2>" in client.get("/datenschutz").text


def test_ohne_englisch_steht_die_deutsche_fassung(client, dbs, admin, schluessel_ok):  # noqa: F811
    _einrichten(client, admin)
    de = _feld(client.get("/verwaltung/assistent/datenschutz").text, "text")
    r = client.post("/verwaltung/assistent/datenschutz", data={"csrf": admin, "aktion": "veroeffentlichen",
                                                               "text": _fertig(de), "text_en": ""},
                    follow_redirects=False)
    assert r.status_code == 303
    assert "<h2>Deine Rechte</h2>" in client.get("/datenschutz", headers={"Accept-Language": "en"}).text
    r = client.post("/verwaltung/assistent/datenschutz", data={"csrf": admin, "aktion": "veroeffentlichen",
                                                               "text": _fertig(de), "text_en": "zu kurz"})
    assert r.status_code == 400


def test_zweisprachiger_text_wird_zum_trennen_angeboten(client, dbs, admin, schluessel_ok):  # noqa: F811
    from app.einstellungen import meta_lesen, meta_schreiben

    _einrichten(client, admin)
    alt = "# Datenschutzhinweis\n\n" + "Deutscher Text. " * 20 + "\n\n# English version\n\n" + "English text. " * 20
    meta_schreiben(dbs, "datenschutz.text", alt)
    dbs.commit()
    seite = client.get("/verwaltung/assistent/datenschutz").text
    assert "# English version" not in _feld(seite, "text") and "Deutscher Text." in _feld(seite, "text")
    assert _feld(seite, "text_en").startswith("# English version")
    dbs.expire_all()
    assert meta_lesen(dbs, "datenschutz.text") == alt  # nur angeboten, nichts geändert, bis veröffentlicht wird
    assert "English text." in client.get("/datenschutz", headers={"Accept-Language": "en"}).text
