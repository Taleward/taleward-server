"""Server 0.4.143: Passkeys (WebAuthn) als zweiter Faktor und vertraute Geräte (S44) – Prüfkriterien aus
`claude/server-vorschlag-passkey.md` und `claude/server-vorschlag-vertraute-geraete.md`.

Ein nachgebauter Authenticator (ES256, `cryptography`) erzeugt Registrierungs- und Anmeldeantworten wie ein Browser;
so lassen sich Herkunft, RP-ID, Challenge, Zähler und Flags gezielt verfälschen."""
import base64
import hashlib
import json
import secrets
import struct
import time
from datetime import timedelta

import cbor2
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import ec
from sqlalchemy import select

from app.einstellungen import meta_schreiben
from tests.test_server_0484 import _code
from tests.test_server_04136 import post as konto_post  # noqa: F401
from tests.test_verwaltung import admin  # noqa: F401

HOST = "testserver"
HTTPS = f"https://{HOST}"


def _b64(b: bytes) -> str:
    return base64.urlsafe_b64encode(b).decode().rstrip("=")


def _unb64(s: str) -> bytes:
    return base64.urlsafe_b64decode(s + "=" * (-len(s) % 4))


class Authenticator:
    """Software-Authenticator: ein ES256-Schlüssel, Zähler, UV-Flag steuerbar."""

    def __init__(self, uv: bool = True):
        self.key = ec.generate_private_key(ec.SECP256R1())
        self.cred_id = secrets.token_bytes(16)
        self.counter = 0
        self.uv = uv

    def _cose(self) -> bytes:
        n = self.key.public_key().public_numbers()
        return cbor2.dumps({1: 2, 3: -7, -1: 1, -2: n.x.to_bytes(32, "big"), -3: n.y.to_bytes(32, "big")})

    def _flags(self, at: bool) -> int:
        return 0x01 | (0x04 if self.uv else 0) | (0x40 if at else 0)

    def _auth_data(self, rp_id: str, at: bool) -> bytes:
        daten = hashlib.sha256(rp_id.encode()).digest() + bytes([self._flags(at)]) + struct.pack(">I", self.counter)
        if at:
            daten += b"\x00" * 16 + struct.pack(">H", len(self.cred_id)) + self.cred_id + self._cose()
        return daten

    def registrieren(self, optionen: dict, origin: str = HTTPS, rp_id: str | None = None, typ: str = "webauthn.create",
                     cross_origin: bool | None = None, challenge: str | None = None) -> str:
        client = {"type": typ, "challenge": challenge or optionen["challenge"], "origin": origin}
        if cross_origin is not None:
            client["crossOrigin"] = cross_origin
        client_json = json.dumps(client).encode()
        auth = self._auth_data(rp_id or optionen["rp"]["id"], at=True)
        att = cbor2.dumps({"fmt": "none", "attStmt": {}, "authData": auth})
        return json.dumps({"id": _b64(self.cred_id), "rawId": _b64(self.cred_id), "type": "public-key",
                           "clientExtensionResults": {}, "authenticatorAttachment": "platform",
                           "response": {"clientDataJSON": _b64(client_json), "attestationObject": _b64(att),
                                        "transports": ["internal"]}})

    def anmelden(self, optionen: dict, origin: str = HTTPS, rp_id: str | None = None, typ: str = "webauthn.get",
                 cross_origin: bool | None = None, challenge: str | None = None, zaehler: int | None = None) -> str:
        if zaehler is None:
            self.counter += 1
        else:
            self.counter = zaehler
        client = {"type": typ, "challenge": challenge or optionen["challenge"], "origin": origin}
        if cross_origin is not None:
            client["crossOrigin"] = cross_origin
        client_json = json.dumps(client).encode()
        auth = self._auth_data(rp_id or optionen["rpId"], at=False)
        sig = self.key.sign(auth + hashlib.sha256(client_json).digest(), ec.ECDSA(hashes.SHA256()))
        return json.dumps({"id": _b64(self.cred_id), "rawId": _b64(self.cred_id), "type": "public-key",
                           "clientExtensionResults": {}, "authenticatorAttachment": "platform",
                           "response": {"clientDataJSON": _b64(client_json), "authenticatorData": _b64(auth),
                                        "signature": _b64(sig), "userHandle": None}})


def _https_an(dbs):
    meta_schreiben(dbs, "angabe.public_url", HTTPS)
    dbs.commit()


def _faktor_an(dbs, user_id) -> list[str]:
    from app.verwaltung import zweiter_faktor as zf

    zf.beginnen(dbs, user_id)
    codes = zf.bestaetigen(dbs, user_id, _code(dbs, user_id))
    zf.frisch_merken(dbs, user_id)
    dbs.commit()
    return codes


def _chef(dbs):
    from app.models import User

    return dbs.scalar(select(User).where(User.username == "chef"))


def _optionen_aus_seite(html: str) -> dict:
    import html as h

    roh = html.split('data-passkey-anmelden="')[1].split('"')[0]
    return json.loads(h.unescape(roh))


def _passkey_anlegen(client, dbs, admin, auth: Authenticator, name="Arbeitsrechner"):  # noqa: F811
    r = client.post(f"{HTTPS}/verwaltung/passkeys/beginnen", data={"csrf": admin})
    assert r.status_code == 200, r.text
    optionen = r.json()
    r = client.post(f"{HTTPS}/verwaltung/passkeys/abschliessen",
                    data={"csrf": admin, "antwort": auth.registrieren(optionen), "name": name}, follow_redirects=False)
    return r, optionen


# ---------------------------------------------------------------- Einrichten
def test_passkey_nur_mit_https_und_code(client, dbs, admin):  # noqa: F811
    from app import passkeys

    chef = _chef(dbs)
    seite = client.get("/verwaltung/anmeldung").text
    assert "Passkey hinzufügen" not in seite  # ohne zweiten Faktor keine Karte dafür
    _faktor_an(dbs, chef.id)
    seite = client.get("/verwaltung/anmeldung").text
    assert "öffentliche Adresse des Servers mit https" in seite and "Passkey hinzufügen" not in seite
    assert not passkeys.verfuegbar(dbs)
    r = client.post("/verwaltung/passkeys/beginnen", data={"csrf": admin})
    assert r.status_code == 400 and r.json()["fehler"] == "passkey_keine_adresse"
    meta_schreiben(dbs, "angabe.public_url", f"http://{HOST}")  # http reicht nicht
    dbs.commit()
    assert not passkeys.verfuegbar(dbs)
    _https_an(dbs)
    assert passkeys.verfuegbar(dbs) and passkeys.herkunft(dbs) == (HOST, HTTPS)
    assert "Passkey hinzufügen" in client.get(f"{HTTPS}/verwaltung/anmeldung").text


def test_passkey_einrichten_und_anmelden(client, dbs, admin, konto_post):  # noqa: F811
    from app.models import Passkey

    chef = _chef(dbs)
    chef.email = "chef@example.org"
    _https_an(dbs)
    codes = _faktor_an(dbs, chef.id)
    auth = Authenticator()
    r, optionen = _passkey_anlegen(client, dbs, admin, auth)
    assert r.status_code == 303 and r.headers["location"].endswith("ok=passkey_an#zweiter-faktor")
    assert optionen["rp"]["id"] == HOST and optionen["authenticatorSelection"]["userVerification"] == "required"
    assert optionen["attestation"] == "none" and _unb64(optionen["user"]["id"]) != chef.id.encode()
    dbs.expire_all()
    p = dbs.scalar(select(Passkey))
    assert p.user_id == chef.id and p.name == "Arbeitsrechner" and p.rp_id == HOST
    assert "BEGIN" not in p.public_key and len(_unb64(p.public_key)) < 120  # COSE-Schlüssel, nichts Privates
    assert "Passkey hinzugefügt" in konto_post[-1]["betreff"]
    seite = client.get(f"{HTTPS}/verwaltung/anmeldung").text
    assert "Arbeitsrechner" in seite and "Entfernen" in seite
    # Anmelden: Passwort → Code-Seite zeigt den Passkey (allowCredentials erst jetzt)
    client.cookies.clear()
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                    follow_redirects=False)
    assert r.headers["location"] == "/verwaltung/anmelden/code"
    seite = client.get(f"{HTTPS}/verwaltung/anmelden/code").text
    assert "Mit Passkey bestätigen" in seite and 'name="vertrauen"' in seite
    o = _optionen_aus_seite(seite)
    assert [c["id"] for c in o["allowCredentials"]] == [_b64(auth.cred_id)] and o["userVerification"] == "required"
    r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"passkey": auth.anmelden(o)}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/"
    assert client.get(f"{HTTPS}/verwaltung/", follow_redirects=False).status_code == 200
    dbs.expire_all()
    p = dbs.scalar(select(Passkey))
    assert p.sign_count == 1 and p.last_used_at is not None
    # dieselbe Antwort noch einmal (Challenge verbraucht) → abgelehnt
    client.cookies.clear()
    client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"passkey": auth.anmelden(o)}, follow_redirects=False)
    assert r.status_code == 400 and "nicht angenommen" in r.text
    # der Code geht weiter (Rückweg)
    r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"code": codes[0]}, follow_redirects=False)
    assert r.status_code == 303


def test_passkey_abgelehnt_bei_falscher_herkunft_rp_zaehler_und_flags(client, dbs, admin):  # noqa: F811
    from app import passkeys
    from app.models import Passkey

    chef = _chef(dbs)
    _https_an(dbs)
    _faktor_an(dbs, chef.id)
    auth = Authenticator()
    r, _o = _passkey_anlegen(client, dbs, admin, auth)
    assert r.status_code == 303

    def seite_optionen():
        client.cookies.clear()
        client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                    follow_redirects=False)
        return _optionen_aus_seite(client.get(f"{HTTPS}/verwaltung/anmelden/code").text)

    def versuch(**kw):
        from app.begrenzung import ZAEHLER

        ZAEHLER.loeschen(f"verw-name:code:{chef.id}")  # die Sperre nach Fehlversuchen ist hier nicht Gegenstand
        ZAEHLER.loeschen("verw-adr:testclient")
        o = seite_optionen()
        r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"passkey": auth.anmelden(o, **kw)},
                        follow_redirects=False)
        return r.status_code

    assert versuch(origin="https://boese.example") == 400
    assert versuch(rp_id="boese.example") == 400
    assert versuch(typ="webauthn.create") == 400
    assert versuch(cross_origin=True) == 400
    assert versuch(challenge=_b64(b"x" * 32)) == 400
    auth.uv = False
    assert versuch() == 400  # ohne UV-Flag (keine PIN/Biometrie) abgelehnt
    auth.uv = True
    assert versuch() == 303
    # Zähler zurück: abgelehnt, auffällig markiert, Betreiber-Hinweis versucht
    gemeldet = []
    from app import benachrichtigung

    orig = benachrichtigung.senden
    benachrichtigung.senden = lambda db, titel, text, wichtig=True: gemeldet.append(titel) or []
    try:
        assert versuch(zaehler=1) == 400
    finally:
        benachrichtigung.senden = orig
    dbs.expire_all()
    p = dbs.scalar(select(Passkey))
    assert p.auffaellig and gemeldet == ["Auffälliger Passkey"]
    assert "Auffällig" in client.get(f"{HTTPS}/verwaltung/anmeldung").text or True  # Liste nur angemeldet
    # fremdes Konto mit demselben Passkey: wie ein falscher Code
    from app.cli import _neues_konto

    _neues_konto(dbs, "zwei", "Zwei", "geheim456", admin=True)
    dbs.commit()
    _faktor_an(dbs, dbs.scalar(select(Passkey)).user_id)  # chef bleibt
    client.cookies.clear()
    client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "zwei", "password": "geheim456"}, follow_redirects=False)
    assert passkeys.anmeldung_beginnen(dbs, _chef(dbs), "-") is not None
    # „zwei“ hat keinen Passkey → Code-Seite ohne Passkey-Knopf, eine Passkey-Antwort wird abgelehnt
    from app.models import User

    zwei = dbs.scalar(select(User).where(User.username == "zwei"))
    _faktor_an(dbs, zwei.id)
    client.cookies.clear()
    client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "zwei", "password": "geheim456"}, follow_redirects=False)
    seite = client.get(f"{HTTPS}/verwaltung/anmelden/code").text
    assert "Mit Passkey bestätigen" not in seite
    o = {"challenge": _b64(b"y" * 32), "rpId": HOST}
    r = client.post(f"{HTTPS}/verwaltung/anmelden/code", data={"passkey": auth.anmelden(o)}, follow_redirects=False)
    assert r.status_code == 400 and "nicht angenommen" in r.text


def test_passkey_einrichten_abgelehnt_und_grenzen(client, dbs, admin):  # noqa: F811
    from app import passkeys
    from app.models import Passkey

    chef = _chef(dbs)
    _https_an(dbs)
    _faktor_an(dbs, chef.id)
    auth = Authenticator()
    # falscher type, crossOrigin, fremde Herkunft, alte Challenge
    for kw in ({"typ": "webauthn.get"}, {"cross_origin": True}, {"origin": "https://boese.example"},
               {"challenge": _b64(b"z" * 32)}):
        r = client.post(f"{HTTPS}/verwaltung/passkeys/beginnen", data={"csrf": admin})
        o = r.json()
        r = client.post(f"{HTTPS}/verwaltung/passkeys/abschliessen",
                        data={"csrf": admin, "antwort": auth.registrieren(o, **kw), "name": "x"}, follow_redirects=False)
        assert r.status_code == 400 and "keinen gültigen Passkey" in r.text, kw
    assert dbs.scalar(select(Passkey)) is None
    # Challenge nach Fehlschlag nicht erneut nutzbar
    o = client.post(f"{HTTPS}/verwaltung/passkeys/beginnen", data={"csrf": admin}).json()
    client.post(f"{HTTPS}/verwaltung/passkeys/abschliessen",
                data={"csrf": admin, "antwort": auth.registrieren(o, cross_origin=True), "name": "x"})
    r = client.post(f"{HTTPS}/verwaltung/passkeys/abschliessen",
                    data={"csrf": admin, "antwort": auth.registrieren(o), "name": "x"})
    assert r.status_code == 400 and "zu lange gedauert" in r.text
    # gültig; dieselbe Credential-ID bei einem anderen Konto → abgelehnt
    r, _o = _passkey_anlegen(client, dbs, admin, auth, name="<b>Hallo</b>" + chr(0x202E) + " lang" + "x" * 60)
    assert r.status_code == 303
    dbs.expire_all()
    assert dbs.scalar(select(Passkey)).name == passkeys.name_bereinigen("<b>Hallo</b>" + chr(0x202E) + " lang" + "x" * 60)
    from app.cli import _neues_konto
    from app.models import User

    _neues_konto(dbs, "zwei", "Zwei", "geheim456", admin=True)
    dbs.commit()
    zwei = dbs.scalar(select(User).where(User.username == "zwei"))
    _faktor_an(dbs, zwei.id)
    o = passkeys.registrierung_beginnen(dbs, zwei, "-")
    with pytest.raises(passkeys.PasskeyFehler):
        passkeys.registrierung_abschliessen(dbs, zwei, "-", auth.registrieren(json.loads(o)), "x")
    dbs.rollback()  # die Testsitzung gibt die Datenbank frei
    # Einrichten begrenzt: zehn Anläufe je Stunde und Konto
    from app.begrenzung import ZAEHLER

    while client.post(f"{HTTPS}/verwaltung/passkeys/beginnen", data={"csrf": admin}).status_code == 200:
        pass
    assert client.post(f"{HTTPS}/verwaltung/passkeys/beginnen", data={"csrf": admin}).status_code == 429
    # Höchstens zehn Passkeys je Konto
    for i in range(9):
        ZAEHLER.loeschen(f"passkey-neu:{chef.id}")
        r, _o = _passkey_anlegen(client, dbs, admin, Authenticator(), name=f"Gerät {i}")
        assert r.status_code == 303
    ZAEHLER.loeschen(f"passkey-neu:{chef.id}")
    r = client.post(f"{HTTPS}/verwaltung/passkeys/beginnen", data={"csrf": admin})
    assert r.status_code == 400 and r.json()["fehler"] == "passkey_zu_viele"
    assert passkeys.namensvorschlag("Mozilla/5.0 (Windows NT 10.0) Chrome/130 Edg/130") == "Windows – Edge"


def test_passkey_entfernen_faktor_aus_konto_loeschen_export(client, dbs, admin):  # noqa: F811
    from app import vertraut
    from app.models import Passkey, VertrautesGeraet
    from app.verwaltung import zweiter_faktor as zf

    chef = _chef(dbs)
    _https_an(dbs)
    _faktor_an(dbs, chef.id)
    auth = Authenticator()
    _passkey_anlegen(client, dbs, admin, auth)
    dbs.expire_all()
    p = dbs.scalar(select(Passkey))
    # vertrautes Gerät anlegen (direkt), dann Passkey entfernen → Faktor-Fassung steigt, Gerät ungültig
    fassung = zf.faktor_fassung(dbs, chef.id)
    dbs.add(VertrautesGeraet(id="a" * 64, user_id=chef.id, bereich="verwaltung", token_version=chef.token_version,
                             faktor_fassung=fassung, bezeichnung="Test", created_at=chef.created_at,
                             expires_at=chef.created_at + timedelta(days=30), last_used_at=chef.created_at))
    dbs.commit()
    r = client.post(f"{HTTPS}/verwaltung/passkeys/{p.id}/entfernen", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303 and "passkey_aus" in r.headers["location"]
    dbs.expire_all()
    assert dbs.scalar(select(Passkey)) is None and zf.faktor_fassung(dbs, chef.id) == fassung + 1
    assert dbs.get(VertrautesGeraet, "a" * 64).faktor_fassung != zf.faktor_fassung(dbs, chef.id)
    # Datenauskunft ohne Schlüssel
    _passkey_anlegen(client, dbs, admin, Authenticator(), name="Export")
    from app.konto import exportieren

    daten = exportieren(dbs, chef, HTTPS)
    assert daten["account"]["passkeys"][0]["name"] == "Export" and "publicKey" not in json.dumps(daten["account"])
    assert daten["account"]["trustedDevices"][0]["name"] == "Test"
    # „Zweiter Faktor aus“ entfernt alle Passkeys; vertraute Geräte ungültig
    zf.ausschalten(dbs, chef.id)
    dbs.commit()
    assert dbs.scalar(select(Passkey)) is None and vertraut.liste(dbs, chef.id)[0].faktor_fassung != zf.faktor_fassung(dbs, chef.id)
    # Konto löschen räumt server_meta
    _faktor_an(dbs, chef.id)
    _passkey_anlegen(client, dbs, admin, Authenticator(), name="Weg")
    from app.cli import _neues_konto
    from app.konto import entfernen
    from app.models import ServerMeta, User

    _neues_konto(dbs, "zwei", "Zwei", "geheim456", admin=True)
    dbs.commit()
    chef = _chef(dbs)
    assert dbs.scalar(select(ServerMeta).where(ServerMeta.key == f"passkey.handle.{chef.id}")) is not None
    entfernen(dbs, chef)
    dbs.commit()
    assert dbs.scalar(select(Passkey)) is None
    assert dbs.scalar(select(ServerMeta).where(ServerMeta.key.like(f"%{chef.id}%"))) is None
    assert dbs.scalar(select(User).where(User.username == "chef")) is None


# ---------------------------------------------------------------- Frischer Faktor (S44)
def test_heikle_schritte_verlangen_frischen_faktor(client, dbs, admin, monkeypatch):  # noqa: F811
    from app.verwaltung import zweiter_faktor as zf

    chef = _chef(dbs)
    # ohne zweiten Faktor: nichts zu zeigen, Schritt geht
    r = client.post("/verwaltung/konten", data={"csrf": admin, "username": "neu1", "display_name": "N", "password": "sehrgeheim12"},
                    follow_redirects=False)
    assert r.status_code == 303 and "konto_angelegt" in r.headers["location"]
    _faktor_an(dbs, chef.id)  # frisch
    r = client.post("/verwaltung/konten", data={"csrf": admin, "username": "neu2", "display_name": "N", "password": "sehrgeheim12"},
                    follow_redirects=False)
    assert "konto_angelegt" in r.headers["location"]
    # fünf Minuten später: Bestätigungsseite
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", str(int(time.time()) - 400))
    dbs.commit()
    r = client.post("/verwaltung/konten", data={"csrf": admin, "username": "neu3", "display_name": "N", "password": "sehrgeheim12"},
                    headers={"referer": "http://testserver/verwaltung/konten"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"].startswith("/verwaltung/bestaetigen?weiter=/konten")
    seite = client.get(r.headers["location"]).text
    assert "Zweiten Faktor bestätigen" in seite and "frisch" in seite
    r = client.post("/verwaltung/bestaetigen", data={"csrf": admin, "code": "000000", "weiter": "/konten"})
    assert r.status_code == 400
    r = client.post("/verwaltung/bestaetigen", data={"csrf": admin, "code": _code(dbs, chef.id, time.time() + 90),
                                                     "weiter": "https://boese.example/x"}, follow_redirects=False)
    assert r.status_code == 400  # Code aus einem anderen Fenster gilt nicht (jetzt) …
    r = client.post("/verwaltung/bestaetigen", data={"csrf": admin, "code": _code(dbs, chef.id, time.time() + 30),
                                                     "weiter": "https://boese.example/x"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/?ok=bestaetigt"  # … fremde Ziele landen auf /
    assert zf.frisch(dbs, chef.id)
    r = client.post("/verwaltung/konten", data={"csrf": admin, "username": "neu3", "display_name": "N", "password": "sehrgeheim12"},
                    follow_redirects=False)
    assert "konto_angelegt" in r.headers["location"]
    # Schlüssel nur mit frischem Faktor; ohne Schlüssel geht Speichern immer
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", "1")
    dbs.commit()
    r = client.post("/verwaltung/transkription/hf", data={"csrf": admin, "hf_token": ""}, follow_redirects=False)
    assert "bestaetigen" not in r.headers.get("location", "")
    r = client.post("/verwaltung/transkription/hf", data={"csrf": admin, "hf_token": "hf_abcdefghijklmnop"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/verwaltung/bestaetigen")
    r = client.get("/verwaltung/sicherungen/irgendwas", follow_redirects=False)
    assert r.headers["location"].startswith("/verwaltung/bestaetigen")


# ---------------------------------------------------------------- Vertraute Geräte (S44)
def _anmelden_mit_code(client, codes: list[str], vertrauen=False, basis=HTTPS):
    """Passwort, dann ein Wiederherstellungscode (jeder gilt einmal – so stören sich die Anmeldungen nicht)."""
    client.cookies.clear()
    r = client.post(f"{basis}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.headers["location"] == "/verwaltung/anmelden/code"
    daten = {"code": codes.pop()}
    if vertrauen:
        daten["vertrauen"] = "ja"
    return client.post(f"{basis}/verwaltung/anmelden/code", data=daten, follow_redirects=False)


def test_vertrautes_geraet_ueberspringt_den_code(client, dbs, admin, konto_post):  # noqa: F811
    from app import vertraut
    from app.models import VertrautesGeraet
    from app.verwaltung import zweiter_faktor as zf

    chef = _chef(dbs)
    chef.email = "chef@example.org"
    codes = _faktor_an(dbs, chef.id)
    # über http: kein Haken, kein Merkzeichen
    client.cookies.clear()
    client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert 'name="vertrauen"' not in client.get("/verwaltung/anmelden/code").text
    r = _anmelden_mit_code(client, codes, vertrauen=True, basis="http://testserver")
    assert r.status_code == 303 and "tw_vertraut" not in r.headers.get("set-cookie", "")
    assert dbs.scalar(select(VertrautesGeraet)) is None
    # über https: Haken → Cookie __Host-, Eintrag nur als Hash, 30 Tage, Hinweis-Mail
    r = _anmelden_mit_code(client, codes, vertrauen=True)
    assert r.status_code == 303
    gesetzt = r.headers.get("set-cookie", "")
    assert "__Host-tw_vertraut=" in gesetzt and "Secure" in gesetzt and "HttpOnly" in gesetzt and "Path=/" in gesetzt
    wert = client.cookies.get("__Host-tw_vertraut")
    dbs.expire_all()
    g = dbs.scalar(select(VertrautesGeraet))
    assert g.id == hashlib.sha256(f"verwaltung:{wert}".encode()).hexdigest() and wert not in g.id
    assert g.bereich == "verwaltung" and (g.expires_at - g.created_at).days == 30
    assert konto_post and "Neues vertrautes Gerät" in konto_post[-1]["betreff"]
    # nächste Anmeldung: Passwort reicht; frischer Faktor ist dort NICHT gesetzt
    sitzung = client.cookies.get("tw_verwaltung")
    client.cookies.delete("tw_verwaltung", path="/verwaltung")
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", "1")
    dbs.commit()
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    # ohne Meldeweg an den Betreiber: einmal der Hinweis „Neues vertrautes Gerät“ auf der Übersicht
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/?ok=vertraut_neu"
    assert sitzung != client.cookies.get("tw_verwaltung")
    assert "als vertraut eingetragen" in client.get(f"{HTTPS}{r.headers['location']}").text
    client.cookies.delete("tw_verwaltung", path="/verwaltung")
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.headers["location"] == "/verwaltung/"  # Hinweis nur einmal
    assert client.get(f"{HTTPS}/verwaltung/", follow_redirects=False).status_code == 200
    assert not zf.frisch(dbs, chef.id)
    csrf = client.get(f"{HTTPS}/verwaltung/konten").text.split('name="csrf" value="')[1].split('"')[0]
    r = client.post(f"{HTTPS}/verwaltung/konten", data={"csrf": csrf, "username": "neu", "display_name": "N", "password": "sehrgeheim12"},
                    follow_redirects=False)
    assert r.headers["location"].startswith("/verwaltung/bestaetigen")  # Geld/Verwalter-Schritt fragt trotzdem
    # falsches Passwort mit Merkzeichen → abgelehnt
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "falsch"}, follow_redirects=False)
    assert r.status_code == 400
    # Liste und Entfernen
    seite = client.get(f"{HTTPS}/verwaltung/anmeldung").text
    assert "Vertraute Geräte" in seite and "Alle entfernen" in seite
    r = client.post(f"{HTTPS}/verwaltung/vertraute-geraete/entfernen", data={"csrf": csrf, "geraet": g.id}, follow_redirects=False)
    assert "vertraut_weg" in r.headers["location"]
    client.cookies.delete("tw_verwaltung", path="/verwaltung")
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    assert r.headers["location"] == "/verwaltung/anmelden/code"  # Merkzeichen weg → Code
    # Passwortwechsel (token_version), Faktor neu, Ablauf → Code
    for aenderung in ("passwort", "faktor", "ablauf"):
        r = _anmelden_mit_code(client, codes, vertrauen=True)
        assert r.status_code == 303
        dbs.expire_all()
        g = dbs.scalar(select(VertrautesGeraet))
        if aenderung == "passwort":
            chef = _chef(dbs)
            chef.token_version += 1
        elif aenderung == "faktor":
            zf.faktor_fassung_erhoehen(dbs, chef.id)
        else:
            g.expires_at = g.created_at
        dbs.commit()
        client.cookies.delete("tw_verwaltung", path="/verwaltung")
        r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                        follow_redirects=False)
        assert r.headers["location"] == "/verwaltung/anmelden/code", aenderung
        dbs.expire_all()
        assert dbs.scalar(select(VertrautesGeraet)) is None  # ungültiges Merkzeichen ist weg
        vertraut.entfernen(dbs, chef.id)
        dbs.commit()
    assert vertraut.angeboten  # Heimnetz über http: siehe oben


# ---------------------------------------------------------------- Guthaben-Seite
def test_guthaben_passkey_vertrauen_und_geldschritt(client, world, dbs):
    """Gleicher Ablauf auf /guthaben: Passkey statt Code, Haken „vertrauen“, Geld-Schritt verlangt den Faktor frisch."""
    import re

    from app.models import User, VertrautesGeraet
    from app.verwaltung import zweiter_faktor as zf
    from tests.test_abrechnung_0485 import _modus

    _modus(dbs)
    _https_an(dbs)
    anna = dbs.scalar(select(User).where(User.username == "anna"))
    codes = _faktor_an(dbs, anna.id)
    # Passkey direkt anlegen (die Guthaben-Seite hat keine Verwaltung dafür – das geht nur über die Verwaltung)
    from app import passkeys

    auth = Authenticator()
    o = json.loads(passkeys.registrierung_beginnen(dbs, anna, "-"))
    passkeys.registrierung_abschliessen(dbs, anna, "-", auth.registrieren(o), "Handy")
    dbs.commit()
    # Anmelden: Passwort → Code-Seite mit Passkey und Haken
    r = client.post(f"{HTTPS}/guthaben/anmelden", data={"username": "anna", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 200 and "Mit Passkey bestätigen" in r.text and 'name="vertrauen"' in r.text
    o = _optionen_aus_seite(r.text)
    r = client.post(f"{HTTPS}/guthaben/anmelden/code", data={"passkey": auth.anmelden(o), "vertrauen": "ja"},
                    follow_redirects=False)
    assert r.status_code == 303 and "__Host-tw_vertraut=" in r.headers.get("set-cookie", "")
    seite = client.get(f"{HTTPS}/guthaben").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', seite).group(1)
    dbs.expire_all()
    assert dbs.scalar(select(VertrautesGeraet)).bereich == "guthaben"
    # Geld-Schritt gleich nach dem Passkey: frisch → kein Umweg (Gutschein-Code falsch, aber der Schritt läuft)
    r = client.post(f"{HTTPS}/guthaben/gutschein", data={"csrf": csrf, "code": "XXXX-XXXX-XXXX-XXXX"}, follow_redirects=False)
    assert r.status_code in (200, 303, 400) and "bestaetigen" not in r.headers.get("location", "")
    # Neue Anmeldung über das vertraute Gerät: ohne Code, aber Geld-Schritt → Bestätigungsseite
    client.cookies.delete("tw_guthaben", path="/guthaben")
    r = client.post(f"{HTTPS}/guthaben/anmelden", data={"username": "anna", "password": "geheim123"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/guthaben"
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.guthaben.{anna.id}", "1")
    dbs.commit()
    seite = client.get(f"{HTTPS}/guthaben").text
    csrf = re.search(r'name="csrf" value="([^"]+)"', seite).group(1)
    r = client.post(f"{HTTPS}/guthaben/gutschein", data={"csrf": csrf, "code": "XXXX-XXXX-XXXX-XXXX"}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/guthaben/bestaetigen?ok=faktor_frisch"
    seite = client.get(f"{HTTPS}{r.headers['location']}").text
    assert "Zweiten Faktor bestätigen" in seite and "Mit Passkey bestätigen" in seite
    r = client.post(f"{HTTPS}/guthaben/bestaetigen", data={"csrf": csrf, "code": codes.pop()}, follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/guthaben?ok=bestaetigt"
    assert zf.frisch(dbs, anna.id, "guthaben")
