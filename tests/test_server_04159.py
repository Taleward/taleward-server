"""Server 0.4.159: Schnittstelle 0.4.34 – Spuren des Bots verschlüsselt (S65, S65.1–S65.4) und S64.1."""
import base64
import hashlib
import json
import re
from datetime import timedelta
from pathlib import Path

import pytest

from app.db import utcnow
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_server_04155 import (  # noqa: F401 (Fixture)
    BEN_ID,
    GAST_ID,
    GILDE,
    SL_ID,
    _discord,
    _zustimmen_gast,
    bot,
)
from tests.test_server_04157 import _fall, anbieter  # noqa: F401 (Fixture)
from tests.test_step2a import API
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)

VEKTOREN = Path(__file__).parent / "vektoren"


# ---------------------------------------------------------------- Hilfen: Spur schreiben wie der Bot
def verschluesseln(oeffentlich_roh: bytes, sid: str, aid: str, klartext: bytes, abschnitt: int = 1024,
                   key_id: bytes | None = None) -> bytes:
    """Schreibt TWSE1 wie der Bot (Bot-Repo src/aufnahme/verschluesselung.ts)."""
    import os

    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PublicKey
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM

    from app import spuren

    kid = key_id or hashlib.sha256(oeffentlich_roh).digest()
    s, a = sid.encode("ascii"), aid.encode("ascii")
    inhalt = os.urandom(32)
    enc, versiegelt = spuren.hpke_versiegeln(X25519PublicKey.from_public_bytes(oeffentlich_roh), inhalt,
                                             b"TWSE1" + kid + s + a)
    aus = [b"TWSE1", kid, enc, versiegelt, abschnitt.to_bytes(4, "big"), bytes([len(s)]), s, bytes([len(a)]), a]
    aes = AESGCM(inhalt)
    stuecke = [klartext[i:i + abschnitt] for i in range(0, len(klartext), abschnitt)]
    if not stuecke or len(stuecke[-1]) == abschnitt:
        stuecke.append(b"")  # der letzte Abschnitt darf leer sein
    for nr, klar in enumerate(stuecke):
        letzter = nr == len(stuecke) - 1
        aad = b"TWSE1" + s + a + nr.to_bytes(8, "big") + (b"\x01" if letzter else b"\x00") + len(klar).to_bytes(4, "big")
        geheim = aes.encrypt(b"\x00" * 4 + nr.to_bytes(8, "big"), klar, aad)
        aus += [len(geheim).to_bytes(4, "big"), geheim]
    return b"".join(aus)


def _abschnitte(datei: bytes) -> tuple[bytes, list[bytes]]:
    """Kopf und Abschnitte (je samt Längenfeld) einer TWSE1-Datei."""
    p = 5 + 32 + 32 + 48 + 4
    p += 1 + datei[p]
    p += 1 + datei[p]
    kopf, rest = datei[:p], []
    while p < len(datei):
        n = int.from_bytes(datei[p:p + 4], "big")
        rest.append(datei[p:p + 4 + n])
        p += 4 + n
    return kopf, rest


def _oeffnen_mit(tmp_path, monkeypatch, datei: bytes, privat_roh: bytes, sid: str, aid: str, teil=1000,
                 teilgroesse=700):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app import spuren

    privat = X25519PrivateKey.from_private_bytes(privat_roh)
    monkeypatch.setattr(spuren, "_schluessel_zu", lambda db, k: privat if k == hashlib.sha256(
        spuren._roh_oeffentlich(privat)).digest() else None)
    eingang = tmp_path / "ein"
    eingang.mkdir(exist_ok=True)
    pfade = []
    for i in range(0, len(datei), teil):  # in Teile wie beim Upload, Grenzen mitten in Abschnitten
        p = eingang / f"{i // teil:05d}.part"
        p.write_bytes(datei[i:i + teil])
        pfade.append(p)
    ziel = tmp_path / "offen"
    groesse = spuren.oeffnen(None, pfade, ziel, teilgroesse, sid, aid)
    teile = sorted(ziel.iterdir())
    assert all(len(t.read_bytes()) == teilgroesse for t in teile[:-1])
    return groesse, b"".join(t.read_bytes() for t in teile)


# ---------------------------------------------------------------- S65.2: HPKE aus der Bibliothek, RFC 9180
# RFC 9180 / CFRG test-vectors.json: mode 0, kem_id 0x20 (X25519), kdf_id 1 (HKDF-SHA256), aead_id 2 (AES-256-GCM)
RFC = {
    "info": "4f6465206f6e2061204772656369616e2055726e",
    "skRm": "497b4502664cfea5d5af0b39934dac72242a74f8480451e1aee7d6a53320333d",
    "pkRm": "430f4b9859665145a6b1ba274024487bd66f03a2dd577d7753c68d7d7d00c00c",
    "enc": "6c93e09869df3402d7bf231bf540fadd35cd56be14f97178f0954db94b7fc256",
    "aad": "436f756e742d30",
    "ct": "e5d84cd531cfb583096e7cfa9641bd3079cf3a91cda813c52deb5f512be9931980a41de125a925cdad859d5b7a",
    "pt": "4265617574792069732074727574682c20747275746820626561757479",
}


def test_hpke_nach_rfc_9180():
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app import spuren

    h = bytes.fromhex
    privat = X25519PrivateKey.from_private_bytes(h(RFC["skRm"]))
    assert spuren._roh_oeffentlich(privat) == h(RFC["pkRm"])
    assert spuren.hpke_oeffnen(privat, h(RFC["enc"]), h(RFC["ct"]), h(RFC["aad"]), info=h(RFC["info"])) == h(RFC["pt"])
    for aad, info in ((b"Count-1", h(RFC["info"])), (h(RFC["aad"]), b"anders")):
        with pytest.raises(Exception):
            spuren.hpke_oeffnen(privat, h(RFC["enc"]), h(RFC["ct"]), aad, info=info)
    # Hin und zurück mit der Suite der Spuren
    enc, ct = spuren.hpke_versiegeln(privat.public_key(), b"x" * 32, b"aad")
    assert spuren.hpke_oeffnen(privat, enc, ct, b"aad") == b"x" * 32


def test_bibliothek_mit_fester_fassung_und_pruefsumme():
    lock = (Path(__file__).parent.parent / "uv.lock").read_text(encoding="utf-8")
    block = lock.split('name = "cryptography"\n', 1)[1].split("\n[[package]]", 1)[0]
    assert re.search(r'^version = "\d+\.\d+\.\d+"$', block, re.M) and "sha256:" in block


# ---------------------------------------------------------------- Gemeinsamer Testvektor aus dem Bot-Repo
def test_gemeinsamer_testvektor_des_bots(tmp_path, monkeypatch):
    v = json.loads((VEKTOREN / "spur-twse1.json").read_text(encoding="utf-8"))
    datei = (VEKTOREN / "spur-twse1.twse").read_bytes()
    klar = (VEKTOREN / "spur-twse1-klartext.opus").read_bytes()
    privat = (VEKTOREN / "spur-twse1-schluessel.bin").read_bytes()
    assert hashlib.sha256(datei).hexdigest() == v["dateiSha256"]
    assert hashlib.sha256(klar).hexdigest() == v["klartextSha256"]
    assert privat.hex() == v["privateKeyHex"]
    groesse, offen = _oeffnen_mit(tmp_path, monkeypatch, datei, privat, v["sessionId"], v["attendeeId"])
    assert groesse == v["klartextBytes"] and offen == klar
    assert datei[5:37].hex() == v["keyId"]


@pytest.mark.parametrize("art", ["byte", "gekuerzt", "angehaengt", "vertauscht", "fremde_runde", "fremde_person",
                                 "fremder_schluessel", "kennung", "abschnittsgroesse", "ohne_letzten"])
def test_veraenderte_spuren_gehen_nicht_auf(tmp_path, monkeypatch, art):
    from app import spuren

    v = json.loads((VEKTOREN / "spur-twse1.json").read_text(encoding="utf-8"))
    datei = (VEKTOREN / "spur-twse1.twse").read_bytes()
    privat = (VEKTOREN / "spur-twse1-schluessel.bin").read_bytes()
    kopf, ab = _abschnitte(datei)
    assert len(ab) == v["abschnitte"]
    sid, aid = v["sessionId"], v["attendeeId"]
    if art == "byte":
        b = bytearray(datei)
        b[len(kopf) + 100] ^= 1
        datei = bytes(b)
    elif art == "gekuerzt":
        datei = datei[:-1]
    elif art == "angehaengt":
        datei = datei + b"\x00"
    elif art == "vertauscht":
        datei = kopf + ab[1] + ab[0] + b"".join(ab[2:])
    elif art == "fremde_runde":
        sid = "ses_andere"
    elif art == "fremde_person":
        aid = "att_andere"
    elif art == "fremder_schluessel":
        privat = bytes(31) + b"\x09"
    elif art == "kennung":
        datei = b"TWSE2" + datei[5:]
    elif art == "abschnittsgroesse":
        datei = datei[:117] + (16).to_bytes(4, "big") + datei[121:]
    elif art == "ohne_letzten":
        datei = kopf + b"".join(ab[:-1])
    with pytest.raises(spuren.Ungueltig):
        _oeffnen_mit(tmp_path, monkeypatch, datei, privat, sid, aid)
    assert not (tmp_path / "offen").exists()  # S65.1: Zwischenverzeichnis sofort weg


@pytest.mark.parametrize("wert", ["../../etc", "a b", "ä", "", "x" * 65])
def test_kopfwerte_streng(tmp_path, monkeypatch, wert):
    """S65.4: sessionId/attendeeId im Kopf nur ASCII-Kennungen, 1–64 Zeichen – auch wenn sie zur Erwartung passen."""
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app import spuren

    privat = X25519PrivateKey.generate()
    roh = spuren._roh_oeffentlich(privat)
    w = wert.encode("utf-8")
    datei = verschluesseln(roh, "ses1", "att1", b"klang" * 100)
    kopf_ende = 5 + 32 + 32 + 48 + 4
    datei = datei[:kopf_ende] + bytes([len(w)]) + w + datei[kopf_ende + 5:]
    with pytest.raises(spuren.Ungueltig):
        _oeffnen_mit(tmp_path, monkeypatch, datei, spuren._roh_privat(privat), "ses1", "att1")


def test_genaues_vielfaches_und_leere_spur(tmp_path, monkeypatch):
    from cryptography.hazmat.primitives.asymmetric.x25519 import X25519PrivateKey

    from app import spuren

    privat = X25519PrivateKey.generate()
    roh, p = spuren._roh_oeffentlich(privat), spuren._roh_privat(privat)
    klar = bytes(range(256)) * 16  # genau 4 Abschnitte, danach ein leerer letzter
    assert _oeffnen_mit(tmp_path, monkeypatch, verschluesseln(roh, "s", "a", klar), p, "s", "a")[1] == klar
    leer = tmp_path / "leer"
    leer.mkdir()
    with pytest.raises(spuren.Ungueltig):  # nichts zu hören
        _oeffnen_mit(leer, monkeypatch, verschluesseln(roh, "s", "a", b""), p, "s", "a")


# ---------------------------------------------------------------- Schnittstelle 0.4.34
def _runde_mit_schluessel(client, b):
    r = client.post(f"{API}/bot/campaigns/{b['cid']}/sessions",
                    json={"guildId": GILDE, "discordUserId": SL_ID, "startedAt": utcnow().isoformat()}, headers=b["h"])
    assert r.status_code == 201, r.text
    return r.json()


def _zwei_spuren(client, b, dbs):
    from app.models import GameSession

    _discord(dbs, "ben", BEN_ID)
    rd = _runde_mit_schluessel(client, b)
    sid = rd["sessionId"]
    client.put(f"{API}/campaigns/{b['cid']}/recording-consent", json={"granted": True}, headers=b["pl"])
    a = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": BEN_ID}, headers=b["h"]).json()
    _zustimmen_gast(client, b, sid)
    g = client.post(f"{API}/bot/sessions/{sid}/attendees", json={"discordUserId": GAST_ID}, headers=b["h"]).json()
    s = dbs.get(GameSession, sid)
    s.recording_started_at = utcnow() - timedelta(hours=1)
    dbs.commit()
    return rd, a["attendeeId"], g["attendeeId"]


def _hochladen(client, b, sid, dateien: dict[str, bytes]):
    files = [{"fileName": f"{i:02d}-{aid[:8]}.twse", "sizeBytes": len(d),
              "mimeType": "application/vnd.taleward.spur-verschluesselt", "trackAttendeeId": aid}
             for i, (aid, d) in enumerate(dateien.items())]
    r = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": files}, headers=b["h"])
    assert r.status_code == 201, r.text
    up = r.json()
    groesse = up["chunkSizeBytes"]
    for f, d in zip(up["files"], dateien.values()):
        for i in range(f["chunkCount"]):
            r = client.put(f"{API}/uploads/{up['uploadId']}/files/{f['fileId']}/chunks/{i}",
                           content=d[i * groesse:(i + 1) * groesse],
                           headers={**b["h"], "Content-Type": "application/octet-stream"})
            assert r.status_code == 204, r.text
    return up


def test_track_key_nur_fuer_den_bot(client, bot, dbs):  # noqa: F811
    from app import dpapi, spuren
    from app.models import ServerMeta

    rd = _runde_mit_schluessel(client, bot)
    tk = rd["trackKey"]
    assert set(tk) == {"keyId", "publicKey"}
    assert re.fullmatch(r"[0-9a-f]{64}", tk["keyId"]) and re.fullmatch(r"[A-Za-z0-9+/]{43}=", tk["publicKey"])
    assert hashlib.sha256(base64.b64decode(tk["publicKey"])).hexdigest() == tk["keyId"]
    info = client.get(f"{API}/info").text
    assert tk["publicKey"] not in info and tk["keyId"] not in info
    assert dpapi.geheim(spuren.META)
    roh = json.loads(dbs.get(ServerMeta, spuren.META).value)["aktuell"]  # (ohne Windows-Schutz im Test)
    assert roh not in info and roh not in client.get(f"{API}/sessions/{rd['sessionId']}", headers=bot["gm"]).text
    # derselbe Schlüssel bei der nächsten Runde
    client.post(f"{API}/bot/sessions/{rd['sessionId']}/events", json={"type": "discarded",
                                                                     "at": utcnow().isoformat()}, headers=bot["h"])
    assert _runde_mit_schluessel(client, bot)["trackKey"] == tk


def test_unverschluesselt_mit_bot_schluessel_und_typ_ohne(client, bot, dbs):  # noqa: F811
    rd, ben, _gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    r = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": [
        {"fileName": "ben.ogg", "sizeBytes": 10, "mimeType": "audio/ogg", "trackAttendeeId": ben}]}, headers=bot["h"])
    assert r.status_code == 400 and r.json()["code"] == "track_unencrypted"
    r = client.post(f"{API}/sessions/{sid}/uploads", json={"source": "discord", "files": [
        {"fileName": "ben.ogg", "sizeBytes": 10, "mimeType": "application/vnd.taleward.spur-verschluesselt",
         "trackAttendeeId": ben}]}, headers=bot["h"])
    assert r.status_code == 400 and r.json()["code"] == "track_unencrypted"  # Name ohne .twse
    # ohne Bot-Schlüssel ist der Typ nicht erlaubt
    client.post(f"{API}/bot/sessions/{sid}/events", json={"type": "discarded", "at": utcnow().isoformat()},
                headers=bot["h"])
    s = client.post(f"{API}/campaigns/{bot['cid']}/sessions", json={"playedAt": "2026-09-20T18:00:00Z", "attendees": [
        {"memberId": bot["gm_member"], "consent": True, "consentSource": "on_site"}]}, headers=bot["gm"])
    assert s.status_code == 201, s.text
    s = s.json()
    r = client.post(f"{API}/sessions/{s['id']}/uploads", json={"source": "table", "files": [
        {"fileName": "a.twse", "sizeBytes": 10, "mimeType": "application/vnd.taleward.spur-verschluesselt"}]},
        headers=bot["gm"])
    assert r.status_code == 400


def test_ungueltige_spur_verworfen_upload_bleibt_offen(client, bot, dbs, monkeypatch):  # noqa: F811
    from app import abrechnung, storage
    from app.models import Attendee, Upload, UploadFile

    rd, ben, gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    pub = base64.b64decode(rd["trackKey"]["publicKey"])
    ben_klar = bytes(range(256)) * 30 + b"ende"
    ben_datei = verschluesseln(pub, sid, ben, ben_klar)
    gast_datei = bytearray(verschluesseln(pub, sid, gast, b"gast" * 500))
    gast_datei[-3] ^= 1  # verändert
    from app.config import get_settings

    monkeypatch.setattr(get_settings(), "chunk_size_bytes", 2000)  # mehrere Teile, Grenzen mitten in Abschnitten
    up = _hochladen(client, bot, sid, {ben: ben_datei, gast: bytes(gast_datei)})
    gemessen = []
    monkeypatch.setattr(abrechnung, "aufnahme_messen", lambda u: gemessen.append(1) or 1800.0)
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=bot["h"])
    assert r.status_code == 400, r.text
    assert r.json()["code"] == "track_invalid" and r.json()["details"]["attendeeIds"] == [gast]
    assert not gemessen
    dbs.expire_all()
    u = dbs.get(Upload, up["uploadId"])
    assert u.state == "open" and [f.track_attendee_id for f in u.files] == [ben]
    assert dbs.get(Attendee, gast) is None
    assert u.files[0].mime_type == "application/vnd.taleward.spur-verschluesselt"  # noch nichts geöffnet
    arten = [e["kind"] for e in client.get(f"{API}/campaigns/{bot['cid']}/discord/log", headers=bot["gm"]).json()[
        "entries"]]
    assert arten[0] == "track_discarded"
    assert not list(storage.upload_dir(up["uploadId"]).glob("*.offen"))
    # nächster Abschluss läuft mit der übrigen Spur weiter
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=bot["h"])
    assert r.status_code == 202, r.text
    dbs.expire_all()
    f = dbs.get(UploadFile, u.files[0].id)
    assert f.mime_type == "audio/ogg" and f.file_name.endswith(".ogg") and f.size_bytes == len(ben_klar)
    offen = b"".join(storage.chunk_path(up["uploadId"], f.id, i).read_bytes() for i in range(f.chunk_count))
    assert offen == ben_klar and f.chunk_count > 1
    assert sorted(p.name for p in storage.upload_dir(up["uploadId"]).iterdir()) == [f.id]


def test_alle_ungueltig_runde_ohne_aufnahme(client, bot, dbs, monkeypatch):  # noqa: F811
    from app.models import GameSession, Upload

    rd, ben, gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    pub = base64.b64decode(rd["trackKey"]["publicKey"])
    fremd = verschluesseln(pub, "andere-runde", ben, b"x" * 3000)  # umgehängt
    falsch = verschluesseln(pub, sid, ben, b"y" * 3000)  # Spur von Ben als Gast ausgegeben
    up = _hochladen(client, bot, sid, {ben: fremd, gast: falsch})
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=bot["h"])
    assert r.status_code == 400 and sorted(r.json()["details"]["attendeeIds"]) == sorted([ben, gast])
    dbs.expire_all()
    assert dbs.get(Upload, up["uploadId"]).state == "aborted"
    assert dbs.get(GameSession, sid).state == "no_recording"
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=bot["h"])
    assert r.status_code in (404, 409)


def test_sl_kann_verschluesselten_upload_nicht_ungeoeffnet_abschliessen(client, bot, dbs, monkeypatch):  # noqa: F811
    """Schließt die SL den Upload des Bots ab, öffnet der Server die Spuren genauso."""
    from app import abrechnung
    from app.models import UploadFile

    rd, ben, gast = _zwei_spuren(client, bot, dbs)
    sid = rd["sessionId"]
    pub = base64.b64decode(rd["trackKey"]["publicKey"])
    up = _hochladen(client, bot, sid, {ben: verschluesseln(pub, sid, ben, b"b" * 3000),
                                       gast: verschluesseln(pub, sid, gast, b"g" * 3000)})
    monkeypatch.setattr(abrechnung, "aufnahme_messen", lambda u: 600.0)
    r = client.post(f"{API}/uploads/{up['uploadId']}/complete", headers=bot["gm"])
    assert r.status_code == 202, r.text
    dbs.expire_all()
    assert {f.mime_type for f in dbs.query(UploadFile).all()} == {"audio/ogg"}


# ---------------------------------------------------------------- S65.3: Erneuern, alte Teile, Wartung
def test_erneuern_nur_mit_frischem_faktor_und_alte_spuren_bleiben_lesbar(client, bot, dbs, admin, tmp_path,
                                                                         monkeypatch):  # noqa: F811
    from app import spuren
    from app.models import GameSession

    rd = _runde_mit_schluessel(client, bot)
    alt = rd["trackKey"]
    seite = client.get("/verwaltung/discord").text
    assert "Schlüssel für die Spuren" in seite and alt["keyId"][:12] in seite
    # ohne frischen Faktor: nichts
    client.post("/verwaltung/discord/spurschluessel", data={"csrf": admin}, follow_redirects=False)
    dbs.expire_all()
    assert spuren.track_key(dbs) == alt
    _faktor_an(client, dbs, _chef(dbs).id)
    r = client.post("/verwaltung/discord/spurschluessel", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    neu = spuren.track_key(dbs)
    assert neu["keyId"] != alt["keyId"]
    # Spur mit dem alten Schlüssel geht noch auf (offene Bot-Runde)
    datei = verschluesseln(base64.b64decode(alt["publicKey"]), "s", "a", b"z" * 2000)
    kennung = bytes.fromhex(alt["keyId"])
    assert spuren._schluessel_zu(dbs, kennung) is not None
    assert spuren.stand(dbs)["alt"] == 1
    # 31 Stunden später: Wartung löscht den alten privaten Teil
    s = dbs.get(GameSession, rd["sessionId"])
    monkeypatch.setattr(spuren, "utcnow", lambda: utcnow() + timedelta(hours=31))
    assert spuren.wartung(dbs) == 1
    dbs.commit()
    dbs.expire_all()
    assert spuren._schluessel_zu(dbs, kennung) is None
    assert spuren._schluessel_zu(dbs, bytes.fromhex(neu["keyId"])) is not None
    assert s is not None and datei[5:37] == kennung


def test_erneuern_ohne_offene_runde_behaelt_nichts(dbs):
    from app import spuren

    erst = spuren.track_key(dbs)
    spuren.erneuern(dbs)
    dbs.commit()
    assert spuren.stand(dbs)["alt"] == 0
    assert spuren._schluessel_zu(dbs, bytes.fromhex(erst["keyId"])) is None


# ---------------------------------------------------------------- S64.1
def test_erstattung_bei_5xx_bleibt_gesperrt(dbs, anbieter):  # noqa: F811
    from app import erstattung
    from app.models import Bezahlvorgang

    g = _fall(dbs)
    anbieter.stripe = 503
    e = erstattung.erstatten(dbs, g, "test")
    assert "pi_neuneuneu1" in e.fehlgeschlagen
    v = next(v for v in dbs.query(Bezahlvorgang).all() if v.zahlung_ref == "pi_neuneuneu1")
    assert v.erstattung_offen_at is not None  # vielleicht doch angekommen
    anbieter.stripe = 200
    e = erstattung.erstatten(dbs, g, "test")
    assert "pi_neuneuneu1" in e.wartet  # nicht noch einmal ohne „Erneut anstoßen“


def test_erstattung_bei_zeitueberschreitung_bleibt_gesperrt(dbs, anbieter, monkeypatch):  # noqa: F811
    import httpx

    from app import erstattung
    from app.models import Bezahlvorgang

    g = _fall(dbs)

    def zu_langsam(*a, **k):
        raise httpx.ReadTimeout("zu langsam")

    monkeypatch.setattr(erstattung, "_beim_anbieter", zu_langsam)
    erstattung.erstatten(dbs, g, "test")
    v = next(v for v in dbs.query(Bezahlvorgang).all() if v.zahlung_ref == "pi_neuneuneu1")
    assert v.erstattung_offen_at is not None
    assert erstattung._sicher_abgelehnt(ValueError("ref"))
    assert not erstattung._sicher_abgelehnt(json.JSONDecodeError("x", "", 0))
