"""Server 0.4.135: Mails im Hausstil (Text- und HTML-Fassung aus denselben Inhalten) und Hinweis-Mails zum Guthaben
(außerhalb der App: wo aufgeladen wird, wenn etwas wartet oder das Guthaben knapp wird)."""
import re
from datetime import timedelta

import pytest
from sqlalchemy import select

from tests.test_abrechnung_0485 import _anna, _kleine_teile, _modus, _sl_anmelden, _wartende_runde  # noqa: F401
from tests.test_server_04123 import _einrichten as _smtp_einrichten
from tests.test_server_04123 import _Postausgang

pytestmark = pytest.mark.usefixtures("_kleine_teile")
ADRESSE = "https://chronik.example.org"
RLO = chr(0x202E)  # Richtungszeichen
WERTE = dict(name="Anna", benutzer="anna", link=ADRESSE + "/pfad/abc", datum="01.10.2026 12:00 UTC",
             guthaben="dein Guthaben", guthaben_gross="Dein Guthaben", wo="")


# ---------------------------------------------------------------- Vorlage
def test_jede_art_in_beiden_sprachen_ohne_reste_und_ohne_fremde_adressen(dbs):
    from app import mail, mailvorlage

    for art, sprachen in mailvorlage.INHALTE.items():
        assert set(sprachen) == {"de", "en"}, art
        for sprache in ("de", "en"):
            betreff, text, html = mail.text(dbs, art, sprache, **WERTE)
            for teil in (betreff, text, html):
                assert not re.search(r"\{\w+\}", teil), (art, sprache)
            assert f"cid:{mailvorlage.ZEICHEN_CID}" in html and "<html lang=\"" + sprache in html
            hat_knopf = mailvorlage.INHALTE[art][sprache]["knopf"] is not None
            assert (f'href="{WERTE["link"]}"' in html) == hat_knopf, art
            assert (WERTE["link"] in text) == hat_knopf, art
            # nichts von außen: die einzige Adresse ist der eigene Link
            assert set(re.findall(r"https?://[^\s\"<]+", html)) <= {WERTE["link"]}, art


def test_namen_werden_in_html_maskiert_und_gesaeubert(dbs):
    from app import mail

    betreff, text, html = mail.text(dbs, "sl_anfrage", "de", **{**WERTE, "name": "<b>Eve</b>" + RLO})
    assert "<b>Eve" not in html and "&lt;b&gt;Eve&lt;/b&gt;" in html
    assert RLO not in betreff + text + html


def test_aufbau_der_mail_mit_eingebettetem_zeichen(dbs, monkeypatch):
    from app import mail

    _smtp_einrichten(dbs, monkeypatch, "info@example.org")
    betreff, text, html = mail.text(dbs, "bestaetigen", "de", **WERTE)
    mail.senden(dbs, "spielerin@example.net", betreff, text, html)
    n = _Postausgang.gesendet[0]
    assert n.get_content_type() == "multipart/alternative"
    klar, verwandt = n.get_payload()
    assert klar.get_content_type() == "text/plain" and "Adresse bestätigen:" in klar.get_content()
    assert verwandt.get_content_type() == "multipart/related"
    seite, bild = verwandt.get_payload()
    assert seite.get_content_type() == "text/html" and bild.get_content_type() == "image/png"
    assert bild["Content-ID"] == "<taleward-zeichen>" and bild.get_content_disposition() == "inline"
    assert len(bild.get_content()) == 5099
    # Antwortadresse und Fuß sagen dasselbe; automatisch gekennzeichnet bleibt sie
    assert n["Reply-To"] == "info@example.org" and n["Auto-Submitted"] == "auto-generated"
    assert "Antworten erreichen info@example.org." in seite.get_content()
    assert "Antworten erreichen info@example.org." in klar.get_content()


def test_ohne_mail_kontakt_kein_satz_ueber_antworten(dbs, monkeypatch):
    from app import mail

    _smtp_einrichten(dbs, monkeypatch, "Verein Beispiel, Telefon 0123")
    betreff, text, html = mail.text(dbs, "passwort", "en", **WERTE)
    assert "Replies reach" not in text and "Replies reach" not in html
    mail.senden(dbs, "spielerin@example.net", betreff, text)  # ohne HTML: wie bisher nur Text
    assert _Postausgang.gesendet[0].get_content_type() == "text/plain"


# ---------------------------------------------------------------- Willkommens-Mail nach der SL-Freigabe
@pytest.fixture()
def post(monkeypatch, dbs):
    from app import mail
    from app.einstellungen import meta_schreiben

    gesendet = []
    monkeypatch.setattr(mail, "senden", lambda db, an, betreff, text, html=None: gesendet.append(
        {"an": an, "betreff": betreff, "text": text, "html": html}))
    meta_schreiben(dbs, "melden.smtp_host", "smtp.example.org")
    meta_schreiben(dbs, "angabe.public_url", ADRESSE)
    dbs.commit()
    return gesendet


def _sl_wartet(dbs, make_user):
    from app.models import User

    make_user("dora")
    u = dbs.scalar(select(User).where(User.username == "dora"))
    u.email, u.gm_permission = "dora@example.net", "pending"
    dbs.commit()
    return u


def test_willkommen_mit_weg_zum_aufladen_nur_mit_eigenem_guthaben(client, dbs, make_user, post):
    from app import slfreigabe

    u = _sl_wartet(dbs, make_user)
    assert slfreigabe.entscheiden(dbs, u, "freigeben", u)
    assert "Guthaben" not in post[-1]["text"] and ADRESSE not in post[-1]["text"]  # Abrechnung aus
    u.gm_permission = "pending"
    _modus(dbs, "guthaben")
    assert slfreigabe.entscheiden(dbs, u, "freigeben", u)
    m = post[-1]
    assert "Guthaben-Seite" in m["text"] and f"{ADRESSE}/guthaben" in m["text"]
    assert f'href="{ADRESSE}/guthaben"' in m["html"] and "€" not in m["text"]
    u.gm_permission = "pending"
    _modus(dbs, "topf")  # im Topf zahlt die Organisation – kein Hinweis aufs eigene Guthaben
    assert slfreigabe.entscheiden(dbs, u, "freigeben", u)
    assert "Guthaben" not in post[-1]["text"]


# ---------------------------------------------------------------- Hinweis-Mails zum Guthaben
def _anna_mit_adresse(dbs):
    a = _anna(dbs)
    a.email = "anna@example.net"
    dbs.commit()
    return a


def _vor(dbs, g, stunden=25):
    g.hinweis_mail_at = g.hinweis_mail_at - timedelta(hours=stunden)
    dbs.commit()


def test_wartet_auf_guthaben_einmal_und_nach_dem_aufladen_wieder_von_vorn(client, world, dbs, tmp_path, post):
    from app import abrechnung, guthaben_mail
    from app.db import utcnow
    from app.models import Job

    _modus(dbs, worker_transkription_h=120)
    _anna_mit_adresse(dbs)
    _wartende_runde(client, world, dbs, tmp_path)
    assert guthaben_mail.pruefen(dbs) == 1
    m = post[-1]
    assert m["an"] == "anna@example.net" and m["betreff"].startswith("Eine Verarbeitung wartet auf Guthaben")
    assert "in der Kampagne „Rabenfels“" in m["text"] and f"{ADRESSE}/guthaben" in m["text"]
    assert "Guthaben-Seite unter „Hinweise per E-Mail“ abbestellen" in m["text"]
    assert "€" not in m["text"] + m["html"] and not re.search(r"\d+,\d\d", m["text"])  # keine Beträge
    assert guthaben_mail.pruefen(dbs) == 0  # schon gemeldet
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    assert g.hinweise == "wartet"  # leer ohne Rest ist kein eigener Anlass
    _vor(dbs, g)
    assert guthaben_mail.pruefen(dbs) == 0  # nach einem Tag nicht noch einmal dasselbe
    abrechnung.buchen(dbs, g, "gutschrift", 5000, grund="Test")
    dbs.commit()
    assert guthaben_mail.pruefen(dbs) == 0 and g.hinweise is None  # vorbei → vergessen
    for job in dbs.scalars(select(Job).where(Job.state == "queued")):
        job.state = "done"  # die Runde ist verarbeitet
    g.hinweis_mail_at = utcnow()
    abrechnung.buchen(dbs, g, "korrektur", -6000, grund="Test")
    dbs.commit()
    assert guthaben_mail.pruefen(dbs) == 0  # die letzte Mail ist keine 24 Stunden her
    _vor(dbs, g)
    assert guthaben_mail.pruefen(dbs) == 1 and post[-1]["betreff"].startswith("Das Guthaben ist im Minus")
    assert "Rabenfels" not in post[-1]["text"]


def test_knapp_und_freigabe(client, world, dbs, tmp_path, post):
    from app import abrechnung, guthaben_mail

    _modus(dbs, worker_transkription_h=36000)  # 30 s ≈ 3 € → über der Freigabe von 2 €
    _anna_mit_adresse(dbs)
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    abrechnung.buchen(dbs, g, "gutschrift", 100, grund="Test")  # unter der Warnschwelle von 1,50 €
    dbs.commit()
    assert guthaben_mail.pruefen(dbs) == 1 and post[-1]["betreff"].startswith("Das Guthaben wird knapp")
    _wartende_runde(client, world, dbs, tmp_path)
    assert guthaben_mail.pruefen(dbs) == 0  # höchstens eine am Tag
    _vor(dbs, g)
    assert guthaben_mail.pruefen(dbs) == 1
    assert post[-1]["betreff"].startswith("Eine Verarbeitung wartet auf „Verarbeiten“")
    assert "Rabenfels" in post[-1]["text"]


def test_abbestellt_ohne_adresse_oder_ohne_oeffentliche_adresse_keine_mail(client, world, dbs, tmp_path, post):
    from app import guthaben_mail
    from app.einstellungen import meta_schreiben

    _modus(dbs)
    _wartende_runde(client, world, dbs, tmp_path)
    assert guthaben_mail.pruefen(dbs) == 0  # Anna hat keine bestätigte Adresse
    a = _anna_mit_adresse(dbs)
    a.guthaben_mails_aus = True
    dbs.commit()
    assert guthaben_mail.pruefen(dbs) == 0
    a.guthaben_mails_aus = False
    meta_schreiben(dbs, "angabe.public_url", "")
    dbs.commit()
    assert guthaben_mail.pruefen(dbs) == 0 and post == []
    meta_schreiben(dbs, "angabe.public_url", ADRESSE)
    _modus(dbs, "aus")
    assert guthaben_mail.pruefen(dbs) == 0
    _modus(dbs, "guthaben")
    assert guthaben_mail.pruefen(dbs) == 1


def test_topf_an_die_verwalter_ohne_kampagnentitel(client, world, dbs, tmp_path, post, make_user):
    from app import guthaben_mail
    from app.models import Campaign, OrgMember, Organization, User

    _modus(dbs, "topf")
    c = dbs.get(Campaign, world["cid"])
    org = dbs.get(Organization, c.organization_id)
    make_user("vera")
    vera = dbs.scalar(select(User).where(User.username == "vera"))
    vera.email, vera.gm_sprache = "vera@example.net", "en"
    om = dbs.get(OrgMember, (org.id, vera.id))
    if om is None:
        dbs.add(OrgMember(organization_id=org.id, user_id=vera.id, role="admin"))
    else:
        om.role = "admin"
    _anna_mit_adresse(dbs)  # SL, aber keine Verwalterin: bekommt nichts
    dbs.commit()
    _wartende_runde(client, world, dbs, tmp_path)
    assert guthaben_mail.pruefen(dbs) == 1
    m = post[-1]
    assert m["an"] == "vera@example.net" and m["betreff"].startswith("Something is waiting for credit")
    assert f"The credit of the organisation “{org.name}”" in m["text"]
    assert "Rabenfels" not in m["text"] + m["html"]  # Vera ist dort nicht SL (G14)


def test_gesperrtes_guthaben_loest_nichts_aus(client, world, dbs, tmp_path, post):
    from app import abrechnung, guthaben_mail
    from app.db import utcnow

    _modus(dbs)
    _anna_mit_adresse(dbs)
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    abrechnung.buchen(dbs, g, "korrektur", -100, grund="Test")
    g.gesperrt_at = utcnow()
    dbs.commit()
    _wartende_runde(client, world, dbs, tmp_path)
    assert guthaben_mail.pruefen(dbs) == 0


def test_seite_abbestellen_und_wieder_bestellen(client, world, dbs, post):
    from app import abrechnung

    _modus(dbs)
    _anna_mit_adresse(dbs)
    csrf, seite = _sl_anmelden(client)
    assert "Hinweise per E-Mail" in seite and "anna@example.net" in seite and "Abbestellen" in seite
    r = client.post("/guthaben/hinweis-mails", data={"csrf": csrf, "an": "0"}, follow_redirects=False)
    assert r.status_code == 303 and "mails_aus" in r.headers["location"]
    dbs.expire_all()
    assert _anna(dbs).guthaben_mails_aus
    seite = client.get("/guthaben?ok=mails_aus").text
    assert "Hinweise per E-Mail sind abbestellt." in seite and "Wieder bestellen" in seite
    client.post("/guthaben/hinweis-mails", data={"csrf": csrf, "an": "1"})
    dbs.expire_all()
    assert not _anna(dbs).guthaben_mails_aus
    assert client.post("/guthaben/hinweis-mails", data={"csrf": "falsch", "an": "0"}).status_code in (400, 403)
    abrechnung.modus_schreiben(dbs, "aus", "test")
    dbs.commit()
    assert client.post("/guthaben/hinweis-mails", data={"csrf": csrf, "an": "0"}).status_code == 404


def test_ohne_mailserver_kein_abschnitt(client, world, dbs):
    from app.einstellungen import meta_schreiben

    _modus(dbs)
    meta_schreiben(dbs, "angabe.public_url", ADRESSE)
    dbs.commit()
    _csrf, seite = _sl_anmelden(client)
    assert "Hinweise per E-Mail" not in seite  # kein Mailserver eingerichtet


def test_mails_in_der_sprache_der_letzten_anmeldung(client, world, dbs, tmp_path, post):
    from app import guthaben_mail, mail

    _modus(dbs)
    a = _anna_mit_adresse(dbs)
    assert a.sprache is None and mail.sprache_fuer(dbs, a) == "de"  # ohne Angabe: Sprache des Servers
    r = client.post("/api/v1/auth/login", json={"username": "anna", "password": "geheim123"},
                    headers={"Accept-Language": "en-GB,en;q=0.9,de;q=0.5"})
    assert r.status_code == 200
    dbs.expire_all()
    assert _anna(dbs).sprache == "en"
    _wartende_runde(client, world, dbs, tmp_path)
    assert guthaben_mail.pruefen(dbs) == 1
    assert post[-1]["betreff"].startswith("Something is waiting for credit")
    assert "in the campaign “Rabenfels”" in post[-1]["text"] and "Your credit" in post[-1]["text"]
    # ohne passende Angabe bleibt die gemerkte Sprache
    client.post("/api/v1/auth/login", json={"username": "anna", "password": "geheim123"},
                headers={"Accept-Language": "fr-FR"})
    dbs.expire_all()
    assert _anna(dbs).sprache == "en"
    client.post("/api/v1/auth/login", json={"username": "anna", "password": "geheim123"},
                headers={"Accept-Language": "de-DE"})
    dbs.expire_all()
    assert _anna(dbs).sprache == "de"
