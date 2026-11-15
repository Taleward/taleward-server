"""Server 0.4.168: Connector – ein KI-Assistent liest und bedient die Verwaltung (MCP), mit Zustimmung des Verwalters,
Freigabe für Geld, Konten und Schlüssel, gesperrten Bereichen und Protokoll."""
import base64
import hashlib
import html
import io
import zipfile
from urllib.parse import parse_qs, urlsplit

import pytest
from sqlalchemy import select

from app.einstellungen import meta_schreiben
from tests.test_server_04135 import ADRESSE
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

RUECK = "https://claude.ai/api/mcp/auth_callback"
GEHEIM = "v" * 50
PROBE = base64.urlsafe_b64encode(hashlib.sha256(GEHEIM.encode()).digest()).decode().rstrip("=")


@pytest.fixture()
def an(client, dbs, admin):  # noqa: F811
    """Connector eingeschaltet: öffentliche Adresse, zweiter Faktor (frisch), Netze offen."""
    from app import connector

    meta_schreiben(dbs, "angabe.public_url", ADRESSE)
    connector.freischalten(dbs, True)
    dbs.commit()
    _faktor_an(client, dbs, _chef(dbs).id)
    r = client.post("/verwaltung/connector/schalter", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert r.status_code == 303, r.text
    dbs.expire_all()
    assert connector.an(dbs) and connector.netze(dbs) == connector.STANDARD_NETZE
    connector.netze_schreiben(dbs, "")
    dbs.commit()
    return admin


def _programm(client, uri=RUECK):
    return client.post("/connector/register", json={"redirect_uris": [uri], "client_name": "Claude"})


def _anfrage(programm, **mehr):
    return {"client_id": programm, "redirect_uri": RUECK, "response_type": "code", "code_challenge": PROBE,
            "code_challenge_method": "S256", "state": "s1", "resource": ADRESSE + "/mcp", **mehr}


def _code(client, csrf, programm):
    r = client.get("/verwaltung/connector/zustimmen", params=_anfrage(programm))
    assert r.status_code == 200 and "Claude" in r.text and "claude.ai" in r.text, r.text
    r = client.post("/verwaltung/connector/zustimmen", data={"csrf": csrf, "antwort": "ja"})
    assert r.status_code == 200, r.text
    ziel = urlsplit(html.unescape(r.text.split('content="0;url=')[1].split('"')[0]))
    werte = parse_qs(ziel.query)
    assert f"{ziel.scheme}://{ziel.netloc}{ziel.path}" == RUECK and werte["state"] == ["s1"]
    return werte["code"][0]


def _tausch(client, programm, code, geheim=GEHEIM):
    return client.post("/connector/token", data={"grant_type": "authorization_code", "code": code,
                                                 "code_verifier": geheim, "client_id": programm, "redirect_uri": RUECK})


def _verbinden(client, csrf):
    programm = _programm(client).json()["client_id"]
    r = _tausch(client, programm, _code(client, csrf, programm))
    assert r.status_code == 200, r.text
    return programm, r.json()


def _rpc(client, schluessel, methode, werte=None, kennung=1):
    return client.post("/mcp", json={"jsonrpc": "2.0", "id": kennung, "method": methode, "params": werte or {}},
                       headers={"Authorization": f"Bearer {schluessel}"})


def _werkzeug(client, schluessel, name, **werte):
    r = _rpc(client, schluessel, "tools/call", {"name": name, "arguments": werte})
    assert r.status_code == 200, r.text
    e = r.json()["result"]
    return e["content"][0]["text"], e["isError"]


# ---------------------------------------------------------------- Schalter
def test_aus_ist_der_standard_und_einschalten_braucht_faktor_und_adresse(client, dbs, admin):  # noqa: F811
    from app import connector

    for pfad in ("/.well-known/oauth-protected-resource", "/.well-known/oauth-authorization-server"):
        assert client.get(pfad).status_code == 404
    assert client.post("/mcp", json={}).status_code == 404 and _programm(client).status_code == 404
    assert client.get("/verwaltung/connector/zustimmen").status_code == 404
    # ohne Freischalten auf dem Server: keine Seite, kein Verweis, kein Einschalten
    assert client.get("/verwaltung/connector").status_code == 404
    assert "connector" not in client.get("/verwaltung/einstellungen/einrichtung").text.lower()
    r = client.post("/verwaltung/connector/schalter", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert r.status_code == 404 and connector.hindernis(dbs) == "versteckt"
    from typer.testing import CliRunner

    from app.cli import app as cli

    assert "nicht freigeschaltet" in CliRunner().invoke(cli, ["connector"]).output
    assert "freigeschaltet, aber aus" in CliRunner().invoke(cli, ["connector", "--an"]).output
    dbs.expire_all()
    assert connector.sichtbar(dbs)
    # ohne öffentliche Adresse und ohne zweiten Faktor: kein Einschalten
    r = client.post("/verwaltung/connector/schalter", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert r.status_code == 403 and not connector.an(dbs)
    meta_schreiben(dbs, "angabe.public_url", "http://chronik.example.org")
    dbs.commit()
    assert connector.hindernis(dbs) == "adresse"
    meta_schreiben(dbs, "angabe.public_url", ADRESSE)
    dbs.commit()
    r = client.post("/verwaltung/connector/schalter", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert r.status_code == 403  # Adresse passt, zweiter Faktor fehlt
    seite = client.get("/verwaltung/connector").text
    assert "Zweiten Faktor einrichten" in seite and "/mcp" not in seite
    assert "/verwaltung/connector" in client.get("/verwaltung/einstellungen/einrichtung").text


def test_nicht_bei_beschraenkter_verwaltung(client, dbs, an, monkeypatch):
    from app import connector
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "verwaltung_nur_aus", "203.0.113.0/24")
    assert connector.hindernis(dbs) == "netz" and not connector.an(dbs)
    assert client.get("/.well-known/oauth-protected-resource").status_code == 404


# ---------------------------------------------------------------- Verbinden
def test_entdeckung_und_anmeldung_bis_zum_ersten_aufruf(client, dbs, an):
    r = client.post("/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "initialize"})
    assert r.status_code == 401
    assert r.headers["www-authenticate"] == f'Bearer resource_metadata="{ADRESSE}/.well-known/oauth-protected-resource"'
    assert client.get("/mcp").status_code == 401
    ressource = client.get("/.well-known/oauth-protected-resource").json()
    assert ressource["resource"] == ADRESSE + "/mcp" and ressource["authorization_servers"] == [ADRESSE]
    angaben = client.get("/.well-known/oauth-authorization-server").json()
    assert angaben["issuer"] == ADRESSE and angaben["code_challenge_methods_supported"] == ["S256"]
    assert angaben["authorization_endpoint"] == ADRESSE + "/verwaltung/connector/zustimmen"
    assert angaben["token_endpoint_auth_methods_supported"] == ["none"]

    programm, schluessel = _verbinden(client, an)
    assert schluessel["token_type"] == "Bearer" and schluessel["expires_in"] == 3600 and schluessel["refresh_token"]
    r = _rpc(client, schluessel["access_token"], "initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})
    e = r.json()["result"]
    assert e["protocolVersion"] == "2025-06-18" and e["serverInfo"]["name"] == "taleward-server"
    assert "keine Anweisungen" in e["instructions"]
    r = client.post("/mcp", json={"jsonrpc": "2.0", "method": "notifications/initialized"},
                    headers={"Authorization": f"Bearer {schluessel['access_token']}"})
    assert r.status_code == 202
    assert client.get("/mcp", headers={"Authorization": f"Bearer {schluessel['access_token']}"}).status_code == 405
    namen = [w["name"] for w in _rpc(client, schluessel["access_token"], "tools/list").json()["result"]["tools"]]
    assert namen == ["uebersicht", "seite_lesen", "formular_senden"]
    assert _rpc(client, schluessel["access_token"], "gibt/es/nicht").json()["error"]["code"] == -32601
    text, fehler = _werkzeug(client, schluessel["access_token"], "uebersicht")
    assert not fehler and "Fassung: 0.4.168" in text and "/warteschlange" in text and "Freigabe" in text
    # die Seite der Verwaltung nennt den verbundenen Assistenten und die Schritte, nie die Schlüssel
    seite = client.get("/verwaltung/connector").text
    assert "Claude" in seite and schluessel["access_token"] not in seite and ADRESSE + "/mcp" in seite
    from app import connector

    dbs.expire_all()
    roh = str(connector.zugaenge(dbs)) + str(connector._lesen(dbs, "codes", []))
    assert schluessel["access_token"] not in roh and schluessel["refresh_token"] not in roh  # nur Prüfsummen


def test_zustimmung_nur_angemeldet_mit_frischem_faktor(client, dbs, an):
    from tests.faktor_hilfe import frisch_merken
    from app.verwaltung import zweiter_faktor

    programm = _programm(client).json()["client_id"]
    fremd = client.__class__(client.app)  # nicht angemeldet: Hinweis statt Zustimmung, nichts gemerkt
    r = fremd.get("/verwaltung/connector/zustimmen", params=_anfrage(programm))
    assert r.status_code == 401 and "Zur Anmeldung" in r.text and 'value="ja"' not in r.text
    chef = _chef(dbs)
    zweiter_faktor.frisch_vergessen(dbs, chef.id)
    dbs.commit()
    r = client.get("/verwaltung/connector/zustimmen", params=_anfrage(programm))
    assert "Zweiten Faktor zeigen" in r.text and 'value="ja"' not in r.text
    r = client.post("/verwaltung/connector/zustimmen", data={"csrf": an, "antwort": "ja"}, follow_redirects=False)
    assert r.status_code == 303 and "/verwaltung/bestaetigen" in r.headers["location"]
    frisch_merken(client, dbs, chef.id)
    dbs.commit()
    r = client.get("/verwaltung/connector/zustimmen")  # nach dem Bestätigen: die Anfrage ist noch da
    assert 'value="ja"' in r.text
    r = client.post("/verwaltung/connector/zustimmen", data={"csrf": an, "antwort": "nein"})
    assert "error=access_denied" in r.text and "code=" not in r.text
    assert "nichts zu bestätigen" in client.get("/verwaltung/connector/zustimmen").text


def test_fremde_rueckleitung_falscher_nachweis_und_einmal_code(client, dbs, an):
    assert _programm(client, "https://boese.example/zurueck").json()["error"] == "invalid_redirect_uri"
    assert _programm(client, "http://localhost:3000/callback").status_code == 400
    programm = _programm(client).json()["client_id"]
    r = client.get("/verwaltung/connector/zustimmen", params=_anfrage(programm, redirect_uri="https://boese.example/"))
    assert r.status_code == 400 and 'value="ja"' not in r.text
    r = client.get("/verwaltung/connector/zustimmen", params=_anfrage(programm, code_challenge_method="plain"))
    assert r.status_code == 400
    r = client.get("/verwaltung/connector/zustimmen", params=_anfrage(programm, resource="https://anders.example/mcp"))
    assert r.status_code == 400
    code = _code(client, an, programm)
    assert _tausch(client, programm, code, "falsch" * 10).json() == {"error": "invalid_grant"}
    assert _tausch(client, programm, code).json() == {"error": "invalid_grant"}  # der Code ist verbraucht
    code = _code(client, an, programm)
    erster = _tausch(client, programm, code).json()
    assert _tausch(client, programm, code).json() == {"error": "invalid_grant"}
    # Erneuern: neuer Schlüssel, der alte zum Erneuern und der alte Zugang gelten nicht mehr
    r = client.post("/connector/token", data={"grant_type": "refresh_token", "refresh_token": erster["refresh_token"],
                                              "client_id": programm})
    zweiter = r.json()
    assert r.status_code == 200 and zweiter["access_token"] != erster["access_token"]
    r = client.post("/connector/token", data={"grant_type": "refresh_token", "refresh_token": erster["refresh_token"]})
    assert r.status_code == 400 and r.json() == {"error": "invalid_grant"}
    assert _rpc(client, erster["access_token"], "ping").status_code == 401
    assert _rpc(client, zweiter["access_token"], "ping").json()["result"] == {}
    assert client.post("/connector/token", data={"grant_type": "password"}).json()["error"] == "unsupported_grant_type"


# ---------------------------------------------------------------- Lesen und Ändern
def test_seite_lesen_und_formular_senden(client, dbs, an):
    from app import connector, discord_bot

    _, s = _verbinden(client, an)
    zugang = s["access_token"]
    text, fehler = _werkzeug(client, zugang, "seite_lesen", pfad="/discord")
    assert not fehler and "Seite: /discord (Status 200)" in text and "# Discord-Bot" in text
    assert '"pfad": "/discord/schalter"' in text and "csrf" not in text  # Formulare mit Pfad, ohne den Schutzwert
    assert "abmelden" not in text  # der Rahmen der Seite gehört nicht dazu
    text2, _ = _werkzeug(client, zugang, "seite_lesen", pfad="/verwaltung/discord")  # mit Vorsatz geht es auch
    assert text2 == text
    discord_bot.schalten(dbs, True)
    dbs.commit()
    text, fehler = _werkzeug(client, zugang, "formular_senden", pfad="/discord/schalter", felder={"an": "0"})
    dbs.expire_all()
    assert not fehler and "Gespeichert." in text and not discord_bot.an(dbs)
    zeilen = connector.protokoll_zeilen(dbs)
    assert [(z["art"], z["pfad"], z["status"]) for z in zeilen[:2]] == [("senden", "/discord/schalter", 200),
                                                                         ("lesen", "/discord", 200)]
    assert connector.zugaenge(dbs)[0]["zuletzt"]
    seite = client.get("/verwaltung/connector").text
    assert "/discord/schalter" in seite and "geändert" in seite
    text, fehler = _werkzeug(client, zugang, "seite_lesen", pfad="/gibt-es-nicht")
    assert fehler and "Status 404" in text


def test_geld_konten_schluessel_nur_in_der_freigabe(client, dbs, an):
    from app import connector, discord_bot
    from app.models import User

    _, s = _verbinden(client, an)
    zugang = s["access_token"]
    felder = {"username": "neu", "display_name": "Neu", "password": "ein-langes-passwort-91"}
    text, fehler = _werkzeug(client, zugang, "formular_senden", pfad="/konten", felder=felder)
    assert fehler and text == connector.FREIGABE_FEHLT
    text, fehler = _werkzeug(client, zugang, "formular_senden", pfad="/discord/schalter", felder={"an": "1"})
    dbs.expire_all()
    assert fehler and text == connector.FREIGABE_FEHLT and not discord_bot.an(dbs)
    assert dbs.scalar(select(User).where(User.username == "neu")) is None
    # der Verwalter gibt frei (frischer Faktor) – dann geht es, und nach dem Beenden wieder nicht
    r = client.post("/verwaltung/connector/freigabe", data={"csrf": an, "an": "1"}, follow_redirects=False)
    assert r.status_code == 303
    assert "noch 30 Minuten" in _werkzeug(client, zugang, "uebersicht")[0]
    text, fehler = _werkzeug(client, zugang, "formular_senden", pfad="/konten", felder=felder)
    dbs.expire_all()
    assert not fehler and dbs.scalar(select(User).where(User.username == "neu")) is not None
    assert "freigegeben bis" in client.get("/verwaltung/connector").text
    client.post("/verwaltung/connector/freigabe", data={"csrf": an, "an": "0"})
    felder["username"] = "nochmal"
    assert _werkzeug(client, zugang, "formular_senden", pfad="/konten", felder=felder)[0] == connector.FREIGABE_FEHLT
    # die Freigabe gilt nur für den Verwalter, der sie gegeben hat, und nie für die Guthaben-Seite
    from app.verwaltung import zweiter_faktor

    dbs.expire_all()
    connector.freigeben(dbs, _chef(dbs))
    sitzung = connector.SITZUNG_VORSATZ + "abc"
    assert zweiter_faktor.frisch(dbs, _chef(dbs).id, sitzung=sitzung)
    assert not zweiter_faktor.frisch(dbs, "jemand-anders", sitzung=sitzung)
    assert not zweiter_faktor.frisch(dbs, _chef(dbs).id, "guthaben", sitzung=sitzung)


def test_gesperrte_bereiche_und_pfade(client, dbs, an):
    from app import connector

    _, s = _verbinden(client, an)
    zugang = s["access_token"]
    for pfad in ("/connector", "/connector/freigabe", "/zweiter-faktor/aus", "/passkeys/beginnen", "/abmelden",
                 "/anmelden", "/bestaetigen", "/sicherungen/heute.zip", "/vertraute-geraete/entfernen",
                 "/verwaltung/connector/schalter"):
        for werkzeug in ("seite_lesen", "formular_senden"):
            text, fehler = _werkzeug(client, zugang, werkzeug, pfad=pfad)
            assert fehler and "nur in der Verwaltung selbst" in text, pfad
    for pfad in ("warteschlange", "https://boese.example/x", "//boese.example/x", "/../api/v1/health", "/a/%2e%2e/b",
                 "/konten\\..", None, 7):
        text, fehler = _werkzeug(client, zugang, "seite_lesen", pfad=pfad)
        assert fehler and ("nicht erlaubt" in text or "beginnen" in text), pfad
    dbs.expire_all()
    assert connector.freigabe_bis(dbs) is None and connector.an(dbs)
    # auch mit der Sitzung des Connectors selbst (am Werkzeug vorbei) geht die Seite des Connectors nicht
    cookie = connector.sitzungs_cookie(dbs, _chef(dbs), connector.zugaenge(dbs)[0]["id"])
    fremd = client.__class__(client.app)
    fremd.cookies.set("tw_verwaltung", cookie)
    assert fremd.get("/verwaltung/connector").status_code == 403
    from app.verwaltung.router import _csrf

    r = fremd.post("/verwaltung/connector/freigabe", data={"csrf": _csrf(cookie), "an": "1"}, follow_redirects=False)
    dbs.expire_all()
    assert r.status_code == 403 and connector.freigabe_bis(dbs) is None
    assert fremd.get("/verwaltung/warteschlange").status_code == 200  # sonst gilt die Sitzung


# ---------------------------------------------------------------- Ende des Zugangs
def test_ausschalten_trennen_faktor_aus_und_kein_verwalter_mehr(client, dbs, an):
    from app import connector
    from app.models import OrgMember
    from app.verwaltung import zweiter_faktor

    _, s = _verbinden(client, an)
    zugang = s["access_token"]
    assert _rpc(client, zugang, "ping").status_code == 200
    chef = _chef(dbs)
    zweiter_faktor.ausschalten(dbs, chef.id)  # ohne zweiten Faktor kein Connector
    dbs.commit()
    assert _rpc(client, zugang, "ping").status_code == 401
    _faktor_an(client, dbs, chef.id)
    assert _rpc(client, zugang, "ping").status_code == 200
    rolle = dbs.scalar(select(OrgMember).where(OrgMember.user_id == chef.id))
    rolle.role = "member"
    dbs.commit()
    assert _rpc(client, zugang, "ping").status_code == 401
    rolle.role = "admin"
    dbs.commit()
    kennung = connector.zugaenge(dbs)[0]["id"]
    r = client.post("/verwaltung/connector/trennen", data={"csrf": an, "zugang": kennung}, follow_redirects=False)
    assert r.status_code == 303 and _rpc(client, zugang, "ping").status_code == 401
    _, s = _verbinden(client, an)
    client.post("/verwaltung/connector/freigabe", data={"csrf": an, "an": "1"})
    r = client.post("/verwaltung/connector/schalter", data={"csrf": an, "an": "0"}, follow_redirects=False)
    assert r.status_code == 303  # Ausschalten: alles weg, auch nach dem Wiedereinschalten
    assert _rpc(client, s["access_token"], "ping").status_code == 404
    dbs.expire_all()
    connector.einschalten(dbs)
    dbs.commit()
    assert _rpc(client, s["access_token"], "ping").status_code == 401
    assert connector.zugaenge(dbs) == [] and connector.freigabe_bis(dbs) is None
    r = client.post("/connector/token", data={"grant_type": "refresh_token", "refresh_token": s["refresh_token"]})
    assert r.json() == {"error": "invalid_grant"}


def test_abgelaufener_zugang_und_netze(client, dbs, an, monkeypatch):
    from app import connector

    _, s = _verbinden(client, an)
    zugang = s["access_token"]
    r = client.post("/verwaltung/connector/netze", data={"csrf": an, "netze": "kein-netz"})
    assert r.status_code == 400 and "stimmt nicht" in r.text
    r = client.post("/verwaltung/connector/netze", data={"csrf": an, "netze": "160.79.104.0/21, 2001:db8::/32"},
                    follow_redirects=False)
    assert r.status_code == 303
    assert _rpc(client, zugang, "ping").status_code == 403  # der Test-Client kommt nicht aus diesem Netz
    assert _programm(client).status_code == 404
    dbs.expire_all()
    assert connector.netz_ok(dbs, "160.79.105.7") and connector.netz_ok(dbs, "::ffff:160.79.104.1")
    assert not connector.netz_ok(dbs, "203.0.113.5") and not connector.netz_ok(dbs, None)
    connector.netze_schreiben(dbs, "")
    dbs.commit()
    jetzt = connector._jetzt()
    monkeypatch.setattr(connector, "_jetzt", lambda: jetzt + connector.ZUGANG_SEKUNDEN + 1)
    assert _rpc(client, zugang, "ping").status_code == 401


# ---------------------------------------------------------------- Seiten als Text
def test_seite_als_text_und_archiv():
    from app import connector

    seite = """<html><head><title>Konten · Probe</title><style>x{}</style></head><body>
      <aside><nav><a href="/verwaltung/">Übersicht</a></nav><form method="post" action="/verwaltung/abmelden">
      <button>abmelden</button></form></aside><main><h1>Konten</h1><p>Zwei   Konten. <a href="/verwaltung/konten?x=1">mehr</a></p>
      <table><tr><th>Name</th><th>Rolle</th></tr><tr><td>Anna</td><td>SL</td></tr></table>
      <form method="post" action="/verwaltung/konten"><input type="hidden" name="csrf" value="geheim">
      <label>Name <input type="text" name="username" value="anna"></label>
      <label><input type="checkbox" name="admin" value="1" checked> Verwalter</label>
      <input type="password" name="password" value="nie-zeigen">
      <select name="rolle"><option value="a">Eins</option><option value="b" selected>Zwei</option></select>
      <textarea name="notiz">Hallo</textarea><button type="submit" name="art" value="neu">Anlegen</button>
      <button type="submit" disabled>Gesperrt</button></form><script>alert(1)</script></main></body></html>"""
    titel, text, formulare = connector.seite_als_text(seite)
    assert titel == "Konten · Probe" and "# Konten" in text and "Zwei Konten. mehr (→ /konten?x=1)" in text
    assert "| Name | Rolle" in text and "| Anna | SL" in text and "[x]" in text and "<Anlegen>" in text
    assert "abmelden" not in text and "alert" not in text and "geheim" not in text and "nie-zeigen" not in text
    assert len(formulare) == 1
    f = formulare[0]
    assert f["methode"] == "POST" and f["pfad"] == "/konten"
    felder = {x["name"]: x for x in f["felder"]}
    assert "csrf" not in felder and felder["username"]["wert"] == "anna" and felder["admin"]["an"] is True
    assert felder["password"]["wert"] == "" and felder["rolle"]["wert"] == "b" and felder["notiz"]["wert"] == "Hallo"
    assert [m["wert"] for m in felder["rolle"]["moeglich"]] == ["a", "b"]
    assert f["knoepfe"] == [{"text": "Anlegen", "aus": False, "name": "art", "wert": "neu"},
                            {"text": "Gesperrt", "aus": True}]
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w") as z:
        z.writestr("kapitel.txt", "Ein Kapitel.")
        z.writestr("ton.opus", b"\x00\x01")
    text, _ = connector.antwort_als_text("application/zip", puffer.getvalue())
    assert "===== kapitel.txt\nEin Kapitel." in text and "ton.opus (2 Bytes, nicht als Text lesbar)" in text
    assert connector.antwort_als_text("application/octet-stream", b"abc")[0].startswith("Datei (application/octet-stream")
    assert connector.antwort_als_text("application/zip", b"kaputt")[0] == "Das Archiv lässt sich nicht lesen."
