"""Server 0.4.133: Aufladen über Stripe und PayPal (S28 G4, G6, G9, G11) und die neue Seite „Guthaben“.
Die Anbieter sind durch einen nachgestellten Gegenüber ersetzt – es geht nichts ins Netz."""
import hashlib
import hmac
import json
import re
import time

import httpx
import pytest
from sqlalchemy import select

from tests.test_abrechnung_0485 import _anna, _modus, _sl_anmelden
from tests.test_verwaltung import admin, csrf_von  # noqa: F401 (Fixture)

WEBHOOK = "whsec_testgeheimnis"


class Anbieter:
    """Nachgestellte Schnittstellen von Stripe und PayPal; merkt sich die Anfragen."""

    def __init__(self):
        self.anfragen: list[httpx.Request] = []
        self.capture = {"status": "COMPLETED"}
        self.verifiziert = True

    def __call__(self, req: httpx.Request) -> httpx.Response:
        self.anfragen.append(req)
        pfad = req.url.path
        if req.url.host == "api.stripe.com" and pfad == "/v1/checkout/sessions":
            return httpx.Response(200, json={"id": "cs_test_1", "url": "https://checkout.stripe.com/c/pay/cs_test_1"})
        if pfad == "/v1/oauth2/token":
            return httpx.Response(200, json={"access_token": "A21"})
        if pfad == "/v2/checkout/orders":
            daten = json.loads(req.content)
            self.paypal_betrag = daten["purchase_units"][0]["amount"]
            self.paypal_vorgang = daten["purchase_units"][0]["custom_id"]
            return httpx.Response(201, json={"id": "ORDER1", "links": [
                {"rel": "payer-action", "href": "https://www.sandbox.paypal.com/checkoutnow?token=ORDER1"}]})
        if pfad == "/v2/checkout/orders/ORDER1/capture":
            return httpx.Response(201, json={"purchase_units": [{"payments": {"captures": [{
                "id": "CAP1", "custom_id": self.paypal_vorgang, "amount": self.paypal_betrag, **self.capture}]}}]})
        if pfad == "/v1/notifications/verify-webhook-signature":
            return httpx.Response(200, json={"verification_status": "SUCCESS" if self.verifiziert else "FAILURE"})
        return httpx.Response(404)


@pytest.fixture()
def anbieter(monkeypatch):
    from app import zahlung

    a = Anbieter()
    monkeypatch.setattr(zahlung, "_client", httpx.Client(transport=httpx.MockTransport(a)))
    return a


def _einrichten(dbs, stripe=True, paypal=True, modus="guthaben"):
    from app import zahlung
    from app.einstellungen import meta_schreiben

    _modus(dbs, modus)
    meta_schreiben(dbs, "angabe.public_url", "https://chronik.example.org")
    zahlung.einstellungen_schreiben(dbs, "test", stripe_schluessel="sk_test_abc" if stripe else "",
                                    stripe_webhook=WEBHOOK if stripe else "",
                                    paypal_id="client" if paypal else "", paypal_geheim="geheim" if paypal else "",
                                    paypal_webhook_id="WH-1" if paypal else "")
    dbs.commit()


def _signiert(daten: dict, geheimnis=WEBHOOK, t=None) -> tuple[bytes, dict]:
    roh = json.dumps(daten).encode()
    t = int(t or time.time())
    sig = hmac.new(geheimnis.encode(), f"{t}.".encode() + roh, hashlib.sha256).hexdigest()
    return roh, {"Stripe-Signature": f"t={t},v1={sig}", "Content-Type": "application/json"}


def _aufladen(client, dbs, anbieter_name="stripe", betrag=1000):
    from app import abrechnung

    csrf, seite = _sl_anmelden(client)
    gid = abrechnung.guthaben_konto(dbs, _anna(dbs).id).id
    r = client.post("/guthaben/aufladen", data={"csrf": csrf, "guthaben_id": gid, "betrag": str(betrag),
                                                "anbieter": anbieter_name})
    assert r.status_code == 200, r.text
    weiter = client.post("/guthaben/aufladen/weiter", data={"csrf": csrf, "guthaben_id": gid, "betrag": str(betrag),
                                                            "anbieter": anbieter_name}, follow_redirects=False)
    return r, weiter, gid


def _vorgang(dbs):
    from app.models import Bezahlvorgang

    dbs.expire_all()
    return dbs.scalars(select(Bezahlvorgang).order_by(Bezahlvorgang.angelegt.desc())).first()


def _saldo(dbs, gid):
    from app import abrechnung

    dbs.expire_all()
    return abrechnung.saldo(dbs, gid)


# ---------------------------------------------------------------- Seite und Stripe
def test_seite_ohne_zahlwege_und_gebuehr_vor_der_zahlung(client, world, dbs, anbieter):
    _modus(dbs)
    _csrf, seite = _sl_anmelden(client)
    assert "Aufladen mit Bezahlung gibt es auf diesem Server noch nicht" in seite and 'class="guthaben__stand' in seite
    assert "taleward-lockup.svg" in seite  # mit Logo
    _einrichten(dbs)
    _csrf, seite = _sl_anmelden(client)
    assert "Guthaben aufladen" in seite and "10,00 €" in seite and "50,00 €" in seite and "Testmodus" in seite
    r, weiter, gid = _aufladen(client, dbs)
    assert "Du zahlst" in r.text and "10,41 €" in r.text and "Weiter zu Stripe" in r.text  # (10 € + 0,25 €) / 98,5 %
    assert "form-action 'self' https://checkout.stripe.com" in r.headers["content-security-policy"]
    assert weiter.status_code == 303 and weiter.headers["location"] == "https://checkout.stripe.com/c/pay/cs_test_1"
    gesendet = dict(httpx.QueryParams(anbieter.anfragen[-1].content.decode()))
    v = _vorgang(dbs)
    assert gesendet["line_items[0][price_data][unit_amount]"] == "1041" and gesendet["metadata[vorgang]"] == v.id
    assert gesendet["success_url"] == f"https://chronik.example.org/guthaben/aufladen/fertig?v={v.id}"
    assert anbieter.anfragen[-1].headers["idempotency-key"] == v.id
    assert (v.zustand, v.netto_cent, v.brutto_cent, v.anbieter_ref) == ("offen", 1000, 1041, "cs_test_1")
    # Betrag außerhalb der festen Knöpfe
    csrf, _ = _sl_anmelden(client)
    r = client.post("/guthaben/aufladen", data={"csrf": csrf, "guthaben_id": gid, "betrag": "500", "anbieter": "stripe"})
    assert r.status_code == 400


def test_stripe_meldung_nur_signiert_und_genau_einmal(client, world, dbs, anbieter):
    _einrichten(dbs)
    _r, _w, gid = _aufladen(client, dbs)
    v = _vorgang(dbs)
    ereignis = {"id": "evt_1", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_1", "metadata": {"vorgang": v.id}, "payment_status": "paid", "amount_total": 1041,
        "currency": "eur", "payment_intent": "pi_1"}}}
    roh, kopf = _signiert(ereignis)
    assert client.post("/zahlung/stripe", content=roh, headers={"Content-Type": "application/json"}).status_code == 400
    _f, falsch = _signiert(ereignis, geheimnis="whsec_falsch")
    assert client.post("/zahlung/stripe", content=roh, headers=falsch).status_code == 400
    _a, alt = _signiert(ereignis, t=time.time() - 3600)
    assert client.post("/zahlung/stripe", content=roh, headers=alt).status_code == 400
    assert _saldo(dbs, gid) == 0
    for _ in range(2):  # doppelt gemeldet → einmal gebucht
        assert client.post("/zahlung/stripe", content=roh, headers=kopf).status_code == 200
    assert _saldo(dbs, gid) == 1000 and _vorgang(dbs).zustand == "bezahlt"
    seite = client.get(f"/guthaben/aufladen/fertig?v={v.id}").text
    assert "10,00 € sind gutgeschrieben" in seite
    # Erstattung: Gegenbuchung; Rückbuchung: Gegenbuchung und Sperre
    roh, kopf = _signiert({"id": "evt_2", "type": "charge.refunded", "data": {"object": {
        "payment_intent": "pi_1", "amount_refunded": 520}}})
    client.post("/zahlung/stripe", content=roh, headers=kopf)
    assert _saldo(dbs, gid) == 500
    roh, kopf = _signiert({"id": "evt_3", "type": "charge.dispute.created", "data": {"object": {
        "id": "dp_1", "payment_intent": "pi_1", "amount": 521}}})
    client.post("/zahlung/stripe", content=roh, headers=kopf)
    from app.models import Guthaben

    dbs.expire_all()
    g = dbs.get(Guthaben, gid)
    assert _saldo(dbs, gid) == 0 and g.gesperrt_at is not None
    _csrf, seite = _sl_anmelden(client)
    assert "zurückgebucht oder angefochten" in seite and 'action="/guthaben/aufladen"' not in seite


def test_stripe_falscher_betrag_bucht_nichts(client, world, dbs, anbieter):
    _einrichten(dbs)
    _r, _w, gid = _aufladen(client, dbs)
    v = _vorgang(dbs)
    roh, kopf = _signiert({"id": "evt_9", "type": "checkout.session.completed", "data": {"object": {
        "id": "cs_test_1", "metadata": {"vorgang": v.id}, "payment_status": "paid", "amount_total": 100,
        "currency": "eur", "payment_intent": "pi_9"}}})
    assert client.post("/zahlung/stripe", content=roh, headers=kopf).status_code == 200
    assert _saldo(dbs, gid) == 0 and _vorgang(dbs).zustand == "abweichung"


def test_sperre_haelt_verarbeitung_an_und_verwaltung_hebt_sie_auf(client, world, dbs, admin):  # noqa: F811
    from app import abrechnung
    from app.db import utcnow
    from app.models import Campaign, Job

    _einrichten(dbs)
    g = abrechnung.guthaben_konto(dbs, _anna(dbs).id)
    abrechnung.buchen(dbs, g, "gutschrift", 5000, grund="Test")
    g.gesperrt_at = utcnow()
    job = Job(type="document", required_capability="llm")
    dbs.add(job)
    dbs.commit()
    from unittest import mock

    with mock.patch.object(abrechnung, "kampagne_von_auftrag", return_value=dbs.get(Campaign, world["cid"])):
        assert abrechnung.warte_grund(dbs, job) == "gesperrt"
    seite = client.get("/verwaltung/abrechnung").text
    assert "Sperre aufheben" in seite
    from app.verwaltung import zweiter_faktor

    zweiter_faktor_an = mock.patch.object(zweiter_faktor, "aktiv", return_value=True)
    with zweiter_faktor_an:
        r = client.post(f"/verwaltung/abrechnung/entsperren/{g.id}", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert abrechnung.guthaben_konto(dbs, _anna(dbs).id).gesperrt_at is None


# ---------------------------------------------------------------- PayPal
def test_paypal_ruecksprung_bucht_ab_und_meldung_bucht_nicht_doppelt(client, world, dbs, anbieter):
    from app import zahlung

    _einrichten(dbs)
    r, weiter, gid = _aufladen(client, dbs, "paypal")
    erwartet = zahlung.brutto(dbs, "paypal", 1000)
    assert erwartet == 1072 and "10,72 €" in r.text and "Weiter zu PayPal" in r.text
    assert weiter.headers["location"].startswith("https://www.sandbox.paypal.com/")
    assert anbieter.paypal_betrag == {"currency_code": "EUR", "value": "10.72"}
    v = _vorgang(dbs)
    r = client.get(f"/guthaben/aufladen/paypal?v={v.id}&token=FREMD", follow_redirects=False)
    assert r.status_code == 303 and _saldo(dbs, gid) == 0  # anderer Auftrag: nichts
    r = client.get(f"/guthaben/aufladen/paypal?v={v.id}&token=ORDER1", follow_redirects=False)
    assert r.headers["location"] == f"/guthaben/aufladen/fertig?v={v.id}"
    assert _saldo(dbs, gid) == 1000 and _vorgang(dbs).zahlung_ref == "CAP1"
    meldung = {"id": "WH-EV1", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {
        "id": "CAP1", "custom_id": v.id, "amount": {"currency_code": "EUR", "value": "10.72"}}}
    kopf = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
            "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "now"}
    assert client.post("/zahlung/paypal", json=meldung, headers=kopf).status_code == 200
    assert _saldo(dbs, gid) == 1000
    anbieter.verifiziert = False
    erstattung = {"id": "WH-EV2", "event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {
        "id": "R1", "amount": {"currency_code": "EUR", "value": "10.72"},
        "links": [{"rel": "up", "href": "https://api.sandbox.paypal.com/v2/payments/captures/CAP1"}]}}
    assert client.post("/zahlung/paypal", json=erstattung, headers=kopf).status_code == 400
    assert _saldo(dbs, gid) == 1000
    anbieter.verifiziert = True
    assert client.post("/zahlung/paypal", json=erstattung, headers=kopf).status_code == 200
    assert _saldo(dbs, gid) == 0


def test_paypal_ausstehend_wartet_auf_meldung(client, world, dbs, anbieter):
    _einrichten(dbs, stripe=False)
    anbieter.capture = {"status": "PENDING"}
    _r, _w, gid = _aufladen(client, dbs, "paypal")
    v = _vorgang(dbs)
    client.get(f"/guthaben/aufladen/paypal?v={v.id}&token=ORDER1")
    assert _saldo(dbs, gid) == 0 and _vorgang(dbs).zustand == "offen"
    _csrf, seite = _sl_anmelden(client)
    assert "noch nicht bestätigt" in seite


# ---------------------------------------------------------------- Grenzen, Abbruch, Schalter
def test_tagesgrenze_und_abbruch(client, world, dbs, anbieter):
    from app import zahlung

    _einrichten(dbs)
    for _ in range(zahlung.JE_TAG_VORGAENGE):
        _r, weiter, _gid = _aufladen(client, dbs)
        assert weiter.status_code == 303
    _r, weiter, _gid = _aufladen(client, dbs)
    assert weiter.status_code == 400 and "Für heute sind genug Aufladungen begonnen" in weiter.text
    v = _vorgang(dbs)
    seite = client.get(f"/guthaben/aufladen/abbruch?v={v.id}").text
    assert "nichts abgebucht" in seite and _vorgang(dbs).zustand == "abgebrochen"


def test_anbieter_nicht_erreichbar(client, world, dbs, monkeypatch):
    from app import zahlung

    _einrichten(dbs)
    monkeypatch.setattr(zahlung, "_client", httpx.Client(transport=httpx.MockTransport(
        lambda req: httpx.Response(500))))
    _r, weiter, _gid = _aufladen(client, dbs)
    assert weiter.status_code == 400 and "nicht erreichbar" in weiter.text
    assert _vorgang(dbs).zustand == "fehlgeschlagen"


def test_ohne_abrechnung_oder_adresse_nichts_da(client, world, dbs):
    from app import zahlung
    from app.einstellungen import meta_schreiben

    assert client.post("/zahlung/stripe", content=b"{}").status_code == 404
    _einrichten(dbs)
    meta_schreiben(dbs, "angabe.public_url", "")
    dbs.commit()
    assert zahlung.bereit(dbs) == [] and client.post("/zahlung/paypal", json={}).status_code == 404
    _modus(dbs, "aus")
    assert client.get("/guthaben/aufladen/fertig").status_code == 404


def test_verwaltung_nur_test_schluessel_und_geheimes_bleibt_verborgen(client, dbs, admin):  # noqa: F811
    from unittest import mock

    from app.einstellungen import meta_lesen
    from app.verwaltung import zweiter_faktor

    _modus(dbs)
    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "angabe.public_url", "https://chronik.example.org")
    dbs.commit()
    daten = {"csrf": admin, "betraege": "10, 20, 50", "stripe_prozent": "1,5", "stripe_fest": "0,25",
             "paypal_prozent": "2,99", "paypal_fest": "0,39", "paypal_id": "client", "paypal_webhook_id": "WH-1"}
    r = client.post("/verwaltung/abrechnung/zahlung", data=daten | {"stripe_schluessel": "sk_test_x"})
    assert r.status_code == 403  # ohne zweiten Faktor
    with mock.patch.object(zweiter_faktor, "aktiv", return_value=True):
        r = client.post("/verwaltung/abrechnung/zahlung", data=daten | {"stripe_schluessel": "sk_live_x"})
        assert r.status_code == 400 and "Echtes Geld ist noch nicht freigegeben" in r.text
        r = client.post("/verwaltung/abrechnung/zahlung", data=daten | {"betraege": "5, 20"})
        assert r.status_code == 400
        r = client.post("/verwaltung/abrechnung/zahlung", data=daten | {
            "stripe_schluessel": "sk_test_geheim1234", "stripe_webhook": WEBHOOK, "paypal_geheim": "pp-geheim"},
            follow_redirects=False)
        assert r.status_code == 303
        seite = client.get("/verwaltung/abrechnung").text
    assert "sk_test_geheim1234" not in seite and "pp-geheim" not in seite and WEBHOOK not in seite
    assert "https://chronik.example.org/zahlung/stripe" in seite and seite.count("marke-status--gut") >= 2
    assert meta_lesen(dbs, "zahlung.stripe_schluessel") == "sk_test_geheim1234"
    # leer lassen behält den Schlüssel
    with mock.patch.object(zweiter_faktor, "aktiv", return_value=True):
        client.post("/verwaltung/abrechnung/zahlung", data=daten)
    dbs.expire_all()
    assert meta_lesen(dbs, "zahlung.stripe_schluessel") == "sk_test_geheim1234"
    assert re.search(r'name="betraege" value="10, 20, 50"', client.get("/verwaltung/abrechnung").text)
