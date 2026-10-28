"""Server 0.4.136: PayPal-Meldungen begrenzt und an den eigenen Auftrag gebunden (S54), Zugangstoken zwischengespeichert."""
import pytest

from tests.test_server_04133 import _aufladen, _einrichten, _saldo, _vorgang, anbieter  # noqa: F401
from tests.test_verwaltung import admin  # noqa: F401

KOPF = {"PAYPAL-AUTH-ALGO": "SHA256withRSA", "PAYPAL-CERT-URL": "https://api.sandbox.paypal.com/cert",
        "PAYPAL-TRANSMISSION-ID": "t1", "PAYPAL-TRANSMISSION-SIG": "sig", "PAYPAL-TRANSMISSION-TIME": "now"}


def _abgeschlossen(v, auftrag):
    res = {"id": "CAP9", "custom_id": v.id, "amount": {"currency_code": "EUR", "value": "10.72"}}
    if auftrag is not None:
        res["supplementary_data"] = {"related_ids": {"order_id": auftrag}}
    return {"id": f"WH-{auftrag}", "event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": res}


def test_meldung_nur_mit_dem_eigenen_auftrag(client, world, dbs, anbieter):  # noqa: F811
    _einrichten(dbs, stripe=False)
    anbieter.capture = {"status": "PENDING"}
    _r, _w, gid = _aufladen(client, dbs, "paypal")
    v = _vorgang(dbs)
    client.get(f"/guthaben/aufladen/paypal?v={v.id}&token=ORDER1")
    assert _saldo(dbs, gid) == 0
    for auftrag in ("ORDER-FREMD", None):  # fremder oder fehlender Auftrag: nichts buchen
        assert client.post("/zahlung/paypal", json=_abgeschlossen(v, auftrag), headers=KOPF).status_code == 200
        assert _saldo(dbs, gid) == 0
    assert client.post("/zahlung/paypal", json=_abgeschlossen(v, "ORDER1"), headers=KOPF).status_code == 200
    assert _saldo(dbs, gid) == 1000


def test_hoechstens_60_meldungen_je_minute_ohne_anfrage_bei_paypal(client, world, dbs, anbieter):  # noqa: F811
    _einrichten(dbs, stripe=False)
    for _ in range(60):
        assert client.post("/zahlung/paypal", json={"event_type": "X"}, headers=KOPF).status_code == 200
    vorher = len(anbieter.anfragen)
    assert client.post("/zahlung/paypal", json={"event_type": "X"}, headers=KOPF).status_code == 429
    assert len(anbieter.anfragen) == vorher


def test_zugangstoken_bis_kurz_vor_ablauf(dbs, monkeypatch):
    import httpx

    from app import zahlung

    _einrichten(dbs, stripe=False)
    zahlung._TOKEN.clear()
    abrufe = []

    def antwort(req):
        abrufe.append(req)
        return httpx.Response(200, json={"access_token": f"T{len(abrufe)}", "expires_in": 32400})

    monkeypatch.setattr(zahlung, "_client", httpx.Client(transport=httpx.MockTransport(antwort)))
    assert zahlung._paypal_token(dbs) == zahlung._paypal_token(dbs) == "T1" and len(abrufe) == 1
    zahlung.einstellungen_schreiben(dbs, "test", paypal_geheim="anderes")  # anderer Zugang → neues Token
    assert zahlung._paypal_token(dbs) == "T2"
    zahlung._TOKEN.clear()


# ---------------------------------------------------------------- Hinweis-Mails bei Änderungen am Konto
class _Post(list):
    kaputt = False


@pytest.fixture()
def post(monkeypatch, dbs):
    from app import konto_mail, mail
    from app.einstellungen import meta_schreiben

    gesendet = _Post()

    def senden(db, an, betreff, text, html=None):
        if gesendet.kaputt:
            raise mail.MailFehler("kaputt")
        gesendet.append({"an": an, "betreff": betreff, "text": text, "html": html})

    monkeypatch.setattr(mail, "senden", senden)
    monkeypatch.setattr(konto_mail, "_starten", konto_mail.senden_alle)  # gleich statt im eigenen Faden
    meta_schreiben(dbs, "melden.smtp_host", "smtp.example.org")
    dbs.commit()
    return gesendet


def _mit_adresse(dbs, make_user, name="dora"):
    from sqlalchemy import select

    from app.models import User

    make_user(name)
    u = dbs.scalar(select(User).where(User.username == name))
    u.email = f"{name}@example.net"
    dbs.commit()
    return u


def _anmelden(client, name="dora", ua="Taleward/0.31 Android"):
    return client.post("/api/v1/auth/login", json={"username": name, "password": "geheim123"},
                       headers={"User-Agent": ua})


def test_anmeldung_genau_eine_mail_in_15_minuten_ohne_ip(client, dbs, make_user, post):
    _mit_adresse(dbs, make_user)
    assert _anmelden(client).status_code == 200
    assert len(post) == 1
    m = post[0]
    assert m["an"] == "dora@example.net" and m["betreff"].startswith("Neue Anmeldung bei deinem Konto")
    assert "mit Passwort in der App" in m["text"] and "UTC" in m["text"]
    assert "testclient" not in m["text"] + m["html"] and "127.0.0.1" not in m["text"]
    assert "http" not in m["text"] and "href" not in m["html"]  # kein Link
    assert "Ändere in der App unter Konto dein Passwort" in m["text"]
    assert _anmelden(client, ua="Mozilla/5.0 (X11; Linux x86_64) Firefox/140.0").status_code == 200
    assert len(post) == 1  # zweite Anmeldung im Fenster: still weg


def test_im_browser_und_versandfehler_stoert_die_anmeldung_nicht(client, dbs, make_user, post):
    _mit_adresse(dbs, make_user)
    post.kaputt = True
    try:
        assert _anmelden(client, ua="Mozilla/5.0 (Windows NT 10.0) Chrome/141.0").status_code == 200
    finally:
        post.kaputt = False
    assert post == []


def test_app_oder_browser_grob_aus_dem_user_agent():
    from types import SimpleNamespace

    from app import konto_mail

    def ort(ua):
        return konto_mail.ort_aus(SimpleNamespace(headers={"user-agent": ua}))

    assert ort("Mozilla/5.0 (Windows NT 10.0) Chrome/141.0") == "browser"
    assert ort("Mozilla/5.0 (Linux; Android 15; wv) AppleWebKit/537.36") == "app"
    assert ort("Taleward/0.31") == ort("") == "app"


def test_ohne_bestaetigte_adresse_oder_schalter_aus_keine_mail(client, dbs, make_user, post):
    from app.einstellungen import meta_schreiben

    make_user("emil")
    assert _anmelden(client, "emil").status_code == 200
    _mit_adresse(dbs, make_user)
    meta_schreiben(dbs, "melden.konto_hinweise", "0")
    dbs.commit()
    assert _anmelden(client).status_code == 200
    assert post == []


def test_passwort_und_adresse_geaendert_an_die_alte_adresse(client, dbs, make_user, post, login):
    from app import konto_mail
    from app.models import User

    u = _mit_adresse(dbs, make_user)
    kopf = login("dora")
    post.clear()
    r = client.put("/api/v1/me/password", json={"currentPassword": "geheim123", "newPassword": "Lichtung-im-Nebel-7"},
                   headers=kopf)
    assert r.status_code == 204, r.text
    assert post[-1]["betreff"].startswith("Dein Passwort wurde geändert") and "in der App" in post[-1]["text"]
    assert client.delete("/api/v1/me/email", headers=kopf).status_code == 204
    m = post[-1]
    assert m["an"] == "dora@example.net" and m["betreff"].startswith("Die E-Mail-Adresse deines Kontos")
    assert "bisher an deinem Konto hinterlegt" in m["text"]
    # nach einem Rollback geht nichts raus
    anzahl = len(post)
    u = dbs.get(User, u.id)
    u.email = "dora@example.net"
    konto_mail.vormerken(dbs, u, "passwort", ort="server")
    dbs.rollback()
    dbs.commit()
    assert len(post) == anzahl


def test_zweiter_faktor_an_und_aus(dbs, make_user, post):
    from app.verwaltung import zweiter_faktor

    u = _mit_adresse(dbs, make_user)
    geheim = zweiter_faktor.beginnen(dbs, u.id)
    import time

    assert zweiter_faktor.bestaetigen(dbs, u.id, zweiter_faktor.code(geheim, int(time.time() // 30)))
    dbs.commit()
    assert post[-1]["betreff"].startswith("Zweiter Faktor eingeschaltet")
    zweiter_faktor.ausschalten(dbs, u.id)
    dbs.commit()
    assert post[-1]["betreff"].startswith("Zweiter Faktor ausgeschaltet") and len(post) == 2
    zweiter_faktor.ausschalten(dbs, u.id)  # war schon aus: keine zweite Mail
    dbs.commit()
    assert len(post) == 2


def test_englisch_nach_anmeldung_auf_englisch(client, dbs, make_user, post):
    _mit_adresse(dbs, make_user)
    r = client.post("/api/v1/auth/login", json={"username": "dora", "password": "geheim123"},
                    headers={"Accept-Language": "en", "User-Agent": "Taleward"})
    assert r.status_code == 200
    assert post[-1]["betreff"].startswith("New sign-in to your account") and "with password in the app" in post[-1]["text"]


def test_verwaltung_hat_den_schalter(client, dbs, admin):  # noqa: F811
    from app import konto_mail

    seite = client.get("/verwaltung/einstellungen").text
    assert 'name="konto_hinweise" value="1" checked' in seite and konto_mail.an(dbs)
