"""Server 0.4.100: Sicherung mit Passwort zum Mitnehmen (Argon2id, AES-256-GCM in Blöcken)."""
import io

import pytest


# ---------------------------------------------------------------- Sicherung mit Passwort
_SCHNELL = {"zeit": 1, "speicher": 8, "parallel": 1}


def _verschluesselt(daten: bytes, passwort: str = "richtig-123") -> bytes:
    from app.sicherung_passwort import verschluesseln

    aus = io.BytesIO()
    verschluesseln(io.BytesIO(daten), aus, passwort, **_SCHNELL)
    return aus.getvalue()


def _entschluesselt(daten: bytes, passwort: str = "richtig-123") -> bytes:
    from app.sicherung_passwort import entschluesseln

    aus = io.BytesIO()
    entschluesseln(io.BytesIO(daten), aus, passwort)
    return aus.getvalue()


@pytest.mark.parametrize("groesse", [0, 1, 1024 * 1024, 1024 * 1024 + 5, 3 * 1024 * 1024])
def test_hin_und_zurueck(groesse):
    import os

    daten = os.urandom(groesse)
    v = _verschluesselt(daten)
    assert daten[:64] not in v or groesse == 0
    assert _entschluesselt(v) == daten


def test_falsches_passwort_und_manipulation():
    import struct

    from app.sicherung_passwort import BLOCK, MAGIE, FormatFehler, PasswortFehler, _KOPF

    daten = bytes(range(256)) * (BLOCK // 256) * 2 + b"rest"  # drei Blöcke
    v = _verschluesselt(daten)
    with pytest.raises(PasswortFehler):
        _entschluesselt(v, "falsch-123")
    kopf = len(MAGIE) + _KOPF.size
    bloecke, pos = [], kopf
    while pos < len(v):
        (n,) = struct.unpack(">I", v[pos:pos + 4])
        bloecke.append(v[pos:pos + 4 + n])
        pos += 4 + n
    assert len(bloecke) == 3
    for kaputt in (
        v[:kopf] + bloecke[0] + bloecke[1],  # letzter Block fehlt
        v[:kopf] + bloecke[0] + bloecke[2],  # mittlerer Block fehlt
        v[:kopf] + bloecke[1] + bloecke[0] + bloecke[2],  # vertauscht
        v + b"x",  # angehängt
        v[:kopf] + bloecke[0] + bloecke[1] + bloecke[2][:-1],  # abgeschnitten
        v[:-1] + bytes([v[-1] ^ 1]),  # ein Bit verändert
        v[:len(MAGIE)] + bytes([v[len(MAGIE)] ^ 1]) + v[len(MAGIE) + 1:],  # Salz verändert
    ):
        with pytest.raises(PasswortFehler):
            _entschluesselt(kaputt)
    with pytest.raises(FormatFehler):
        _entschluesselt(b"PK\x03\x04" + v[4:])
    zu_teuer = v[:len(MAGIE) + 16] + struct.pack(">I", 1000) + v[len(MAGIE) + 20:]
    with pytest.raises(FormatFehler):
        _entschluesselt(zu_teuer)


def test_kurzes_passwort_abgelehnt(tmp_path):
    from app.sicherung_passwort import exportieren, verschluesseln

    with pytest.raises(ValueError):
        verschluesseln(io.BytesIO(b"x"), io.BytesIO(), "kurz")
    with pytest.raises(ValueError):
        exportieren(tmp_path / "s.taleward", "1234567")


def test_standard_ist_angemessen_teuer():
    from app import sicherung_passwort as sp

    assert sp.SPEICHER_KIB >= 64 * 1024 and sp.ZEITKOSTEN >= 3


def test_export_und_import_ueber_die_kommandozeile(client, world, dbs, tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from app import sicherung, sicherung_passwort
    from app.cli import app
    from app.models import Campaign

    monkeypatch.setattr(sicherung_passwort, "ZEITKOSTEN", 1)
    monkeypatch.setattr(sicherung_passwort, "SPEICHER_KIB", 8)
    monkeypatch.setattr(sicherung_passwort, "PARALLEL", 1)
    dbs.add(Campaign(title="Die Mühle am Nebelbach"))
    dbs.commit()
    ziel = tmp_path / "stick" / "meine-runden"
    ziel.parent.mkdir()
    r = CliRunner().invoke(app, ["sicherung-export", str(ziel)], input="richtig-123\n")
    assert r.exit_code == 0, r.output
    datei = ziel.with_name("meine-runden.taleward")
    assert f"Sicherung gespeichert: {datei}" in r.output and datei.is_file()
    assert not list(datei.parent.glob("*.part")) and not any("export" in e.name for e in sicherung.liste())
    assert b"chronik.db" not in datei.read_bytes()  # nichts im Klartext

    dbs.query(Campaign).filter(Campaign.title == "Die Mühle am Nebelbach").delete()
    dbs.commit()
    dbs.close()
    r = CliRunner().invoke(app, ["sicherung-import", str(datei), "--ja"], input="falsch-123\n")
    assert r.exit_code == 2 and "Passwort" in r.output
    r = CliRunner().invoke(app, ["sicherung-import", str(tmp_path / "gibt-es-nicht.taleward"), "--ja"],
                           input="richtig-123\n")
    assert r.exit_code == 1
    r = CliRunner().invoke(app, ["sicherung-import", str(datei), "--ja"], input="richtig-123\n")
    assert r.exit_code == 0, r.output
    assert "Bisheriger Stand gesichert" in r.output and "Wiederhergestellt" in r.output
    from app.db import session_factory

    with session_factory()() as neu:
        assert neu.query(Campaign).filter(Campaign.title == "Die Mühle am Nebelbach").count() == 1
