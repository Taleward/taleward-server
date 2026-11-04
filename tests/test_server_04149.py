"""Server 0.4.149: frischer zweiter Faktor je Sitzung und auf allen Wegen zu Schlüsseln (S57), Merkzeichen und
Challenge (S58), Sicht der Urheberin auf übernommene Vorschläge nach späteren Änderungen (S56-Rest)."""
import time

from sqlalchemy import select

from app.einstellungen import meta_schreiben
from tests.faktor_hilfe import sitzung
from tests.test_server_04136 import post as konto_post  # noqa: F401
from tests.test_server_04143 import HTTPS, _anmelden_mit_code, _chef, _faktor_an, _https_an
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401


# ---------------------------------------------------------------- S57: frisch nur in dieser Sitzung
def test_frisch_gilt_nur_fuer_die_sitzung(dbs, admin):  # noqa: F811
    from app.verwaltung import zweiter_faktor as zf

    chef = _chef(dbs)
    zf.beginnen(dbs, chef.id)
    from tests.test_server_0484 import _code

    zf.bestaetigen(dbs, chef.id, _code(dbs, chef.id))
    zf.frisch_merken(dbs, chef.id, sitzung="sitzung-a")
    assert zf.frisch(dbs, chef.id, sitzung="sitzung-a")
    assert not zf.frisch(dbs, chef.id, sitzung="sitzung-b")
    assert not zf.frisch(dbs, chef.id, sitzung="-") and not zf.frisch(dbs, chef.id, sitzung=None)
    assert not zf.frisch(dbs, chef.id, "guthaben", sitzung="sitzung-a")  # Bereiche getrennt
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", str(int(time.time())))  # alte Form
    assert not zf.frisch(dbs, chef.id, sitzung="sitzung-a")
    zf.frisch_merken(dbs, chef.id, sitzung="-")  # ohne Sitzung wird nichts gemerkt
    assert not zf.frisch(dbs, chef.id, sitzung="-")


def test_vertrautes_geraet_erbt_den_frischen_faktor_nicht(client, dbs, admin):  # noqa: F811
    """Wer mit Passwort und vertrautem Gerät hereinkommt, während die Person gerade woanders bestätigt hat, muss den
    Faktor selbst zeigen."""
    chef = _chef(dbs)
    _https_an(dbs)
    codes = _faktor_an(client, dbs, chef.id)
    assert _anmelden_mit_code(client, codes, vertrauen=True).status_code == 303  # Sitzung A, frisch
    sitzung_a = client.cookies.get("tw_verwaltung")
    csrf_a = client.get(f"{HTTPS}/verwaltung/konten").text.split('name="csrf" value="')[1].split('"')[0]
    # Sitzung B: dasselbe Konto über das vertraute Gerät (nur Passwort), innerhalb der fünf Minuten
    client.cookies.delete("tw_verwaltung", path="/verwaltung")
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                    follow_redirects=False)
    assert r.status_code == 303 and "anmelden/code" not in r.headers["location"]
    csrf_b = client.get(f"{HTTPS}/verwaltung/konten").text.split('name="csrf" value="')[1].split('"')[0]
    r = client.post(f"{HTTPS}/verwaltung/konten", follow_redirects=False,
                    data={"csrf": csrf_b, "username": "neu-b", "display_name": "N", "password": "sehrgeheim12"})
    assert r.headers["location"].startswith("/verwaltung/bestaetigen")
    # Sitzung A darf weiter
    client.cookies.delete("tw_verwaltung", path="/verwaltung")
    client.cookies.set("tw_verwaltung", sitzung_a, path="/verwaltung")
    r = client.post(f"{HTTPS}/verwaltung/konten", follow_redirects=False,
                    data={"csrf": csrf_a, "username": "neu-a", "display_name": "N", "password": "sehrgeheim12"})
    assert "konto_angelegt" in r.headers["location"]


def test_assistent_und_zugaenge_verlangen_frischen_faktor(client, dbs, admin, monkeypatch):  # noqa: F811
    from app import einrichtung, schluessel

    chef = _chef(dbs)
    _faktor_an(client, dbs, chef.id)
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", "1")  # nicht mehr frisch
    meta_schreiben(dbs, "betrieb.art", "beides")
    dbs.commit()
    monkeypatch.setattr(schluessel, "mistral", lambda k: "ok")
    monkeypatch.setattr(schluessel, "huggingface", lambda k: "ok")
    assert einrichtung.betriebsart(dbs) == "beides"
    ref = {"referer": "http://testserver/verwaltung/assistent/betrieb"}
    wege = [
        ("/verwaltung/assistent/betrieb", {"mistral_key": "sk-neu-1234567890", "limit_euro": "0", "stunden": "12"}),
        ("/verwaltung/assistent/betrieb", {"hf_token": "hf_abcdefghijklmnop", "limit_euro": "0", "stunden": "12"}),
        ("/verwaltung/assistent/zusammenfassung", {"wahl": "api", "mistral_key": "sk-neu-1234567890"}),
        ("/verwaltung/assistent/sicherung/jetzt", {}),
        ("/verwaltung/assistent/spielleitung", {"username": "sl1", "display_name": "SL", "password": "sehrgeheim12"}),
        ("/verwaltung/assistent/koppeln", {"weiter": "/verwaltung/transkription"}),
        ("/verwaltung/worker", {"name": "PC 3"}),
        ("/verwaltung/benachrichtigungen", {"smtp_pass": "geheim", "zeitzone": "Europe/Berlin"}),
        ("/verwaltung/benachrichtigungen", {"ntfy_token": "tk_neu", "zeitzone": "Europe/Berlin"}),
    ]
    for pfad, daten in wege:
        r = client.post(pfad, data={"csrf": admin, **daten}, headers=ref, follow_redirects=False)
        assert r.status_code == 303 and r.headers["location"].startswith("/verwaltung/bestaetigen"), (pfad, daten)
    from app.einstellungen import meta_lesen

    assert meta_lesen(dbs, "extern.api_key") in (None, "") and meta_lesen(dbs, "melden.smtp_pass") in (None, "")
    # ohne neuen Schlüssel geht der Schritt wie bisher
    r = client.post("/verwaltung/assistent/zusammenfassung", data={"csrf": admin, "wahl": "lokal"},
                    follow_redirects=False)
    assert "bestaetigen" not in r.headers["location"]


# ---------------------------------------------------------------- S58
def test_merkzeichen_nur_aus_dem_richtigen_cookie_und_immer_neu(client, dbs, admin, konto_post):  # noqa: F811
    from app import vertraut
    from app.models import VertrautesGeraet

    chef = _chef(dbs)
    chef.email = "chef@example.org"
    _https_an(dbs)
    codes = _faktor_an(client, dbs, chef.id)
    # untergeschobenes Merkzeichen ohne __Host- (könnte eine Subdomain setzen): unter https nicht gelesen, nicht übernommen
    client.cookies.clear()
    client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    untergeschoben = "x" * 43
    client.cookies.set("tw_vertraut", untergeschoben)
    r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"code": codes.pop(), "vertrauen": "ja"},
                    follow_redirects=False)
    assert r.status_code == 303
    neu = client.cookies.get("__Host-tw_vertraut")
    assert neu and neu != untergeschoben
    dbs.expire_all()
    eintraege = list(dbs.scalars(select(VertrautesGeraet)))
    assert [g.id for g in eintraege] == [vertraut._hash(neu, "verwaltung")]
    # vorhandenes __Host-Merkzeichen (etwa untergeschoben, bevor die Person „vertrauen“ ankreuzt): nie übernommen
    client.cookies.clear()
    client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    vorher = "y" * 43
    client.cookies.set("__Host-tw_vertraut", vorher)
    r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"code": codes.pop(), "vertrauen": "ja"},
                    follow_redirects=False)
    assert r.status_code == 303
    zweites = r.headers["set-cookie"].split("__Host-tw_vertraut=")[1].split(";")[0]
    assert zweites not in (vorher, neu)
    dbs.expire_all()
    ids = {g.id for g in dbs.scalars(select(VertrautesGeraet))}
    assert vertraut._hash(vorher, "verwaltung") not in ids and vertraut._hash(zweites, "verwaltung") in ids


def test_merken_zieht_den_anderen_bereich_mit(dbs, admin):  # noqa: F811
    from fastapi import Response
    from starlette.requests import Request

    from app import vertraut
    from app.models import VertrautesGeraet

    chef = _chef(dbs)

    def anfrage(wert=None):
        kopf = [(b"host", b"testserver")] + ([(b"cookie", f"__Host-tw_vertraut={wert}".encode())] if wert else [])
        return Request({"type": "http", "scheme": "https", "server": ("testserver", 443), "path": "/", "headers": kopf,
                        "query_string": b"", "method": "POST"})

    a = Response()
    vertraut.merken(a, anfrage(), dbs, chef, "verwaltung")
    erster = a.headers["set-cookie"].split("__Host-tw_vertraut=")[1].split(";")[0]
    b = Response()
    vertraut.merken(b, anfrage(erster), dbs, chef, "guthaben")
    zweiter = b.headers["set-cookie"].split("__Host-tw_vertraut=")[1].split(";")[0]
    assert zweiter != erster
    dbs.flush()
    ids = {(g.id, g.bereich) for g in dbs.scalars(select(VertrautesGeraet))}
    assert ids == {(vertraut._hash(zweiter, "verwaltung"), "verwaltung"), (vertraut._hash(zweiter, "guthaben"), "guthaben")}


def test_challenge_bleibt_verbraucht_wenn_danach_etwas_scheitert(dbs, admin):  # noqa: F811
    from app import passkeys
    from app.models import ServerMeta

    chef = _chef(dbs)
    passkeys._challenge_merken(dbs, chef, "get", "bindung-1")
    dbs.commit()
    assert passkeys._challenge_verbrauchen(dbs, chef, "get", "bindung-1") is not None
    dbs.rollback()  # wie nach einem Fehler 500 in der Prüfung
    assert not list(dbs.scalars(select(ServerMeta).where(ServerMeta.key.like("passkey.challenge.%"))))
    assert passkeys._challenge_verbrauchen(dbs, chef, "get", "bindung-1") is None


def test_rechteentzug_ueber_die_kommandozeile_entfernt_vertraute_geraete(dbs, admin):  # noqa: F811
    from datetime import timedelta

    from typer.testing import CliRunner

    from app.cli import app
    from app.db import utcnow
    from app.models import OrgMember, Organization, VertrautesGeraet

    chef = _chef(dbs)
    jetzt = utcnow()
    dbs.add(VertrautesGeraet(id="a" * 64, user_id=chef.id, bereich="verwaltung", token_version=chef.token_version,
                             faktor_fassung=0, bezeichnung="Browser", created_at=jetzt,
                             expires_at=jetzt + timedelta(days=30), last_used_at=jetzt))
    zweite = Organization(name="Zweiter Verein")
    dbs.add(zweite)
    dbs.flush()
    dbs.add(OrgMember(organization_id=zweite.id, user_id=chef.id, role="admin"))
    dbs.commit()
    r = CliRunner().invoke(app, ["admin", "-u", "chef", "--entziehen"])
    assert r.exit_code == 0, r.output
    dbs.expire_all()
    assert dbs.scalar(select(VertrautesGeraet)) is None
    assert {om.role for om in dbs.scalars(select(OrgMember).where(OrgMember.user_id == chef.id))} == {"member"}


# ---------------------------------------------------------------- S56-Rest
def test_urheberin_sieht_spaetere_aenderungen(client, world):
    from tests.test_server_04144 import _eigene, _eintrag, _vorschlagen

    w = world
    oeff = _eintrag(client, w, name="Burg Rabenstein", summary="Alte Burg.")
    ids = {}
    for name in ("verschoben", "gekuerzt", "neu"):
        ids[name] = _vorschlagen(client, w, title=f"Vorschlag {name}", detail=f"Der Turm {name} ist eingestürzt.").json()["id"]
    for name in ("verschoben", "gekuerzt"):
        r = client.post(f"{API}/player-proposals/{ids[name]}/decision", headers=w["gm"],
                        json={"decision": "accept", "mergeIntoEntryId": oeff["id"]})
        assert r.status_code == 200, r.text
    r = client.post(f"{API}/player-proposals/{ids['neu']}/decision", headers=w["gm"], json={"decision": "accept"})
    neu_id = r.json()["resultEntryId"]
    eigene = _eigene(client, w)
    assert {eigene[i]["state"] for i in ids.values()} == {"accepted"}
    # Die SL schiebt einen der Texte später in die SL-Notizen: für die Urheberin „closed“
    e = client.get(f"{API}/entries/{oeff['id']}", headers=w["gm"]).json()
    client.patch(f"{API}/entries/{oeff['id']}", headers=w["gm"],
                 json={"summary": e["summary"].replace("Der Turm verschoben ist eingestürzt.", "").strip(),
                       "gmNotes": "Der Turm verschoben ist eingestürzt."})
    eigene = _eigene(client, w)
    assert (eigene[ids["verschoben"]]["state"], eigene[ids["verschoben"]]["resultEntryId"]) == ("closed", None)
    assert eigene[ids["gekuerzt"]]["state"] == "accepted"
    # ein neuer Eintrag bleibt ihr Ergebnis, auch umformuliert – solange er für sie sichtbar ist
    client.patch(f"{API}/entries/{neu_id}", headers=w["gm"], json={"summary": "Ganz anders beschrieben."})
    assert _eigene(client, w)[ids["neu"]]["state"] == "accepted"
    client.patch(f"{API}/entries/{neu_id}", headers=w["gm"], json={"hiddenFromMemberIds": [w["pl_member"]]})
    assert _eigene(client, w)[ids["neu"]]["state"] == "closed"


def test_ueber_die_laengengrenze_gilt_nicht_als_uebernommen(client, world, dbs):
    from app import spielende
    from app.models import Entry
    from tests.test_server_04144 import _eigene, _eintrag, _vorschlagen

    w = world
    voll = _eintrag(client, w, name="Chronik", summary="Alt.")
    dbs.get(Entry, voll["id"]).summary = "x" * (spielende.MAX_TEXT - 5)
    dbs.commit()
    pid = _vorschlagen(client, w, title="Nachtrag", detail="Ein langer Nachtrag zur Chronik.").json()["id"]
    r = client.post(f"{API}/player-proposals/{pid}/decision", headers=w["gm"],
                    json={"decision": "accept", "mergeIntoEntryId": voll["id"]})
    assert r.status_code == 200
    assert (_eigene(client, w)[pid]["state"], _eigene(client, w)[pid]["resultEntryId"]) == ("closed", None)


def test_migration_fuellt_den_uebernommenen_text(tmp_path):
    import sqlalchemy as sa
    from alembic import command
    from alembic.config import Config

    url = f"sqlite:///{tmp_path / 'm.db'}"
    cfg = Config("alembic.ini")
    cfg.set_main_option("sqlalchemy.url", url)
    command.upgrade(cfg, "c6e8a0b2d4f6")
    e = sa.create_engine(url)
    with e.begin() as c:
        c.execute(sa.text("PRAGMA foreign_keys=OFF"))
        for pid, state, sichtbar in (("p1", "accepted", 1), ("p2", "accepted", 0), ("p3", "open", 0)):
            c.execute(sa.text("INSERT INTO player_proposals (id, campaign_id, entry_type, title, detail, state, "
                              "text_sichtbar, created_at) VALUES (:id, 'c', 'npc', 'T', :d, :s, :v, '2026-11-01')"),
                      {"id": pid, "d": f"Text {pid}", "s": state, "v": sichtbar})
    command.upgrade(cfg, "c8e0a2b4d6f8")
    with e.begin() as c:
        werte = dict(c.execute(sa.text("SELECT id, oeffentlicher_text FROM player_proposals")).all())
    assert werte == {"p1": "Text p1", "p2": None, "p3": None}


def test_frisch_bindet_an_den_test_client(client, dbs, admin):  # noqa: F811
    """Hilfe für die übrigen Tests: tests/faktor_hilfe.py liest die Sitzung des Test-Clients."""
    assert sitzung(client) not in ("-", "")
