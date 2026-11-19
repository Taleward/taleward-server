"""Server 0.4.170: feste Liste für das Sprechermodell, Wiedereintritt nur mit neuer Einladung, geheim Mitgebrachtes,
Word-Dokumente im eigenen Prozess, Umzugsarchiv, Arbeit außerhalb des Ereignis-Takts, Grenzen für Versuche."""
import asyncio
import hashlib
import io
import json
import shutil
import uuid
import zipfile

import pytest
from sqlalchemy import select

from tests.test_charaktere_047 import _char, _einladen
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

API = "/api/v1"
REPO = "pyannote/speaker-diarization-community-1"
COMMIT = "a" * 40
INHALT = {"config.yaml": b"pipeline: probe\n", "gewichte/modell.bin": b"\x00\x01" * 500}


def _summe(daten: bytes) -> str:
    return hashlib.sha256(daten).hexdigest()


@pytest.fixture()
def liste(monkeypatch):
    """Feste Liste mit Probedateien statt des echten Modells."""
    from app import modelle

    monkeypatch.setattr(modelle, "FEST", {REPO: (COMMIT, {p: (len(d), _summe(d)) for p, d in INHALT.items()})})
    return modelle


class Quelle:
    """Server aus Sicht des Workers: Verzeichnis und Dateien, wie der Test sie vorgibt."""

    def __init__(self, fassung=COMMIT, dateien=None, inhalt=None):
        self.fassung, self.inhalt = fassung, dict(INHALT if inhalt is None else inhalt)
        self.dateien = list(self.inhalt) if dateien is None else dateien
        self.geladen = []

    def verzeichnis(self, repo):
        return {"fassung": self.fassung, "dateien": [{"pfad": p, "sha256": "f" * 64} for p in self.dateien]}

    def laden(self, repo, fassung, pfad, ziel, hoechstens=10 ** 9):
        self.geladen.append(pfad)
        ziel.parent.mkdir(parents=True, exist_ok=True)
        ziel.write_bytes(self.inhalt[pfad])
        return _summe(self.inhalt[pfad])


# ---------------------------------------------------------------- Sprechermodell: nur die geprüfte Fassung
def test_echte_liste_passt_zum_verzeichnis_des_spiegels():
    from app import modellablage, modelle

    fassung, dateien = modelle.FEST[modelle.SPRECHERMODELL]
    assert modelle.commit_ok(fassung) and modellablage.SPIEGEL[modelle.SPRECHERMODELL][0] == fassung
    manifest = {"repo": modelle.SPRECHERMODELL, "fassung": fassung,
                "dateien": [{"pfad": p, "groesse": g, "sha256": s} for p, (g, s) in sorted(dateien.items())]}
    assert _summe(json.dumps(manifest, indent=2).encode()) == modellablage.SPIEGEL[modelle.SPRECHERMODELL][1]
    assert modelle.feste_fassung(modelle.SPRECHERMODELL) == fassung and "config.yaml" in dateien


def test_geprueftes_modell_wird_geladen_und_beim_naechsten_mal_wieder_geprueft(client, liste):
    q = Quelle()
    b = liste.vom_server(REPO, q)
    assert b.fassung == COMMIT and sorted(q.geladen) == sorted(INHALT)
    assert (b.pfad / "gewichte" / "modell.bin").read_bytes() == INHALT["gewichte/modell.bin"]
    q2 = Quelle()
    assert liste.vom_server(REPO, q2).pfad == b.pfad and q2.geladen == []  # unverändert: nichts neu geladen
    # nachträglich veränderter Zwischenspeicher: verworfen und frisch geholt, nie so geladen
    (b.pfad / "gewichte" / "modell.bin").write_bytes(b"veraendert")
    q3 = Quelle()
    b3 = liste.vom_server(REPO, q3)
    assert sorted(q3.geladen) == sorted(INHALT) and (b3.pfad / "gewichte" / "modell.bin").read_bytes() == INHALT["gewichte/modell.bin"]
    # eine zusätzliche Datei im Ordner: ebenso
    (b.pfad / "boese.py").write_text("print(1)")
    q4 = Quelle()
    liste.vom_server(REPO, q4)
    assert q4.geladen and not (b.pfad / "boese.py").exists()


@pytest.mark.parametrize("quelle, text", [
    (Quelle(fassung="b" * 40), "nur die geprüfte Fassung"),                                 # fremde Fassung
    (Quelle(dateien=[*INHALT, "boese.py"], inhalt={**INHALT, "boese.py": b"x"}), "boese.py"),  # Datei außerhalb der Liste
    (Quelle(dateien=["config.yaml"]), "nicht genau die geprüften Dateien"),                 # eine Datei fehlt
])
def test_abweichendes_angebot_wird_abgelehnt_bevor_etwas_geladen_wird(client, liste, quelle, text):
    with pytest.raises(liste.ModellFehler, match=text):
        liste.vom_server(REPO, quelle)
    assert quelle.geladen == []
    from app.config import get_settings

    assert not list((get_settings().data_dir / "modelle").rglob("*.bin"))


def test_gleiche_fassung_mit_anderer_datei(client, liste):
    q = Quelle(inhalt={**INHALT, "gewichte/modell.bin": b"\x00\x02" * 500})  # gleiche Größe, anderer Inhalt
    with pytest.raises(liste.ModellFehler, match="Prüfsumme"):
        liste.vom_server(REPO, q)
    from app.config import get_settings

    assert not list((get_settings().data_dir / "modelle").rglob("*.bin"))  # nichts halb Geladenes bleibt


def test_gemerktes_modell_ohne_server_nur_wenn_es_noch_stimmt(client, liste):
    import httpx

    b = liste.vom_server(REPO, Quelle())

    class Weg(Quelle):
        def verzeichnis(self, repo):
            raise httpx.ConnectError("aus")

    assert liste.vom_server(REPO, Weg()).quelle == "gemerkt"
    (b.pfad / "config.yaml").write_bytes(b"pipeline: fremd\n")
    with pytest.raises(liste.ModellFehler, match="nicht erreichbar"):
        liste.vom_server(REPO, Weg())


def test_eigener_abruf_nur_mit_genau_diesen_dateien(client, liste, tmp_path, monkeypatch):
    ordner = tmp_path / COMMIT
    for p, d in INHALT.items():
        (ordner / p).parent.mkdir(parents=True, exist_ok=True)
        (ordner / p).write_bytes(d)
    gefragt = []
    monkeypatch.setattr(liste, "_laden", lambda repo, fassung, token, nur_lokal: gefragt.append(fassung) or ordner)
    assert liste.bereitstellen(REPO, "hf_x").fassung == COMMIT and set(gefragt) == {COMMIT}  # nur dieser Commit
    (ordner / "gewichte" / "modell.bin").write_bytes(b"anders")
    with pytest.raises(liste.ModellFehler, match="nicht die geprüfte Fassung"):
        liste.bereitstellen(REPO, "hf_x")
    shutil.rmtree(ordner / "gewichte")
    with pytest.raises(liste.ModellFehler, match="fehlt"):
        liste.bereitstellen(REPO, "hf_x")


def test_download_hat_eine_obergrenze(client, tmp_path):
    import httpx

    from app import modelle

    klient = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, content=b"x" * 5000)))
    q = modelle.ServerQuelle("http://server.example", "t", klient=klient)
    with pytest.raises(modelle.ModellFehler, match="größer als erwartet"):
        q.laden("org/modell", COMMIT, "a.bin", tmp_path / "a.bin", 1000)
    assert not (tmp_path / "a.bin").exists()
    assert q.laden("org/modell", COMMIT, "a.bin", tmp_path / "a.bin", 5000) == _summe(b"x" * 5000)


def test_server_legt_nur_die_gepruefte_fassung_ab(client, dbs, liste):
    from app import modellablage
    from tests.test_modellablage import hf_transport

    from app.einstellungen import meta_schreiben

    meta_schreiben(dbs, "hf.token", "hf_gut")
    dbs.commit()
    with pytest.raises(modellablage.AblageFehler):  # die Quelle des Tests kennt die geprüfte Fassung nicht
        modellablage.holen(dbs, klient=hf_transport())
    assert modellablage.vorhanden(dbs) is None

    def fremd(tmp):
        (tmp / "config.yaml").write_bytes(b"anders")
        return [{"pfad": "config.yaml", "groesse": 6, "sha256": _summe(b"anders")}]

    with pytest.raises(modellablage.AblageFehler, match="nicht die geprüfte Fassung"):
        modellablage._ablegen(REPO, COMMIT, "huggingface", fremd)
    assert not modellablage._ordner(REPO, COMMIT).exists()


# ---------------------------------------------------------------- Startguthaben: eine Adresse, viele Schreibweisen
def test_gleiche_postfaecher_gleiche_pruefsumme(client, dbs):
    from app import startguthaben

    assert startguthaben.pruefsumme(dbs, "a.b+x@googlemail.com") == startguthaben.pruefsumme(dbs, "AB@gmail.com")
    assert startguthaben.pruefsumme(dbs, "a.b@example.org") != startguthaben.pruefsumme(dbs, "ab@example.org")
    assert startguthaben.normalisiert("A.B.C+neu@Gmail.com") == "abc@gmail.com"


# ---------------------------------------------------------------- Zähler und Anmeldename
def test_ueberlanger_name_bleibt_klein(client, dbs, admin):  # noqa: F811
    from app import begrenzung

    lang = "x" * 500_000
    assert len(begrenzung.kurz(f"login-name:{lang}")) <= begrenzung.MAX_LAENGE
    assert begrenzung.kurz("login-name:anna") == "login-name:anna"
    assert begrenzung.kurz(f"a:{lang}") != begrenzung.kurz(f"a:{lang}y")  # verschiedene Namen, verschiedene Zähler
    r = client.post(f"{API}/auth/login", json={"username": lang, "password": "x"})
    assert r.status_code in (400, 422)
    fremd = client.__class__(client.app)
    r = fremd.post("/verwaltung/anmelden", data={"username": lang, "password": "x"})
    assert r.status_code in (400, 422)
    begrenzung.ZAEHLER.versuch(f"probe:{lang}", 5, 60)
    assert max(len(k) for k in begrenzung.ZAEHLER._daten) <= begrenzung.MAX_LAENGE
    # unbekannter Name in der Verwaltung: dieselbe Antwort wie bei falschem Passwort
    r = fremd.post("/verwaltung/anmelden", data={"username": "gibtesnicht", "password": "falsch-falsch"})
    assert "Benutzername oder Passwort stimmt nicht." in r.text


# ---------------------------------------------------------------- Entfernt: zurück nur mit neuer Einladung
def test_entfernte_person_braucht_eine_neue_einladung(client, world):
    w = world
    alt = _einladen(client, w)
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["gm"]).status_code == 204
    r = client.post(f"{API}/campaigns/join", headers=w["pl"], json={"code": alt})
    assert r.status_code == 404 and r.json()["code"] == "invite_invalid"  # wie ein ungültiger Code
    assert client.get(f"{API}/campaigns/{w['cid']}", headers=w["pl"]).status_code == 404
    neu = _einladen(client, w)
    r = client.post(f"{API}/campaigns/join", headers=w["pl"], json={"code": neu})
    assert r.status_code == 200
    ich = next(m for m in r.json()["members"] if m["id"] == w["pl_member"])
    assert ich["role"] == "player" and ich.get("leftAt") is None
    # andere Personen nimmt der alte Code weiter an
    assert client.post(f"{API}/campaigns/join", headers=w["out"], json={"code": alt}).status_code == 200


def test_wer_selbst_geht_kommt_mit_dem_alten_code_zurueck(client, world):
    w = world
    alt = _einladen(client, w)
    assert client.delete(f"{API}/campaigns/{w['cid']}/members/{w['pl_member']}", headers=w["pl"]).status_code == 204
    assert client.post(f"{API}/campaigns/join", headers=w["pl"], json={"code": alt}).status_code == 200


# ---------------------------------------------------------------- Geheim mitgebracht, als noch niemand sonst da war
def test_geheime_mitgebrachte_welt_bleibt_geheim(client, world, dbs):
    from app.models import Entry

    w = world
    client.put(f"{API}/campaigns/{w['cid']}/members/me/character", headers=w["pl"], json=_char())
    feind = {"id": str(uuid.uuid4()), "version": 1, "type": "npc", "name": "Bruder Kael",
             "summary": "Sucht Tharvok.", "secret": True}
    ort = {"id": str(uuid.uuid4()), "version": 1, "type": "location", "name": "Nebelhain",
           "summary": "Heimatdorf im Moor.", "secret": False}
    client.post(f"{API}/campaigns/{w['cid']}/members/me/world", headers=w["pl"], json={"entries": [feind, ort]})
    for p in client.get(f"{API}/campaigns/{w['cid']}/character-proposals", headers=w["gm"]).json():
        assert p["hiddenFromMemberIds"] == []  # außer Ben ist niemand da
        assert client.patch(f"{API}/proposals/{p['id']}", headers=w["gm"],
                            json={"decision": "accepted"}).json()["decision"] == "accepted"
    dbs.expire_all()
    eintraege = {e.name: e for e in dbs.scalars(select(Entry).where(Entry.campaign_id == w["cid"]))}
    assert not eintraege["Bruder Kael"].allgemein_bekannt and eintraege["Nebelhain"].allgemein_bekannt
    # Nachzüglerin: sieht den Ort, den Feind nicht
    assert client.post(f"{API}/campaigns/join", headers=w["out"], json={"code": _einladen(client, w)}).status_code == 200
    namen = lambda h: {e["name"] for e in client.get(f"{API}/campaigns/{w['cid']}/entries", headers=h).json()}  # noqa: E731
    assert "Nebelhain" in namen(w["out"]) and "Bruder Kael" not in namen(w["out"])
    assert "Bruder Kael" in namen(w["pl"]) and "Bruder Kael" in namen(w["gm"])
    # die SL legt selbst fest, wer es weiß: dann gilt ihre Entscheidung
    eid = eintraege["Bruder Kael"].id
    r = client.patch(f"{API}/entries/{eid}", headers=w["gm"], json={"visibility": "public", "hiddenFromMemberIds": []})
    assert r.status_code == 200
    dbs.expire_all()
    assert dbs.get(Entry, eid).allgemein_bekannt and "Bruder Kael" in namen(w["out"])


# ---------------------------------------------------------------- Word-Dokumente
def _docx(xml: str) -> bytes:
    puffer = io.BytesIO()
    with zipfile.ZipFile(puffer, "w", zipfile.ZIP_DEFLATED) as z:
        z.writestr("word/document.xml", xml)
    return puffer.getvalue()


W = 'xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"'


def test_docx_im_eigenen_prozess_ohne_doctype(client, monkeypatch):
    from app import errors, unterlagen

    gut = _docx(f'<?xml version="1.0"?><w:document {W}><w:body><w:p><w:r><w:t>Die Küste von Moorbach.</w:t></w:r>'
                f'</w:p><w:p><w:r><w:br w:type="page"/><w:t>Zweite Seite.</w:t></w:r></w:p></w:body></w:document>')
    x = unterlagen.auslesen("docx", gut)
    assert [a["text"] for a in x.abschnitte] == ["Die Küste von Moorbach.", "Zweite Seite."] and x.seiten == 2
    boese = _docx('<?xml version="1.0"?><!DOCTYPE d [<!ENTITY a "aaaaaaaaaa"><!ENTITY b "&a;&a;&a;&a;&a;&a;&a;&a;">]>'
                  f'<w:document {W}><w:body><w:p><w:r><w:t>&b;&b;&b;</w:t></w:r></w:p></w:body></w:document>')
    with pytest.raises(errors.ApiError) as e:
        unterlagen.auslesen("docx", boese)
    assert e.value.code == "unsupported_document"
    nur_doctype = _docx(f'<?xml version="1.0"?><!DOCTYPE d><w:document {W}><w:body/></w:document>')
    with pytest.raises(errors.ApiError):
        unterlagen.auslesen("docx", nur_doctype)
    # zu groß laut Verzeichnis: abgelehnt, ohne dass ein Prozess startet
    monkeypatch.setattr(unterlagen, "MAX_DOCX_XML", 50)
    monkeypatch.setattr(unterlagen, "_pdf_prozess", lambda *a: 1 / 0)
    with pytest.raises(errors.ApiError):
        unterlagen.auslesen("docx", gut)
    # im Serverprozess wird das XML nicht mehr gelesen
    quelle = (unterlagen.Path(unterlagen.__file__)).read_text(encoding="utf-8")
    assert "fromstring" not in quelle and "ElementTree" not in quelle


# ---------------------------------------------------------------- Umzugsarchiv
def test_umzug_jede_datei_nur_einmal_und_groesse_vor_dem_lesen(tmp_path):
    from app import umzug

    pfad = tmp_path / "a.zip"
    with zipfile.ZipFile(pfad, "w") as z:
        z.writestr("unterlagen/eins.txt", "x" * 1000)
    with zipfile.ZipFile(pfad) as z:
        verwendet: set[str] = set()
        assert umzug._lesen(z, "unterlagen/eins.txt", 5000, verwendet) == b"x" * 1000
        with pytest.raises(umzug.ImportFehler) as e:
            umzug._lesen(z, "unterlagen/eins.txt", 5000, verwendet)  # zweiter Verweis auf dieselbe Datei
        assert e.value.schluessel == "import_unsafe"
        assert umzug._lesen(z, "unterlagen/eins.txt", 999) is None  # zu groß: gar nicht erst gelesen
        assert umzug._lesen(z, "fehlt.txt") is None and umzug._lesen(z, "../boese") is None
        assert umzug._lesen(z, "unterlagen/eins.txt") == b"x" * 1000  # Bilder dürfen mehrfach verwiesen sein


# ---------------------------------------------------------------- nichts Langsames im Ereignis-Takt
def _im_takt() -> bool:
    try:
        asyncio.get_running_loop()
        return True
    except RuntimeError:
        return False


def test_zahlungsmeldung_und_bilder_laufen_im_eigenen_faden(client, world, dbs, monkeypatch):
    from app import bilder, zahlung

    gesehen = {}
    monkeypatch.setattr(zahlung, "bereit", lambda db: ["paypal", "stripe"])

    def pruefen(db, kopf, ereignis):
        gesehen["paypal"] = _im_takt()
        return True

    monkeypatch.setattr(zahlung, "paypal_signatur_gueltig", pruefen)
    monkeypatch.setattr(zahlung, "paypal_meldung", lambda db, e: gesehen.__setitem__("buchung", _im_takt()))
    assert client.post("/zahlung/paypal", json={"id": "WH-1"}).status_code == 200
    assert gesehen == {"paypal": False, "buchung": False}
    monkeypatch.setattr(zahlung, "paypal_signatur_gueltig", lambda db, kopf, e: False)
    assert client.post("/zahlung/paypal", json={"id": "WH-2"}).status_code == 400
    monkeypatch.setattr(zahlung, "stripe_signatur_gueltig", lambda roh, kopf, geheimnis: True)
    monkeypatch.setattr(zahlung, "stripe_meldung", lambda db, e: gesehen.__setitem__("stripe", _im_takt()))
    assert client.post("/zahlung/stripe", json={"id": "evt_1"}).status_code == 200 and gesehen["stripe"] is False
    echt = bilder.cover_speichern
    monkeypatch.setattr(bilder, "cover_speichern", lambda *a: gesehen.__setitem__("bild", _im_takt()) or echt(*a))
    from tests.test_umzug_048 import _png  # noqa: PLC0415

    r = client.put(f"{API}/campaigns/{world['cid']}/cover-image", headers={**world["gm"], "Content-Type": "image/png"},
                   content=_png())
    assert r.status_code == 200 and gesehen["bild"] is False


# ---------------------------------------------------------------- Passwort und Code: begrenzt, auch angemeldet
def test_passwort_aendern_und_konto_loeschen_sind_begrenzt(client, world):
    from app.routers.auth import LOGIN_JE_NAME

    w = world
    for _ in range(LOGIN_JE_NAME):
        r = client.put(f"{API}/me/password", headers=w["pl"], json={"currentPassword": "falsch", "newPassword": "x"})
        assert r.status_code == 401
    r = client.put(f"{API}/me/password", headers=w["pl"],
                   json={"currentPassword": "geheim123", "newPassword": "Ein-langes-Passwort-73"})
    assert r.status_code == 429  # auch das richtige Passwort hilft jetzt nicht
    r = client.request("DELETE", f"{API}/me", headers=w["pl"], json={"password": "geheim123"})
    assert r.status_code == 429  # derselbe Zähler
    # ein anderes Konto ist nicht betroffen, und ein richtiger Versuch zählt nicht
    for _ in range(LOGIN_JE_NAME + 2):
        r = client.put(f"{API}/me/password", headers=w["out"],
                       json={"currentPassword": "geheim123", "newPassword": "geheim123"})
        assert r.status_code != 429


def test_faktor_ausschalten_und_einrichten_sind_begrenzt(client, dbs, admin):  # noqa: F811
    from app.verwaltung.router import SPERRE_VERSUCHE
    from tests.test_server_04143 import _chef, _faktor_an

    for _ in range(SPERRE_VERSUCHE):
        r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": admin, "password": "falsch"})
        assert "Das Passwort stimmt nicht." in r.text
    r = client.post("/verwaltung/zweiter-faktor/beginnen", data={"csrf": admin, "password": "falsch"})
    assert "Zu viele Fehlversuche" in r.text
    from app.begrenzung import ZAEHLER

    ZAEHLER.vergessen()
    _faktor_an(client, dbs, _chef(dbs).id)
    for _ in range(SPERRE_VERSUCHE):
        r = client.post("/verwaltung/zweiter-faktor/aus", data={"csrf": admin, "password": "falsch", "code": "000000"})
        assert "Passwort oder Code stimmt nicht." in r.text
    r = client.post("/verwaltung/zweiter-faktor/aus", data={"csrf": admin, "password": "falsch", "code": "000000"})
    assert "Zu viele Fehlversuche" in r.text
    from app.verwaltung import zweiter_faktor

    dbs.expire_all()
    assert zweiter_faktor.aktiv(dbs, _chef(dbs).id)


def test_einrichtungscode_in_der_adresse_wird_gezaehlt(client, dbs):
    from app.verwaltung.router import SPERRE_VERSUCHE

    for _ in range(SPERRE_VERSUCHE):
        r = client.get("/verwaltung/einrichtung", params={"code": "FALS-CHER"})
        assert r.status_code == 200 and "Zu viele Fehlversuche" not in r.text
    r = client.get("/verwaltung/einrichtung", params={"code": "FALS-CHER"})
    assert "Zu viele Fehlversuche" in r.text
    assert "Zu viele" not in client.get("/verwaltung/einrichtung").text  # ohne Code zählt nichts


# ---------------------------------------------------------------- Kleineres
def test_export_nennt_spielenden_nur_titel_veroeffentlichter_runden(client, world, dbs):
    from app.models import Attendee, GameSession

    w = world
    from tests.test_step2a import neue_session

    sid = neue_session(client, w)["id"]
    dbs.get(GameSession, sid).title = "Der Verrat des Vogts"
    dbs.commit()
    assert dbs.scalar(select(Attendee).where(Attendee.session_id == sid, Attendee.member_id == w["pl_member"]))
    titel = lambda h: {x["sessionId"]: x["title"] for x in  # noqa: E731
                       client.get(f"{API}/me/export", headers=h).json()["sessionsAttended"]}
    assert titel(w["pl"])[sid] is None and titel(w["gm"])[sid] == "Der Verrat des Vogts"
    dbs.get(GameSession, sid).state = "published"
    dbs.commit()
    assert titel(w["pl"])[sid] == "Der Verrat des Vogts"


def test_email_zaehler_vor_der_auskunft(client, world, dbs, monkeypatch):
    from app.models import User

    w = world
    dbs.scalar(select(User).where(User.username == "anna")).email = "anna@example.org"
    dbs.commit()
    for _ in range(5):
        r = client.put(f"{API}/me/email", headers=w["pl"], json={"email": "anna@example.org"})
        assert r.status_code == 409 and r.json()["code"] == "email_taken"
    r = client.put(f"{API}/me/email", headers=w["pl"], json={"email": "frei@example.org"})
    assert r.status_code == 429  # nach fünf Fragen gibt es keine Auskunft mehr, auch nicht zu anderen Adressen
    assert client.put(f"{API}/me/email", headers=w["pl"], json={"email": "anna@example.org"}).status_code == 429


def test_gemeldete_audiolaenge_ist_gekappt(client, dbs, monkeypatch):
    from types import SimpleNamespace

    from app import abrechnung

    auftrag = SimpleNamespace(id="j1", upload_id="u1")
    kosten = SimpleNamespace(zustand="laeuft", art="transcribe", menge=3600)
    dateien = SimpleNamespace(files=[SimpleNamespace(size_bytes=30_000_000)])  # ≈ 40 000 s bei kleinster Bitrate
    monkeypatch.setattr(dbs, "get", lambda modell, kennung: kosten if modell is abrechnung.KostenAuftrag else dateien)
    abrechnung.menge_nachtragen(dbs, auftrag, 3700)
    assert kosten.menge == 3700  # etwas mehr als gemessen: zählt
    abrechnung.menge_nachtragen(dbs, auftrag, 10 ** 9)
    assert kosten.menge == 40_000  # nie mehr, als in der Datei stecken kann
    kosten.menge = 3600
    dateien.files = [SimpleNamespace(size_bytes=1000)]
    abrechnung.menge_nachtragen(dbs, auftrag, 10 ** 9)
    assert kosten.menge == int(3600 * 1.1) + 60  # ohne brauchbare Größe: gemessen plus Toleranz
    abrechnung.menge_nachtragen(dbs, auftrag, 100)
    assert kosten.menge == int(3600 * 1.1) + 60  # nie weniger als bisher
