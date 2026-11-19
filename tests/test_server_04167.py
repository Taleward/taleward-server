"""Server 0.4.167: Spielleitungen erfahren außerhalb der App, wie das Guthaben geht (Info- und Begrüßungs-Mail),
Startguthaben für neue Spielleitungen, Kontaktangabe nie zur Guthaben-Seite."""
from datetime import timedelta

import pytest
from sqlalchemy import select

from app.einstellungen import meta_schreiben
from tests.test_abrechnung_0485 import _modus
from tests.test_server_04135 import ADRESSE, post  # noqa: F401 (Fixture)
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


def _preise(dbs, **werte):
    from app import abrechnung

    p = abrechnung.preise(dbs)
    for k, v in werte.items():
        setattr(p, k, v)
    abrechnung.preise_schreiben(dbs, p, "test")
    dbs.commit()


def _konto(dbs, make_user, name, email=None):
    from app.models import User

    make_user(name)
    u = dbs.scalar(select(User).where(User.username == name))
    u.email = email
    dbs.commit()
    return u


def _anmelden(client, name):
    r = client.post(f"{API}/auth/login", json={"username": name, "password": "geheim123"})
    assert r.status_code == 200, r.text
    return {"Authorization": f"Bearer {r.json()['accessToken']}"}


def _kampagne(client, kopf, titel="Probe"):
    r = client.post(f"{API}/campaigns", json={"title": titel}, headers=kopf)
    assert r.status_code in (200, 201), r.text
    return r.json()["id"]


def _saldo(dbs, u):
    from app import abrechnung

    g = abrechnung.guthaben_konto(dbs, u.id, anlegen=False)
    return abrechnung.saldo(dbs, g.id) if g is not None and g.id else 0


# ---------------------------------------------------------------- Startguthaben
def test_startguthaben_einmal_je_konto_und_adresse(client, dbs, make_user, post):  # noqa: F811
    _modus(dbs, "guthaben")
    _preise(dbs, startguthaben=200, startguthaben_monat=2000)
    dora = _konto(dbs, make_user, "dora", "Dora+spiel@Example.net")
    kopf = _anmelden(client, "dora")
    _kampagne(client, kopf)
    dbs.expire_all()
    assert _saldo(dbs, dora) == 200
    willkommen = [m for m in post if m["an"] == "Dora+spiel@Example.net"]
    assert len(willkommen) == 1 and "2,00" in willkommen[0]["text"] and f"{ADRESSE}/guthaben" in willkommen[0]["text"]
    assert "Passwort" in willkommen[0]["text"]
    _kampagne(client, kopf, "Zweite")  # zweite Kampagne: nichts mehr
    dbs.expire_all()
    assert _saldo(dbs, dora) == 200 and len([m for m in post if m["an"] == dora.email]) == 1
    # dieselbe Adresse (anders geschrieben) an einem zweiten Konto: kein zweites Startguthaben
    from app.models import User

    dora.email = "alt@example.net"  # Adresse wieder frei
    dbs.commit()
    emil = _konto(dbs, make_user, "emil", "dora@example.net")
    _kampagne(client, _anmelden(client, "emil"))
    dbs.expire_all()
    assert _saldo(dbs, emil) == 0
    assert dbs.scalar(select(User).where(User.username == "emil")).startguthaben_at is not None
    # Konto gelöscht: die Prüfsumme bleibt, ohne Adresse
    from app.models import StartguthabenAdresse

    zeilen = dbs.scalars(select(StartguthabenAdresse)).all()
    assert len(zeilen) == 1 and "dora" not in zeilen[0].adresse_hash and len(zeilen[0].adresse_hash) == 64


def test_kein_startguthaben_ohne_bestaetigte_adresse_dann_bei_bestaetigung(client, dbs, make_user, post):  # noqa: F811
    from app import startguthaben

    _modus(dbs, "guthaben")
    _preise(dbs, startguthaben=200)
    fritz = _konto(dbs, make_user, "fritz")
    _kampagne(client, _anmelden(client, "fritz"))
    dbs.expire_all()
    assert _saldo(dbs, fritz) == 0 and post == []
    # Bestätigung der Adresse löst es aus (hier direkt der Haken, den die Bestätigungsseite ruft)
    fritz.email = "fritz@example.net"
    startguthaben.begruessen(dbs, fritz)
    dbs.commit()
    assert _saldo(dbs, fritz) == 200 and len(post) == 1


def test_monatsgrenze(client, dbs, make_user, post):  # noqa: F811
    from app.models import AbrechnungProtokoll

    _modus(dbs, "guthaben")
    _preise(dbs, startguthaben=200, startguthaben_monat=300)
    a = _konto(dbs, make_user, "anke", "anke@example.net")
    b = _konto(dbs, make_user, "berta", "berta@example.net")
    _kampagne(client, _anmelden(client, "anke"))
    _kampagne(client, _anmelden(client, "berta"))
    dbs.expire_all()
    assert _saldo(dbs, a) == 200 and _saldo(dbs, b) == 0  # 200 + 200 > 300
    assert dbs.scalar(select(AbrechnungProtokoll).where(AbrechnungProtokoll.was == "startguthaben_grenze"))
    berta = [m for m in post if m["an"] == "berta@example.net"]
    assert len(berta) == 1 and "Ausprobieren" not in berta[0]["text"]  # Begrüßung ohne Startguthaben


@pytest.mark.parametrize("modus", ["aus", "topf"])
def test_nur_im_guthaben_betrieb(client, dbs, make_user, post, modus):  # noqa: F811
    _modus(dbs, modus)
    _preise(dbs, startguthaben=200)
    u = _konto(dbs, make_user, "gerd", "gerd@example.net")
    _kampagne(client, _anmelden(client, "gerd"))
    dbs.expire_all()
    assert _saldo(dbs, u) == 0 and post == []


def test_aus_bei_null(client, dbs, make_user, post):  # noqa: F811
    _modus(dbs, "guthaben")
    u = _konto(dbs, make_user, "hanna", "hanna@example.net")
    _kampagne(client, _anmelden(client, "hanna"))
    dbs.expire_all()
    assert _saldo(dbs, u) == 0
    assert len(post) == 1 and "Willkommen" in post[0]["betreff"] and "Ausprobieren" not in post[0]["text"]


def test_startguthaben_nicht_erstattbar(client, dbs, make_user, post):  # noqa: F811
    from app import abrechnung, erstattung

    _modus(dbs, "guthaben")
    _preise(dbs, startguthaben=200)
    u = _konto(dbs, make_user, "ida", "ida@example.net")
    _kampagne(client, _anmelden(client, "ida"))
    dbs.expire_all()
    assert erstattung.erstattbar(dbs, abrechnung.guthaben_konto(dbs, u.id)) == 0


def test_normalisierte_adresse_und_aufraeumen(dbs):
    from app import startguthaben
    from app.db import utcnow
    from app.models import StartguthabenAdresse

    assert startguthaben.normalisiert(" Max+Test@Example.ORG ") == "max@example.org"
    assert startguthaben.pruefsumme(dbs, "max+a@example.org") == startguthaben.pruefsumme(dbs, "MAX@example.org")
    assert startguthaben.pruefsumme(dbs, "kaputt") is None
    dbs.add(StartguthabenAdresse(adresse_hash="a" * 64, betrag_cent=1, angelegt=utcnow() - timedelta(days=365 * 3 + 1)))
    dbs.add(StartguthabenAdresse(adresse_hash="b" * 64, betrag_cent=1))
    dbs.commit()
    assert startguthaben.aufraeumen(dbs) == 1
    dbs.commit()
    assert [z.adresse_hash for z in dbs.scalars(select(StartguthabenAdresse))] == ["b" * 64]


def test_datenschutz_vorlage_nennt_die_pruefsumme(dbs):
    from app import rechtstexte

    _modus(dbs, "guthaben")
    assert not any("Prüfsumme" in t for t in rechtstexte.datenschutz_abschnitt(dbs))
    _preise(dbs, startguthaben=200)
    de = "\n".join(rechtstexte.datenschutz_abschnitt(dbs))
    en = "\n".join(rechtstexte.datenschutz_abschnitt(dbs, "en"))
    assert "Prüfsumme deiner E-Mail-Adresse" in de and "[3] Jahren" in de
    assert "checksum of your email address" in en and "[3] years" in en


# ---------------------------------------------------------------- Info-Mail an heutige SL
def _sl_mit_kampagne(client, dbs, make_user, name, email):
    u = _konto(dbs, make_user, name, email)
    _kampagne(client, _anmelden(client, name))
    return u


def test_info_mail_mit_rueckfrage_faktor_und_nur_einmal(client, dbs, admin, make_user, post):  # noqa: F811
    from app.models import AbrechnungProtokoll, User

    # SL von früher (Abrechnung war aus – keine Begrüßung)
    a = _sl_mit_kampagne(client, dbs, make_user, "alma", "alma@example.net")
    b = _sl_mit_kampagne(client, dbs, make_user, "bodo", "bodo@example.net")
    _sl_mit_kampagne(client, dbs, make_user, "carl", None)  # ohne Adresse: nie
    b.guthaben_mails_aus = True  # abbestellt: nie
    _konto(dbs, make_user, "nurspieler", "spieler@example.net")  # keine SL: nie
    dbs.commit()
    assert post == []
    # Einschalten führt zur Karte
    _faktor_an(client, dbs, _chef(dbs).id)
    r = client.post("/verwaltung/abrechnung/modus", data={"csrf": admin, "modus": "guthaben"}, follow_redirects=False)
    assert r.status_code == 303 and "guthaben_eingeschaltet" in r.headers["location"]
    assert r.headers["location"].endswith("#guthaben-info")
    seite = client.get("/verwaltung/abrechnung").text
    assert 'id="guthaben-info"' in seite and "Ja, 1 Spielleitungen jetzt anschreiben" in seite
    pfad = "/verwaltung/abrechnung/guthaben-info"
    assert client.post(pfad, data={"csrf": admin, "anzahl": "1"}).status_code == 400  # ohne Haken
    assert client.post(pfad, data={"csrf": admin, "anzahl": "5", "bestaetigt": "1"}).status_code == 400  # falsche Zahl
    assert post == []
    r = client.post(pfad, data={"csrf": admin, "anzahl": "1", "bestaetigt": "1"}, follow_redirects=False)
    assert r.status_code == 303 and "guthaben_info" in r.headers["location"]
    assert [m["an"] for m in post] == ["alma@example.net"]
    m = post[0]
    assert f"{ADRESSE}/guthaben" in m["text"] and "€" not in m["text"] and "Google, Discord, Apple oder Microsoft" in m["text"]
    dbs.expire_all()
    assert dbs.get(User, a.id).guthaben_info_at is not None
    assert dbs.scalar(select(AbrechnungProtokoll).where(AbrechnungProtokoll.was == "guthaben_info"))
    # zweiter Klick: niemand mehr
    assert "schon informiert" in client.get("/verwaltung/abrechnung").text
    r = client.post(pfad, data={"csrf": admin, "anzahl": "0", "bestaetigt": "1"}, follow_redirects=False)
    assert r.status_code == 303 and len(post) == 1


def test_info_mail_nur_mit_frischem_faktor(client, dbs, admin, make_user, post):  # noqa: F811
    _sl_mit_kampagne(client, dbs, make_user, "alma", "alma@example.net")
    _modus(dbs, "guthaben")
    r = client.post("/verwaltung/abrechnung/guthaben-info", data={"csrf": admin, "anzahl": "1", "bestaetigt": "1"},
                    follow_redirects=False)
    assert not (r.status_code == 303 and "guthaben_info" in r.headers.get("location", ""))
    assert post == []


def test_info_ohne_mailversand_nicht(client, dbs, admin, make_user):  # noqa: F811
    _sl_mit_kampagne(client, dbs, make_user, "alma", "alma@example.net")
    _modus(dbs, "guthaben")
    _faktor_an(client, dbs, _chef(dbs).id)
    seite = client.get("/verwaltung/abrechnung").text
    assert "Dafür braucht es den Mailversand" in seite
    r = client.post("/verwaltung/abrechnung/guthaben-info", data={"csrf": admin, "anzahl": "1", "bestaetigt": "1"})
    assert r.status_code == 409


def test_freigabe_mail_zaehlt_als_informiert(client, dbs, make_user, post):  # noqa: F811
    from app import slfreigabe, startguthaben
    from tests.test_server_04135 import _sl_wartet

    _modus(dbs, "guthaben")
    u = _sl_wartet(dbs, make_user)
    assert slfreigabe.entscheiden(dbs, u, "freigeben", u)
    dbs.commit()
    assert "Google, Discord, Apple oder Microsoft" in post[-1]["text"]
    assert u.guthaben_info_at is not None and u not in startguthaben.info_empfaenger(dbs)


# ---------------------------------------------------------------- Kontaktangabe
@pytest.mark.parametrize("wert", ["https://srv.example.org/guthaben", "https://srv.example.org/GUTHABEN?x=1",
                                  "https://srv.example.org/%67uthaben", "https://a.example.org/x/%2Fguthaben",
                                  "/guthaben-bedingungen"])
def test_kontakt_nie_zur_guthaben_seite(client, dbs, admin, wert):  # noqa: F811
    from app.einstellungen import angaben, kontakt_erlaubt

    assert not kontakt_erlaubt(wert)
    assert kontakt_erlaubt("info@example.org") and kontakt_erlaubt("https://example.org/kontakt")
    r = client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "betreiber", "server_name": "S",
                                                       "server_operator": "B", "server_contact": wert})
    assert r.status_code == 400 and "nicht auf die Guthaben-Seite" in r.text
    # steht so etwas aus früheren Fassungen gespeichert: /info nennt keinen Kontakt
    meta_schreiben(dbs, "angabe.server_contact", wert)
    dbs.commit()
    if angaben(dbs).server_contact == wert:
        assert client.get(f"{API}/info").json().get("contact") is None


# ---------------------------------------------------------------- Caddy nur an der Kennung erkannt
def test_s69_1_fremder_caddy_name_wird_erkannt(tmp_path):
    from tests.test_server_04164 import CADDY_ZEILE, _aufruf, _ordner, netz

    ziel, stub = _ordner(tmp_path)
    assert _aufruf(ziel, stub, "an", "app.example.org", "meine-app:8080").returncode == 0
    netz(tmp_path, [CADDY_ZEILE, "zusatz-meine-app-1 taleward-zusatz=meine-app,",
                    "taleward-caddy-2 taleward-zusatz=server,taleward-caddy-2,"])
    r = _aufruf(ziel, stub, "liste")
    assert r.returncode != 0 and "taleward-caddy-2" in r.stderr
    assert (ziel / "extra-caddy" / "aus" / "app.example.org.caddy").exists()


def test_s69_1_projektname_ohne_taleward_alias(tmp_path):
    from tests.test_server_04164 import CADDY_ZEILE, _aufruf, _ordner, netz

    ziel, stub = _ordner(tmp_path)
    netz(tmp_path, [CADDY_ZEILE, "taleward_web_1 taleward-zusatz=web,"])
    r = _aufruf(ziel, stub, "liste")
    assert r.returncode != 0 and "taleward_web_1" in r.stderr


def test_s69_1_echter_caddy_bleibt_ausgenommen_und_ohne_caddy_niemand(tmp_path):
    from tests.test_server_04164 import CADDY_ZEILE, _aufruf, _ordner, netz

    ziel, stub = _ordner(tmp_path)
    netz(tmp_path, [CADDY_ZEILE, "zusatz-meine-app-1 taleward-zusatz=meine-app,"])
    assert _aufruf(ziel, stub, "liste").returncode == 0  # der echte Caddy mit Alias „caddy“ ist in Ordnung
    (tmp_path / "caddy-id").write_text("")  # Caddy läuft nicht: dann wird niemand ausgenommen
    assert _aufruf(ziel, stub, "liste").returncode != 0


def test_s69_1_update_parkt_bei_fremdem_caddy(tmp_path):
    from tests.test_server_04164 import CADDY_ZEILE, _absichern

    ziel, r = _absichern(tmp_path, {"gut.example.org.caddy": "gut"}, [
        CADDY_ZEILE, "taleward-caddy-2 taleward-zusatz=server,"])
    assert r.returncode == 0 and "ACHTUNG" in r.stdout
    assert (ziel / "extra-caddy" / "aus" / "gut.example.org.caddy").exists()


def test_s69_1_hilfsfunktionen_in_beiden_skripten_gleich():
    from tests.test_server_04164 import DEPLOY

    def block(datei):
        text = (DEPLOY / datei).read_text(encoding="utf-8")
        return text[text.index("# Wer hängt"):text.index("  return 1\n}\n", text.index("test_regel_auffrischen() {"))]

    assert block("zusatz-app.sh") == block("aktualisieren.sh")
