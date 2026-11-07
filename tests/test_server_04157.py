"""Server 0.4.157: Hinweis auf eine geänderte Datenschutz-Vorlage, Knopf „Erstatten“, feste Erweitert-Optionen,
Ablaufdatum der Anmeldeschlüssel mit Erinnerung, Kampagnentitel auf der Einladungsseite."""
import json
from datetime import timedelta

import httpx
import pytest

from app.db import utcnow
from app.einstellungen import meta_lesen, meta_schreiben
from tests.test_betrieb import einrichten, ntfy  # noqa: F401 (Fixture)
from tests.test_server_04133 import _einrichten
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_server_04153 import _bezahlt, _konto
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


# ---------------------------------------------------------------- Datenschutz-Vorlage
def test_hinweis_wenn_vorlage_sich_aendert(client, dbs, admin):  # noqa: F811
    from app import datenschutz

    assert not datenschutz.vorlage_neuer(dbs)  # nichts veröffentlicht
    meta_schreiben(dbs, "datenschutz.text", "Unser Text.")
    dbs.commit()
    assert datenschutz.vorlage_neuer(dbs)  # veröffentlicht, aber nie mit der Vorlage abgeglichen
    seite = client.get("/verwaltung/").text
    assert "Die Vorlage für den Datenschutzhinweis hat sich geändert." in seite
    seite = client.get("/verwaltung/assistent/datenschutz").text
    assert "Aktuelle Vorlage ansehen" in seite
    r = client.post("/verwaltung/assistent/datenschutz", data={"csrf": admin, "aktion": "gesehen"},
                    follow_redirects=False)
    assert r.status_code in (200, 303)
    dbs.expire_all()
    assert not datenschutz.vorlage_neuer(dbs)
    assert "hat sich geändert" not in client.get("/verwaltung/").text
    # Eine andere Einstellung ändert die Vorlage → der Hinweis kommt wieder
    meta_schreiben(dbs, "datenschutz.vorlage_stand", "anders")
    dbs.commit()
    assert datenschutz.vorlage_neuer(dbs)


# ---------------------------------------------------------------- Erstatten
class Anbieter:
    def __init__(self, stripe=200, paypal=201):
        self.anfragen: list[httpx.Request] = []
        self.stripe, self.paypal = stripe, paypal

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.anfragen.append(req)
        if req.url.path == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21"})
        if req.url.path == "/v1/refunds":
            return httpx.Response(self.stripe, json={"id": "re_1", "status": "succeeded"})
        if req.url.path.startswith("/v2/payments/captures/"):
            return httpx.Response(self.paypal, json={"id": "RF1", "status": "COMPLETED"})
        return httpx.Response(404)


@pytest.fixture()
def anbieter(monkeypatch):
    from app import zahlung

    a = Anbieter()
    monkeypatch.setattr(zahlung, "_client", httpx.Client(transport=httpx.MockTransport(a)))
    return a


def _fall(dbs):
    from app import abrechnung

    _einrichten(dbs)
    u = _konto(dbs, "erna")
    g = abrechnung.guthaben_konto(dbs, u.id)
    _bezahlt(dbs, g, 1000, 1060, tage=300, ref="pi_altaltalt1")  # Frist vorbei → von Hand
    _bezahlt(dbs, g, 2000, 2090, anbieter="paypal", tage=5, ref="CAPTUREPAYPAL1")
    _bezahlt(dbs, g, 500, 545, tage=1, ref="pi_neuneuneu1")
    dbs.commit()
    return g


def test_erstatten_stoesst_beide_anbieter_an(client, dbs, admin, anbieter):  # noqa: F811
    from app import erstattung

    g = _fall(dbs)
    seite = client.get("/verwaltung/abrechnung", params={"erstattung": "erna"}).text
    assert 'action="/verwaltung/abrechnung/erstattung/ausfuehren"' in seite and "35,00 € jetzt erstatten" in seite
    pfad = "/verwaltung/abrechnung/erstattung/ausfuehren"
    # ohne zweiten Faktor nicht
    r = client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1"}, follow_redirects=False)
    assert not (r.status_code == 303 and "erstattung_angestossen" in r.headers.get("location", ""))
    assert anbieter.anfragen == []
    _faktor_an(client, dbs, _chef(dbs).id)
    assert client.post(pfad, data={"csrf": admin, "guthaben": g.id}).status_code == 400  # ohne Haken
    assert client.post(pfad, data={"csrf": admin, "guthaben": "gibt-es-nicht", "bestaetigt": "1"}).status_code == 404
    r = client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1"})
    assert r.status_code == 200 and "Bleibt von Hand zu überweisen: 10,00 €" in r.text
    stripe = [a for a in anbieter.anfragen if a.url.path == "/v1/refunds"]
    paypal = [a for a in anbieter.anfragen if "/captures/" in a.url.path]
    assert len(stripe) == 1 and b"payment_intent=pi_neuneuneu1" in stripe[0].content and b"amount=545" in stripe[0].content
    assert stripe[0].headers["Idempotency-Key"].startswith("erstattung-")
    assert len(paypal) == 1 and paypal[0].url.path == "/v2/payments/captures/CAPTUREPAYPAL1/refund"
    assert json.loads(paypal[0].content)["amount"] == {"value": "20.90", "currency_code": "EUR"}
    assert paypal[0].url.host == "api-m.sandbox.paypal.com"
    # gebucht wird erst mit der Meldung: das Guthaben ist unverändert
    assert erstattung.erstattbar(dbs, g) == 3500
    # ein zweiter Klick, solange die Meldung fehlt, geht nicht an den Anbieter (0.4.158, S64)
    assert client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1"}).status_code == 409
    assert len([a for a in anbieter.anfragen if a.url.path == "/v1/refunds"]) == 1
    # „Erneut anstoßen“ innerhalb von 24 h: derselbe Schlüssel – der Anbieter führt ihn nur einmal aus
    client.post(pfad, data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1", "erneut": "1"})
    stripe2 = [a for a in anbieter.anfragen if a.url.path == "/v1/refunds"]
    assert stripe2[1].headers["Idempotency-Key"] == stripe[0].headers["Idempotency-Key"]


def test_erstatten_meldet_abgelehnte_zahlung(client, dbs, admin, anbieter):  # noqa: F811
    from app.models import AbrechnungProtokoll

    g = _fall(dbs)
    anbieter.paypal = 422
    _faktor_an(client, dbs, _chef(dbs).id)
    r = client.post("/verwaltung/abrechnung/erstattung/ausfuehren",
                    data={"csrf": admin, "guthaben": g.id, "bestaetigt": "1"})
    assert r.status_code == 502 and "CAPTUREPAYPAL1" in r.text
    arten = {z.was for z in dbs.query(AbrechnungProtokoll).all()}
    assert {"erstattung_angestossen", "erstattung_fehlgeschlagen"} <= arten


def test_fremde_zahlungsbezuege_gehen_nie_raus(dbs, anbieter):
    from app import abrechnung, erstattung

    _einrichten(dbs)
    g = abrechnung.guthaben_konto(dbs, _konto(dbs, "erna").id)
    _bezahlt(dbs, g, 500, 545, ref="../v1/charges")
    dbs.commit()
    e = erstattung.erstatten(dbs, g, "test")
    assert e.fehlgeschlagen == ["../v1/charges"] and anbieter.anfragen == []


# ---------------------------------------------------------------- Erweitert-Optionen fest
def test_erweitert_optionen_fest(client, dbs, admin):  # noqa: F811
    from app.einstellungen import llm_konfig
    from app.zusammenfassung import pruefansicht_art

    meta_schreiben(dbs, "llm.weg_lokal", "notizen")
    meta_schreiben(dbs, "pruefung.ansicht", "gegenpruefung")
    meta_schreiben(dbs, "llm.api_modell_notizen", "mistral-small-latest")
    dbs.commit()
    seite = client.get("/verwaltung/zusammenfassung").text
    assert 'name="weg_lokal"' not in seite and 'name="pruefansicht"' not in seite
    assert 'name="api_modell_notizen"' not in seite and 'name="erweitert_fest"' in seite
    r = client.post("/verwaltung/zusammenfassung", data={
        "csrf": admin, "art": "lokal", "anbieter": "mistral", "api_modell": "mistral-medium-latest",
        "lokal_modell": "auto", "lokal_kontext": "12288", "erweitert_fest": "1", "gegenpruefen_feld": "1"},
        follow_redirects=False)
    assert r.status_code == 303, r.text[:500]
    dbs.expire_all()
    k = llm_konfig(dbs)
    assert k.weg_lokal == "abschrift" and k.api_modell_notizen == "" and pruefansicht_art(dbs) == "pflicht"


# ---------------------------------------------------------------- Ablaufdatum der Anmeldeschlüssel
def test_ablaufdatum_und_erinnerung(client, dbs, admin, ntfy):  # noqa: F811
    from app import anmeldedienste, benachrichtigung

    bald = (utcnow() + timedelta(days=20)).date()
    r = client.post("/verwaltung/anmeldung/microsoft", data={
        "csrf": admin, "client_id": "abc", "client_secret": "geheim-geheim", "ablauf": bald.isoformat()},
        follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert anmeldedienste.ablauf(dbs, "microsoft") == bald
    seite = client.get("/verwaltung/anmeldung").text
    assert 'name="ablauf"' in seite and "Schlüssel läuft in 20 Tagen ab" in seite
    assert "Der Schlüssel für Microsoft läuft in 20 Tagen ab." in client.get("/verwaltung/").text
    assert client.post("/verwaltung/anmeldung/microsoft", data={
        "csrf": admin, "client_id": "abc", "ablauf": "kein-datum"}).status_code == 400
    einrichten(dbs)
    assert "anmeldeschluessel:microsoft" in benachrichtigung.pruefen(dbs)
    assert any("Microsoft" in n.get("title", "") for n in ntfy)
    assert "anmeldeschluessel:microsoft" not in benachrichtigung.pruefen(dbs)  # je Stufe nur einmal
    meta_schreiben(dbs, "oidc.microsoft.ablauf", (utcnow() + timedelta(days=5)).date().isoformat())
    dbs.commit()
    assert "anmeldeschluessel:microsoft" in benachrichtigung.pruefen(dbs)  # 7-Tage-Stufe
    meta_schreiben(dbs, "oidc.microsoft.ablauf", (utcnow() - timedelta(days=1)).date().isoformat())
    dbs.commit()
    assert "anmeldeschluessel:microsoft" in benachrichtigung.pruefen(dbs)  # abgelaufen
    assert "abgelaufen" in client.get("/verwaltung/anmeldung").text
    # Dienst ausschalten nimmt das Datum mit
    client.post("/verwaltung/anmeldung/microsoft", data={"csrf": admin, "entfernen": "1"})
    dbs.expire_all()
    assert anmeldedienste.ablauf(dbs, "microsoft") is None and not meta_lesen(dbs, "oidc.microsoft.client_secret")


# ---------------------------------------------------------------- Einladungsseite
def test_einladungsseite_zeigt_kampagnentitel(client, dbs, world):
    from app.models import Campaign

    titel = dbs.get(Campaign, world["cid"]).title
    code = client.post(f"{API}/campaigns/{world['cid']}/invites", headers=world["gm"]).json()["code"]
    r = client.get(f"/einladung/{code}")
    assert r.status_code == 200 and f"Kampagne: {titel}" in r.text
    assert r.headers["Cache-Control"] == "no-store"
    assert titel not in r.text.split("</head>")[0]  # nie in den Vorschau-Angaben
    assert "Kampagne:" not in client.get("/einladung/RABE-12345678").text


# ---------------------------------------------------------------- F1 (Sicherheits-Chat 07.11.)
def test_kopplungsbefehl_nennt_die_serveradresse(client, admin):  # noqa: F811
    client.post("/verwaltung/assistent/koppeln", data={"csrf": admin, "weiter": "/verwaltung/transkription"})
    seite = client.get("/verwaltung/transkription").text
    assert "--server http://testserver</code>" in seite
    assert "<dd><code>http://testserver</code></dd>" in seite
