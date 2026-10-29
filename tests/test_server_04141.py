"""Server 0.4.141: Absender mit Anzeigename, bestätigte Adresse nicht doppelt, Ortszeit in Konto-Mails, Zähler-Kacheln
als Links mit Filter der Warteschlange, Worker „arbeitet“, Segnungen in der DSA-Liste."""
from datetime import datetime, timezone

from sqlalchemy import func
from email.utils import parseaddr

import pytest

from tests.test_anmeldung import API, post  # noqa: F401
from tests.test_server_04136 import post as konto_post  # noqa: F401
from tests.test_server_04123 import _Postausgang, _einrichten
from tests.test_verwaltung import admin  # noqa: F401


# ---------------------------------------------------------------- Absender
def test_absender_traegt_den_servernamen(dbs, monkeypatch):
    from app import mail
    from app.einstellungen import meta_schreiben

    _einrichten(dbs, monkeypatch, "info@example.org")
    meta_schreiben(dbs, "angabe.server_name", "Taleward Communityserver")
    dbs.commit()
    mail.senden(dbs, "spielerin@example.net", "Betreff", "Text")
    n = _Postausgang.gesendet[0]
    assert parseaddr(n["From"]) == ("Taleward Communityserver", "noreply@example.org")
    assert n["Reply-To"] == "info@example.org"
    assert n["Message-ID"].endswith("@example.org>")


def test_absendername_ohne_steuerzeichen_und_nie_leer():
    from app.benachrichtigung import absendername

    assert absendername("Verein " + chr(0x202E) + "Beispiel\n") == "Verein Beispiel"
    assert absendername("") == "Taleward" and absendername(None) == "Taleward"
    assert len(absendername("x" * 200)) == 80


def test_betreiber_meldung_mit_anzeigename(dbs, monkeypatch, admin):  # noqa: F811
    from app import benachrichtigung
    from app.einstellungen import meta_schreiben

    _einrichten(dbs, monkeypatch, "info@example.org")
    meta_schreiben(dbs, "melden.email_an", "chef@example.org")
    dbs.commit()
    assert benachrichtigung.senden(dbs, "Test", "Text") == []
    assert parseaddr(_Postausgang.gesendet[-1]["From"])[1] == "noreply@example.org"
    assert parseaddr(_Postausgang.gesendet[-1]["From"])[0]


# ---------------------------------------------------------------- E-Mail-Adresse nicht doppelt
def test_bestaetigte_adresse_nicht_noch_einmal(client, world, dbs, post):  # noqa: F811
    from sqlalchemy import select

    from app.models import User

    w = world
    post.einrichten(dbs)
    u = dbs.scalar(select(User).where(User.username == "ben"))
    u.email = "ben@example.org"
    dbs.commit()
    r = client.put(f"{API}/me/email", json={"email": "Ben@Example.org"}, headers=w["pl"])
    assert r.status_code == 409 and r.json()["code"] == "email_confirmed"
    assert "schon bestätigt" in r.json()["message"]
    assert len(post) == 0  # keine Mail, kein Vorgang
    me = client.get(f"{API}/me", headers=w["pl"]).json()
    assert me["email"] == "ben@example.org" and me["emailPending"] is None
    # Eine andere Adresse geht weiterhin
    assert client.put(f"{API}/me/email", json={"email": "neu@example.org"}, headers=w["pl"]).status_code == 202
    assert len(post) == 1


def test_offener_vorgang_zur_bestaetigten_adresse_wird_verworfen(client, world, dbs, post):  # noqa: F811
    from sqlalchemy import select

    from app.models import User

    w = world
    post.einrichten(dbs)
    assert client.put(f"{API}/me/email", json={"email": "ben@example.org"}, headers=w["pl"]).status_code == 202
    u = dbs.scalar(select(User).where(User.username == "ben"))
    u.email = "ben@example.org"  # so sah es nach dem Bestätigen in alten Fassungen aus: bestätigt und noch offen
    dbs.commit()
    assert client.put(f"{API}/me/email", json={"email": "ben@example.org"}, headers=w["pl"]).status_code == 409
    dbs.expire_all()
    u = dbs.scalar(select(User).where(User.username == "ben"))
    assert u.email == "ben@example.org" and u.email_pending is None
    from sqlalchemy import func

    from app.models import EinmalToken

    assert dbs.scalar(select(func.count()).select_from(EinmalToken).where(EinmalToken.user_id == u.id,
                                                                           EinmalToken.art == "email_bestaetigen")) == 0


# ---------------------------------------------------------------- Ortszeit
def test_ortszeit_je_sprache():
    from zoneinfo import ZoneInfo

    from app.benachrichtigung import ortszeit

    wann = datetime(2026, 10, 29, 8, 28, tzinfo=timezone.utc)
    assert ortszeit(wann, ZoneInfo("Europe/Berlin"), "de") == "29.10.2026, 09:28 Uhr (MEZ)"
    assert ortszeit(wann, ZoneInfo("Europe/Berlin"), "en") == "2026-10-29 09:28 CET"
    sommer = datetime(2026, 7, 1, 8, 28, tzinfo=timezone.utc)
    assert ortszeit(sommer, ZoneInfo("Europe/Berlin"), "de") == "01.07.2026, 10:28 Uhr (MESZ)"
    assert ortszeit(wann, timezone.utc, "de") == "29.10.2026, 08:28 Uhr (UTC)"


def test_zeitzone_einstellbar_nur_aus_der_liste(client, dbs, admin):  # noqa: F811
    from app import benachrichtigung

    assert benachrichtigung.konfig(dbs).zeitzone == "Europe/Berlin"
    seite = client.get("/verwaltung/einstellungen").text
    assert 'name="zeitzone"' in seite and 'value="Europe/Berlin" selected' in seite
    r = client.post("/verwaltung/benachrichtigungen", data={"csrf": admin, "zeitzone": "Mars/Olympus"})
    assert r.status_code == 400
    r = client.post("/verwaltung/benachrichtigungen", data={"csrf": admin, "zeitzone": "Europe/London"},
                    follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert benachrichtigung.konfig(dbs).zeitzone == "Europe/London"
    assert str(benachrichtigung.zeitzone(dbs)) == "Europe/London"


def test_konto_hinweis_in_ortszeit(client, dbs, make_user, konto_post):  # noqa: F811
    from tests import test_server_04136 as t36

    t36._mit_adresse(dbs, make_user)
    assert t36._anmelden(client).status_code == 200
    assert len(konto_post) == 1 and " Uhr (ME" in konto_post[0]["text"] and "UTC" not in konto_post[0]["text"]


# ---------------------------------------------------------------- Übersicht und Warteschlange
def test_kacheln_sind_links_und_filtern_die_warteschlange(client, world, dbs, admin):  # noqa: F811
    from app.models import GameSession
    from tests.test_step2a import neue_session

    seite = client.get("/verwaltung/").text
    assert 'href="/verwaltung/transkription"><span class="kennzahlen__titel">Worker bereit' in seite
    for ziel in ("wartet", "arbeit", "fehler", "sl"):
        assert f'href="/verwaltung/warteschlange?zeigen={ziel}"' in seite
    assert ">Ansehen<" not in seite.split("kennzahlen")[1].split("</div>")[0]
    s1 = neue_session(client, world)
    s2 = neue_session(client, world)
    from app.models import Job

    for sid, zustand in ((s1["id"], "failed"), (s2["id"], "awaiting_review")):
        s = dbs.get(GameSession, sid)
        s.state = zustand
        dbs.add(Job(type="transcribe", session_id=sid, state="failed" if zustand == "failed" else "done",
                    required_capability="asr"))
    dbs.commit()
    alle = client.get("/verwaltung/warteschlange").text
    assert "die letzten 100 Aufträge" in alle
    nur_fehler = client.get("/verwaltung/warteschlange?zeigen=fehler").text
    assert "Nur fehlgeschlagene Runden" in nur_fehler and "Alle Aufträge anzeigen" in nur_fehler
    nur_sl = client.get("/verwaltung/warteschlange?zeigen=sl").text
    assert "Nur Runden, die auf die Spielleitung warten" in nur_sl
    assert "Only rounds waiting for the game master" in client.get("/verwaltung/warteschlange?zeigen=sl",
                                                                    headers={"Accept-Language": "en"}).text
    # Unbekannter Filter: alles wie ohne
    assert "die letzten 100 Aufträge" in client.get("/verwaltung/warteschlange?zeigen=x").text


def test_worker_zeigt_arbeitet_waehrend_eines_auftrags(client, world, dbs, admin):  # noqa: F811
    from app.db import utcnow
    from app.models import GameSession, Job, Worker
    from tests.test_step2a import neue_session

    w = Worker(name="Kim", token_hash="x" * 64, last_seen_at=utcnow())
    dbs.add(w)
    s = dbs.get(GameSession, neue_session(client, world)["id"])
    dbs.add(Job(type="transcribe", session_id=s.id, state="leased", required_capability="asr",
                lease_worker_id=w.id, lease_expires_at=utcnow(), progress=0.4))
    dbs.commit()
    zeile = client.get("/verwaltung/transkription").text.split("<strong>Kim</strong>")[1].split("</tr>")[0]
    assert ">arbeitet<" in zeile and ">bereit<" not in zeile and "40 %" in zeile
    assert ">working<" in client.get("/verwaltung/transkription", headers={"Accept-Language": "en"}).text


# ---------------------------------------------------------------- DSA-Segnungen
def test_segnungen_sind_fachwoerter_und_fliegen_aus_dem_kapitel():
    from app import artefakte, begriffe

    fach, alltag = begriffe.regelwoerter("dsa", frozenset({"segen", "schmerz", "liturgie"}))
    assert {"Heilungssegen", "Glückssegen", "Schutzsegen"} <= set(fach)
    assert "Liturgie" in alltag and "Schmerz" in alltag
    text = ("Stattdessen traf ihn nur ein Schlag, doch Orasilas’ Heilungssegen lindert den Schmerz und ein Glückssegen "
            "wurde ihm gewährt. Der Gedanke an eine Liturgie gegen den Unschuldigen wurde verworfen.")
    aus, befunde = artefakte.kapitel(text, [], [], fach, alltag)
    assert "Heilungssegen" not in aus and "Glückssegen" not in aus
    assert "Liturgie" in aus  # Alltagswort ohne Zahl bleibt Erzählung
    assert any(b["art"] == "regel" for b in befunde)


@pytest.mark.parametrize("wort", ["Heilungssegen", "Glückssegen", "Weisheitssegen"])
def test_segnungen_stehen_in_der_dsa_liste(wort):
    from app import begriffe

    assert wort in begriffe.liste("dsa").regel["de"]


# ---------------------------------------------------------------- statische Dateien mit Fassung
def test_stilvorlage_und_skripte_mit_fassung(client, admin):  # noqa: F811
    """Firefox hielt nach dem Update die alte Stilvorlage im Zwischenspeicher – die Adresse trägt jetzt die Fassung."""
    from app.aktualisierung import eigene_fassung

    seite = client.get("/verwaltung/anmeldung").text
    assert f'href="/verwaltung/static/verwaltung.css?v={eigene_fassung()}"' in seite
    assert f'src="/verwaltung/static/code-kaesten.js?v={eigene_fassung()}"' in seite
    assert client.get(f"/verwaltung/static/verwaltung.css?v={eigene_fassung()}").status_code == 200


# ---------------------------------------------------------------- bekannte Geräte
def test_browser_anmeldung_mailt_nur_bei_neuem_geraet(client, dbs, konto_post):  # noqa: F811
    from sqlalchemy import select

    from app import geraete
    from app.cli import _neues_konto
    from app.models import BekanntesGeraet, User

    _neues_konto(dbs, "chef", "Chefin", "geheim123", admin=True)
    u = dbs.scalar(select(User).where(User.username == "chef"))
    u.email = "chef@example.net"
    dbs.commit()
    r = client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303 and geraete.COOKIE in r.headers["set-cookie"]
    cookie = [c for c in r.headers.get_list("set-cookie") if c.startswith(geraete.COOKIE)][0]
    assert "HttpOnly" in cookie and "SameSite=strict" in cookie and "Path=/;" in cookie
    assert len(konto_post) == 1 and "Neue Anmeldung" in konto_post[0]["betreff"]
    assert dbs.scalar(select(BekanntesGeraet)).user_id == u.id
    # zweite Anmeldung aus demselben Browser: keine Mail (auch nach dem 15-Minuten-Fenster)
    from app.begrenzung import ZAEHLER

    ZAEHLER.vergessen()
    client.post("/verwaltung/abmelden", data={"csrf": "x"})
    r = client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303 and len(konto_post) == 1
    # neuer Browser ohne Cookie: wieder eine Mail und ein zweites Gerät
    client.cookies.clear()
    ZAEHLER.vergessen()
    r = client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303 and len(konto_post) == 2
    assert dbs.scalar(select(func.count()).select_from(BekanntesGeraet)) == 2


def test_fremdes_konto_mit_gleichem_cookie_ist_neu(client, dbs, konto_post):  # noqa: F811
    from sqlalchemy import select

    from app.cli import _neues_konto
    from app.models import User

    for name in ("chef", "zwei"):
        _neues_konto(dbs, name, name, "geheim123", admin=True)
        dbs.scalar(select(User).where(User.username == name)).email = f"{name}@example.net"
    dbs.commit()
    client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    client.post("/verwaltung/abmelden", data={"csrf": "x"})
    client.post("/verwaltung/anmelden", data={"username": "zwei", "password": "geheim123"}, follow_redirects=False)
    assert [m["an"] for m in konto_post] == ["chef@example.net", "zwei@example.net"]


def test_neues_passwort_vergisst_geraete_und_wartung_raeumt_auf(client, dbs):
    from datetime import timedelta

    from sqlalchemy import select

    from app import geraete, passwortlink
    from app.cli import _neues_konto
    from app.db import utcnow
    from app.models import BekanntesGeraet, User

    _neues_konto(dbs, "chef", "Chefin", "geheim123", admin=True)
    u = dbs.scalar(select(User).where(User.username == "chef"))
    dbs.commit()
    r = client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303, r.text[:300]
    dbs.expire_all()
    assert dbs.scalar(select(func.count()).select_from(BekanntesGeraet)) == 1
    passwortlink.passwort_setzen(dbs, u, "neuesgeheim123", ort="app")
    dbs.commit()
    assert dbs.scalar(select(func.count()).select_from(BekanntesGeraet)) == 0
    dbs.add(BekanntesGeraet(id="a" * 64, user_id=u.id, ort="verwaltung", last_seen_at=utcnow() - timedelta(days=181)))
    dbs.add(BekanntesGeraet(id="b" * 64, user_id=u.id, ort="verwaltung"))
    dbs.commit()
    assert geraete.aufraeumen(dbs) == 1
    assert dbs.scalar(select(BekanntesGeraet)).id == "b" * 64
