"""Server 0.4.102: Windows-Reste (Port auch über IPv6, ffmpeg-Wächter, Sicherung mit Passwort, zweiter Faktor unter
dem Windows-Datenschutz)."""
import errno
import io
import os
import socket
import stat
import struct
import time
import unicodedata

import pytest


# ---------------------------------------------------------------- Anschluss
def _ipv6_da() -> bool:
    if not socket.has_ipv6:
        return False
    try:
        s = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
        s.bind(("::1", 0))
        s.close()
        return True
    except OSError:
        return False


@pytest.mark.skipif(not _ipv6_da(), reason="ohne IPv6")
def test_einzelplatz_belegt_auch_ipv6_loopback():
    from app import cli

    anschluesse = cli.einzelplatz_anschluesse(0)
    try:
        assert len(anschluesse) == 2
        v4, v6 = anschluesse
        assert v6.family == socket.AF_INET6 and v6.getsockname()[0] == "::1"
        assert v6.getsockname()[1] == v4.getsockname()[1]
        assert v6.getsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY) == 1
    finally:
        for s in anschluesse:
            s.close()


@pytest.mark.skipif(not _ipv6_da(), reason="ohne IPv6")
def test_fremder_lauscher_auf_ipv6_verhindert_den_start():
    from app import cli

    fremd = socket.socket(socket.AF_INET6, socket.SOCK_STREAM)
    fremd.setsockopt(socket.IPPROTO_IPV6, socket.IPV6_V6ONLY, 1)
    fremd.bind(("::1", 0))
    fremd.listen()
    port = fremd.getsockname()[1]
    try:
        with pytest.raises(OSError):
            cli.einzelplatz_anschluesse(port)
        # der IPv4-Anschluss wurde wieder freigegeben
        probe = cli.einzelplatz_anschluss(port)
        probe.close()
    finally:
        fremd.close()


def test_ohne_ipv6_reicht_ipv4(monkeypatch):
    from app import cli

    echt = cli.einzelplatz_anschluss

    def ohne_v6(port, familie=None, adresse="0.0.0.0"):
        if familie == socket.AF_INET6:
            raise OSError(errno.EADDRNOTAVAIL, "nicht verfügbar")
        return echt(port, familie, adresse)

    monkeypatch.setattr(cli, "einzelplatz_anschluss", ohne_v6)
    monkeypatch.setattr(socket, "has_ipv6", True)
    anschluesse = cli.einzelplatz_anschluesse(0)
    try:
        assert len(anschluesse) == 1 and anschluesse[0].family == socket.AF_INET
    finally:
        anschluesse[0].close()


def test_adresse_ohne_heimnetz_ist_127_0_0_1(monkeypatch):
    from app import einzelplatz

    monkeypatch.setattr(einzelplatz, "heimnetz_adresse", lambda: None)
    monkeypatch.setattr(einzelplatz, "port", lambda: 8000)
    assert einzelplatz.oeffentliche_url() == "http://127.0.0.1:8000"


# ---------------------------------------------------------------- ffmpeg-Wächter
def test_haengendes_ffmpeg_wird_beendet(tmp_path, monkeypatch):
    import wave

    from app import audio

    skript = tmp_path / "ffmpeg-haengt"
    skript.write_text("#!/bin/sh\nexec sleep 60\n")
    skript.chmod(skript.stat().st_mode | stat.S_IXUSR)
    monkeypatch.setattr(audio, "_ffmpeg", lambda: str(skript))
    monkeypatch.setattr(audio, "_zeitgrenze", lambda datei: 0.5)
    quelle = tmp_path / "a.ogg"
    quelle.write_bytes(b"x" * 100)
    t0 = time.monotonic()
    with wave.open(str(tmp_path / "z.wav"), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(audio.SAMPLE_RATE)
        with pytest.raises(audio.AudioFehler) as fehler:
            audio._dekodieren_in(quelle, w, 60)
    assert fehler.value.code == "audio_timeout"
    assert time.monotonic() - t0 < 10  # nicht erst nach 60 s


# ---------------------------------------------------------------- Sicherung mit Passwort
def test_kopf_mit_zu_wenig_speicher_je_faden_ist_formatfehler():
    from app.sicherung_passwort import MAGIE, _KOPF, FormatFehler, entschluesseln

    kopf = MAGIE + _KOPF.pack(os.urandom(16), 1, 16, 8, os.urandom(7))  # 16 KiB bei 8 Fäden: argon2 lehnt ab
    with pytest.raises(FormatFehler):
        entschluesseln(io.BytesIO(kopf + struct.pack(">I", 32) + b"\0" * 32), io.BytesIO(), "geheimnis123")


def test_schluessel_fehler_von_argon2_wird_formatfehler(monkeypatch):
    from app import sicherung_passwort as sp

    with pytest.raises(sp.FormatFehler):
        sp._schluessel("geheimnis123", b"\0" * 16, 1, 7, 1)  # unter dem Mindestspeicher von argon2


def test_passwort_wird_normalisiert():
    from app.sicherung_passwort import entschluesseln, verschluesseln

    zerlegt = unicodedata.normalize("NFD", "Café-Würfel")
    zusammen = unicodedata.normalize("NFC", "Café-Würfel")
    assert zerlegt != zusammen
    v = io.BytesIO()
    verschluesseln(io.BytesIO(b"inhalt"), v, zerlegt, zeit=1, speicher=64, parallel=1)
    aus = io.BytesIO()
    entschluesseln(io.BytesIO(v.getvalue()), aus, zusammen)
    assert aus.getvalue() == b"inhalt"


def test_alte_sicherung_ohne_normalisierung_bleibt_lesbar(monkeypatch):
    from app import sicherung_passwort as sp

    zerlegt = unicodedata.normalize("NFD", "Café-Würfel")
    echt = sp._schluessel
    monkeypatch.setattr(sp, "_schluessel", lambda *a, **k: echt(*a, normalisieren=False))  # wie 0.4.100/0.4.101
    v = io.BytesIO()
    sp.verschluesseln(io.BytesIO(b"x" * (sp.BLOCK + 5)), v, zerlegt, zeit=1, speicher=64, parallel=1)
    monkeypatch.undo()
    aus = io.BytesIO()
    sp.entschluesseln(io.BytesIO(v.getvalue()), aus, zerlegt)
    assert aus.getvalue() == b"x" * (sp.BLOCK + 5)
    with pytest.raises(sp.PasswortFehler):
        sp.entschluesseln(io.BytesIO(v.getvalue()), io.BytesIO(), unicodedata.normalize("NFC", "Café-Würfel"))


def test_argon2_und_cryptography_sind_direkte_abhaengigkeiten():
    import tomllib
    from pathlib import Path

    with open(Path(__file__).resolve().parents[1] / "pyproject.toml", "rb") as f:
        namen = {d.split(">")[0].split("[")[0].strip() for d in tomllib.load(f)["project"]["dependencies"]}
    assert {"argon2-cffi", "cryptography"} <= namen


# ---------------------------------------------------------------- Zweiter Faktor unter Windows
@pytest.fixture
def windows_datenschutz(monkeypatch):
    from app import dpapi

    monkeypatch.setattr(dpapi, "verfuegbar", lambda: True)
    monkeypatch.setattr(dpapi, "schuetzen", lambda b: b"W" + b[::-1])
    monkeypatch.setattr(dpapi, "oeffnen", lambda b: b[1:][::-1] if b[:1] == b"W" else (_ for _ in ()).throw(OSError()))
    return dpapi


def test_totp_geheimnis_geschuetzt(dbs, windows_datenschutz):
    import json

    from app.verwaltung import zweiter_faktor as zf

    geheim = zf.beginnen(dbs, "u1")
    neu = json.loads(zf.meta_lesen(dbs, "verwaltung.totp_neu.u1"))
    assert neu["geheim"].startswith("dpapi:") and geheim not in neu["geheim"]
    assert zf.angefangen(dbs, "u1") == geheim
    jetzt = time.time()
    assert zf.bestaetigen(dbs, "u1", zf.code(geheim, int(jetzt // 30)))
    roh = zf.meta_lesen(dbs, "verwaltung.totp.u1")
    assert geheim not in roh and json.loads(roh)["geheim"].startswith("dpapi:")
    spaeter = jetzt + 60
    assert zf.pruefen(dbs, "u1", zf.code(geheim, int(spaeter // 30)), jetzt=spaeter)
    assert json.loads(zf.meta_lesen(dbs, "verwaltung.totp.u1"))["geheim"].startswith("dpapi:")


def test_alter_klartext_wird_beim_verbrauchen_geschuetzt(dbs, windows_datenschutz):
    import base64
    import json
    import secrets

    from app.verwaltung import zweiter_faktor as zf

    geheim = base64.b32encode(secrets.token_bytes(20)).decode().rstrip("=")
    zf.meta_schreiben(dbs, "verwaltung.totp.u2", json.dumps({"geheim": geheim, "zuletzt": 0, "codes": [], "seit": 0}))
    jetzt = time.time()
    assert zf.pruefen(dbs, "u2", zf.code(geheim, int(jetzt // 30)), jetzt=jetzt)
    gespeichert = json.loads(zf.meta_lesen(dbs, "verwaltung.totp.u2"))["geheim"]
    assert gespeichert.startswith("dpapi:") and gespeichert != geheim


def test_fremder_pc_kein_code_gilt(dbs, windows_datenschutz):
    import json

    from app.verwaltung import zweiter_faktor as zf

    zf.meta_schreiben(dbs, "verwaltung.totp.u3", json.dumps({"geheim": "dpapi:" + "QUJD", "zuletzt": 0, "codes": []}))
    assert zf.aktiv(dbs, "u3")
    assert not zf.pruefen(dbs, "u3", "123456")
