"""Server 0.4.128 / Schnittstelle 0.4.23: Registrierungsarten (invite_only, code, open, closed) und SL-Freigabe
(ServerInfo.gmApproval, User.gmPermission/gmDecidedAt, POST /me/gm-request, 403 gm_permission_required)."""
import sqlite3
import uuid

import pytest

from tests.test_anmeldung import Postfach
from tests.test_konto import daten, einladung
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

API = "/api/v1"


def setzen(dbs, **werte):
    from app.einstellungen import meta_schreiben

    for k, v in werte.items():
        meta_schreiben(dbs, {"modus": "abrechnung.modus"}.get(k, k), v)
    dbs.commit()


@pytest.fixture()
def post(monkeypatch, dbs):
    """Mailserver eingerichtet, Mails werden abgefangen; fehler=True lässt den Versand scheitern."""
    from app import mail

    gesendet = Postfach()
    gesendet.fehler = False

    def senden(db, an, betreff, text):
        if gesendet.fehler:
            raise mail.MailFehler("kaputt")
        gesendet.append((an, betreff, text))

    monkeypatch.setattr(mail, "senden", senden)
    setzen(dbs, **{"melden.smtp_host": "smtp.example.org", "angabe.public_url": "https://chronik.example.org"})
    return gesendet


def mit_adresse(dbs, name: str, adresse: str):
    from sqlalchemy import select

    from app.db import utcnow
    from app.models import User

    u = dbs.scalar(select(User).where(User.username == name))
    u.email, u.email_verified_at = adresse, utcnow()
    dbs.commit()
    return u


# ---------------------------------------------------------------- Regel gmApproval
def test_gm_approval_nach_registrierung_einstellung_und_abrechnung(client, dbs):
    info = client.get(f"{API}/info").json()
    assert info["apiVersion"] == "0.4.25" and info["registration"] == "invite_only" and info["gmApproval"] == "none"
    for registrierung, freigabe, modus, erwartet in [
        ("open", "offen", "aus", "required"), ("code", "offen", "aus", "none"), ("invite_only", "immer", "aus", "required"),
        ("closed", "immer", "topf", "required"), ("open", "immer", "guthaben", "none"), ("open", "offen", "topf", "required"),
    ]:
        setzen(dbs, registrierung=registrierung, sl_freigabe=freigabe, modus=modus)
        info = client.get(f"{API}/info").json()
        assert (info["registration"], info["gmApproval"]) == (registrierung, erwartet), (registrierung, freigabe, modus)


# ---------------------------------------------------------------- Registrierung
def test_registrierungsarten(client, world, dbs):
    from app import konto
    from app.models import User

    url = f"{API}/auth/register"
    # invite_only: ohne Einladung wie bisher 404
    assert client.post(url, json=daten()).json()["code"] == "invite_invalid"
    assert client.post(url, json=daten(registrationCode="Nebel-1234")).json()["code"] == "invite_invalid"
    # code: ohne Code 400, falscher Code 400, richtiger Code legt nur das Konto an
    setzen(dbs, registrierung="code")
    konto.registrierungscode_setzen(dbs, "Nebel 1234")
    dbs.commit()
    r = client.post(url, json=daten())
    assert r.status_code == 400 and r.json()["code"] == "registration_code_required"
    r = client.post(url, json=daten(registrationCode="falsch-99"))
    assert r.status_code == 400 and r.json()["code"] == "registration_code_invalid"
    r = client.post(url, json=daten(registrationCode="  nebel1234 "))  # Leerzeichen und Groß/klein egal
    assert r.status_code == 201, r.text
    neu = r.json()["user"]
    assert neu["gmPermission"] == "none" and neu["gmDecidedAt"] is None
    h = {"Authorization": f"Bearer {r.json()['accessToken']}"}
    assert client.get(f"{API}/campaigns", headers=h).json() == []  # keine Mitgliedschaft
    assert "1234" not in str(client.get(f"{API}/info").json()) and "nebel" not in str(client.get(f"{API}/info").json())
    # Einladung geht in code weiter, auch mit falschem Registrierungscode daneben
    r = client.post(url, json=daten(username="mit.einladung", inviteCode=einladung(client, world),
                                    registrationCode="egal"))
    assert r.status_code == 201, r.text
    # open: ohne Code; eine Einladung wird trotzdem geprüft
    setzen(dbs, registrierung="open")
    assert client.post(url, json=daten(username="offen.ohne")).status_code == 201
    assert client.post(url, json=daten(username="offen.falsch", inviteCode="FALSCH-0000")).json()["code"] == "invite_invalid"
    # closed: 403
    setzen(dbs, registrierung="closed")
    r = client.post(url, json=daten(username="zu.spaet", registrationCode="nebel1234"))
    assert r.status_code == 403 and r.json()["code"] == "registration_closed"
    assert dbs.query(User).filter(User.username.in_(["zu.spaet", "offen.falsch"])).count() == 0


def test_falsche_registrierungscodes_sind_begrenzt(client, dbs):
    from app import konto

    setzen(dbs, registrierung="code")
    konto.registrierungscode_setzen(dbs, "Nebel 1234")
    dbs.commit()
    codes = [client.post(f"{API}/auth/register", json=daten(registrationCode=f"falsch-{i}")).status_code
             for i in range(12)]
    assert codes[0] == 400 and 429 in codes
    # gespeichert ist nur eine Prüfsumme
    from app.einstellungen import meta_lesen

    gespeichert = meta_lesen(dbs, "registrierungscode")
    assert gespeichert.startswith("sha256:") and "1234" not in gespeichert


# ---------------------------------------------------------------- Kampagne anlegen
def test_anlegen_nur_mit_freigabe_beitreten_immer(client, world, dbs, make_user, login):
    w = world
    setzen(dbs, registrierung="open")
    make_user("dora")
    h = login("dora")
    r = client.post(f"{API}/campaigns", json={"title": "Ohne Freigabe"}, headers=h)
    assert r.status_code == 403 and r.json()["code"] == "gm_permission_required"
    r = client.post(f"{API}/imports", headers=h, json={"fileName": "umzug.zip", "sizeBytes": 10})
    assert r.status_code == 403 and r.json()["code"] == "gm_permission_required"
    # Beitreten per Einladung geht ohne Freigabe
    beitritt = client.post(f"{API}/campaigns/join", json={"code": einladung(client, w), "characterName": "Tamsin"},
                           headers=h)
    assert beitritt.status_code == 200, beitritt.text
    # Einzelzahler: Freigabe entfällt
    setzen(dbs, modus="guthaben")
    assert client.post(f"{API}/campaigns", json={"title": "Eigenes Guthaben"}, headers=h).status_code == 201
    setzen(dbs, modus="aus")
    # Freigabe → anlegen geht; Import wird beim Abschluss noch einmal geprüft
    from app.models import User

    dora = dbs.query(User).filter_by(username="dora").one()
    dora.gm_permission = "granted"
    dbs.commit()
    assert client.post(f"{API}/campaigns", json={"title": "Mit Freigabe"}, headers=h).status_code == 201
    imp = client.post(f"{API}/imports", headers=h, json={"fileName": "umzug.zip", "sizeBytes": 10}).json()
    dbs.refresh(dora)
    dora.gm_permission = "rejected"
    dbs.commit()
    r = client.post(f"{API}/imports/{imp['importId']}/complete", headers=h)
    assert r.status_code == 403 and r.json()["code"] == "gm_permission_required"


# ---------------------------------------------------------------- Anfrage
def test_anfrage_genau_eine_mail_und_fehlerfaelle(client, dbs, make_user, login, admin, post):  # noqa: F811
    setzen(dbs, registrierung="open")
    mit_adresse(dbs, "chef", "chefin@example.org")
    make_user("dora")
    h = login("dora")
    url = f"{API}/me/gm-request"
    r = client.post(url, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "email_unverified" and not post
    mit_adresse(dbs, "dora", "dora@example.org")
    r = client.post(url, headers={**h, "Accept-Language": "en"})
    assert r.status_code == 202 and r.json()["gmPermission"] == "pending" and r.json()["gmDecidedAt"] is None
    assert client.post(url, headers=h).status_code == 202  # wiederholt: nichts Neues
    assert len(post) == 1
    an, betreff, text = post[0]
    assert an == "chefin@example.org" and "Dora" in betreff
    assert "https://chronik.example.org/verwaltung/sl-freigaben" in text and "dora" in text
    assert client.get(f"{API}/me", headers=h).json()["gmPermission"] == "pending"
    # Der Link aus der Mail ändert nichts
    seite = client.get("/verwaltung/sl-freigaben")
    assert seite.status_code == 200 and "Dora" in seite.text and "Freigeben" in seite.text
    assert "Eine Anfrage zur SL-Freigabe wartet." in client.get("/verwaltung/").text
    assert client.get(f"{API}/me", headers=h).json()["gmPermission"] == "pending"
    # Ohne CSRF keine Entscheidung
    from app.models import User

    dora = dbs.query(User).filter_by(username="dora").one()
    assert client.post(f"/verwaltung/sl-freigaben/{dora.id}/freigeben", data={"csrf": "0"}).status_code == 403
    assert client.post(f"/verwaltung/sl-freigaben/{dora.id}/unsinn", data={"csrf": admin}).status_code == 404
    # Ablehnen → Mail an die Person (auf Englisch, wie angefragt), neue Anfrage 409
    r = client.post(f"/verwaltung/sl-freigaben/{dora.id}/ablehnen", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303 and "sl_abgelehnt" in r.headers["location"]
    assert post[-1][0] == "dora@example.org" and "declined" in post[-1][2]
    ich = client.get(f"{API}/me", headers=h).json()
    assert ich["gmPermission"] == "rejected" and ich["gmDecidedAt"]
    r = client.post(url, headers=h)
    assert r.status_code == 409 and r.json()["code"] == "gm_request_closed"
    # Verwaltung erlaubt eine neue Anfrage → none, dann wieder 202
    client.post(f"/verwaltung/sl-freigaben/{dora.id}/erlauben", data={"csrf": admin})
    assert client.get(f"{API}/me", headers=h).json()["gmPermission"] == "none"
    assert client.post(url, headers=h).status_code == 202 and len(post) == 3
    # Freigeben → granted, Mail, Anlegen geht; weitere Anfrage 200
    client.post(f"/verwaltung/sl-freigaben/{dora.id}/freigeben", data={"csrf": admin})
    ich = client.get(f"{API}/me", headers=h).json()
    assert ich["gmPermission"] == "granted" and ich["gmDecidedAt"] and "Freigabe erteilt" in post[-1][2]  # Sprache der letzten Anfrage
    assert client.post(url, headers=h).status_code == 200
    assert client.post(f"{API}/campaigns", json={"title": "Nebelfeld"}, headers=h).status_code == 201
    # Zurücknehmen: keine neuen Kampagnen, bestehende bleiben
    client.post(f"/verwaltung/sl-freigaben/{dora.id}/entziehen", data={"csrf": admin})
    assert client.post(f"{API}/campaigns", json={"title": "Noch eine"}, headers=h).status_code == 403
    assert len(client.get(f"{API}/campaigns", headers=h).json()) == 1
    # Verwaltungskonten behalten ihre Freigabe
    chef = dbs.query(User).filter_by(username="chef").one()
    dbs.refresh(chef)
    assert chef.gm_permission == "granted"
    r = client.post(f"/verwaltung/sl-freigaben/{chef.id}/entziehen", data={"csrf": admin}, follow_redirects=False)
    assert "sl_unveraendert" in r.headers["location"]


def test_anfrage_bei_mailfehler_bleibt_und_laesst_sich_wiederholen(client, dbs, make_user, login, admin, post):  # noqa: F811
    setzen(dbs, sl_freigabe="immer")
    setzen(dbs, **{"melden.email_an": "verwaltung@example.org"})
    make_user("dora")
    mit_adresse(dbs, "dora", "dora@example.org")
    h = login("dora")
    post.fehler = True
    r = client.post(f"{API}/me/gm-request", headers=h)
    assert r.status_code == 202 and r.json()["gmPermission"] == "pending"
    seite = client.get("/verwaltung/sl-freigaben").text
    assert "nicht verschickt" in seite and "Mail erneut senden" in seite
    from app.models import User

    dora = dbs.query(User).filter_by(username="dora").one()
    r = client.post(f"/verwaltung/sl-freigaben/{dora.id}/mail", data={"csrf": admin}, follow_redirects=False)
    assert "sl_mail_fehler" in r.headers["location"]
    post.fehler = False
    r = client.post(f"/verwaltung/sl-freigaben/{dora.id}/mail", data={"csrf": admin}, follow_redirects=False)
    assert "ok=sl_mail" in r.headers["location"] and post[-1][0] == "verwaltung@example.org"
    assert "nicht verschickt" not in client.get("/verwaltung/sl-freigaben").text


def test_anfrage_ohne_noetige_freigabe_und_begrenzung(client, dbs, make_user, login):
    make_user("dora")
    h = login("dora")
    r = client.post(f"{API}/me/gm-request", headers=h)
    assert r.status_code == 200 and r.json()["gmPermission"] == "none"  # invite_only, offen: nichts anzulegen
    stati = [client.post(f"{API}/me/gm-request", headers=h).status_code for _ in range(12)]
    assert stati[-1] == 429 and r.json()["gmPermission"] == "none"
    assert client.post(f"{API}/me/gm-request").status_code == 401


# ---------------------------------------------------------------- Verwaltung
def test_einstellungen_registrierung_code_und_freigabe(client, dbs, admin):  # noqa: F811
    from app import konto, slfreigabe

    def speichern(**felder):
        return client.post("/verwaltung/einstellungen", data={"csrf": admin, "bereich": "konten", **felder},
                           follow_redirects=False)

    assert speichern(registrierung="code").status_code == 400  # ohne Code
    assert speichern(registrierung="code", registrierungscode="kurz").status_code == 400
    assert speichern(registrierung="code", registrierungscode="Nebel 1234 Feld").status_code == 303
    assert konto.registrierung(dbs) == "code" and konto.registrierungscode_passt(dbs, "nebel1234feld")
    assert speichern(registrierung="code").status_code == 303  # Code bleibt
    assert speichern(registrierung="weit_offen").status_code == 400
    assert speichern(registrierung="open", sl_freigabe="immer").status_code == 303
    dbs.expire_all()
    assert slfreigabe.einstellung(dbs) == "immer" and slfreigabe.gm_approval(dbs) == "required"
    seite = client.get("/verwaltung/einstellungen").text
    assert "SL-Freigabe verlangen" in seite and "noch keiner" not in seite
    setzen(dbs, modus="guthaben")
    assert "Entfällt, weil jede Spielleitung" in client.get("/verwaltung/einstellungen").text
    assert "Jede Spielleitung bezahlt ihr Guthaben selbst" in client.get("/verwaltung/sl-freigaben").text


def test_konten_seite_gibt_frei_und_verwalter_bekommen_freigabe(client, dbs, admin):  # noqa: F811
    from app.models import User

    setzen(dbs, registrierung="open")
    mitglied = dbs.query(User).filter_by(username="mitglied").one()
    assert mitglied.gm_permission == "none"
    seite = client.get("/verwaltung/konten").text
    assert "ohne SL-Freigabe" in seite and "SL-Freigabe erteilen" in seite
    r = client.post(f"/verwaltung/sl-freigaben/{mitglied.id}/freigeben", data={"csrf": admin, "zurueck": "konten"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/verwaltung/konten?ok=sl_freigegeben")
    dbs.refresh(mitglied)
    assert mitglied.gm_permission == "granted" and mitglied.gm_decided_at is not None
    from app.cli import _neues_konto

    neu = _neues_konto(dbs, "zweite", "Zweite", "geheim123")
    dbs.commit()
    client.post(f"/verwaltung/konten/{neu.id}/verwalter", data={"csrf": admin})
    dbs.refresh(neu)
    assert neu.gm_permission == "granted" and neu.gm_decided_at is None


def test_nutzungsbedingungen_folgen_der_registrierung(client, dbs):
    from app.nutzungsbedingungen import vorlage

    assert "Ein Konto gibt es nur mit Einladung." in vorlage(dbs) and "freigibt" not in vorlage(dbs)
    setzen(dbs, registrierung="open")
    de, en = vorlage(dbs), vorlage(dbs, "en")
    assert "Ein Konto kann hier jede Person anlegen. Eigene Kampagnen leiten kannst du erst" in de
    assert "Anyone can create an account here. Running your own campaigns requires approval" in en
    assert "offen für Rollenspielgruppen" in de
    setzen(dbs, registrierung="code", modus="guthaben")
    assert "Registrierungscode des Betreibers" in vorlage(dbs) and "freigibt" not in vorlage(dbs)


# ---------------------------------------------------------------- Migration
def test_migration_gibt_bestehenden_sl_und_verwaltern_die_freigabe(tmp_path, monkeypatch):
    from alembic import command
    from alembic.config import Config

    from app import config, db

    dbfile = tmp_path / "chronik.db"
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.chdir(tmp_path)
    config.get_settings.cache_clear()
    db.reset_engine()
    root = __import__("pathlib").Path(__file__).resolve().parent.parent
    cfg = Config(str(root / "alembic.ini"))
    cfg.set_main_option("script_location", str(root / "migrations"))
    cfg.set_main_option("sqlalchemy.url", f"sqlite:///{dbfile}")
    command.upgrade(cfg, "b7d1e5f3a9c2")
    con = sqlite3.connect(dbfile)
    oid, cid = str(uuid.uuid4()), str(uuid.uuid4())
    ids = {n: str(uuid.uuid4()) for n in ("sl", "verwalter", "spieler", "ehemals")}
    for name, uid in ids.items():
        con.execute("INSERT INTO users (id, username, display_name, token_version, created_at, setup_account) "
                    "VALUES (?, ?, ?, 0, '2026-10-01 10:00:00', 0)", (uid, name, name))
    con.execute("INSERT INTO organizations (id, name, created_at) VALUES (?, 'Verein', '2026-10-01 10:00:00')", (oid,))
    con.execute("INSERT INTO org_members (organization_id, user_id, role, joined_at) VALUES (?, ?, 'admin', "
                "'2026-10-01 10:00:00')", (oid, ids["verwalter"]))
    spalten = [z[1] for z in con.execute("PRAGMA table_info(campaigns)")]
    werte = {"id": cid, "title": "Alt", "description": "", "created_at": "2026-10-01 10:00:00", "organization_id": oid}
    pflicht = [z for z in con.execute("PRAGMA table_info(campaigns)") if z[3] and z[4] is None and z[1] not in werte]
    for z in pflicht:
        werte[z[1]] = 0 if "INT" in z[2].upper() or "BOOL" in z[2].upper() else "x"
    werte = {k: v for k, v in werte.items() if k in spalten}
    con.execute(f"INSERT INTO campaigns ({', '.join(werte)}) VALUES ({', '.join('?' * len(werte))})", list(werte.values()))
    for name, rolle in (("sl", "gm"), ("spieler", "player")):
        mspalten = {z[1]: z for z in con.execute("PRAGMA table_info(members)")}
        mw = {"id": str(uuid.uuid4()), "campaign_id": cid, "user_id": ids[name], "role": rolle,
              "joined_at": "2026-10-01 10:00:00"}
        for k, z in mspalten.items():
            if z[3] and z[4] is None and k not in mw:
                mw[k] = 0 if "INT" in z[2].upper() or "BOOL" in z[2].upper() else "x"
        con.execute(f"INSERT INTO members ({', '.join(mw)}) VALUES ({', '.join('?' * len(mw))})", list(mw.values()))
    con.commit()
    con.close()
    command.upgrade(cfg, "head")
    con = sqlite3.connect(dbfile)
    stand = dict(con.execute("SELECT username, gm_permission FROM users"))
    entschieden = con.execute("SELECT COUNT(*) FROM users WHERE gm_decided_at IS NOT NULL").fetchone()[0]
    con.close()
    assert stand == {"sl": "granted", "verwalter": "granted", "spieler": "none", "ehemals": "none"}
    assert entschieden == 0
    db.reset_engine()
    config.get_settings.cache_clear()
