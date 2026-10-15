"""Server 0.4.103: Wiederherstellungscodes gelten auch, wenn sich das Geheimnis des zweiten Faktors auf diesem PC nicht
öffnen lässt."""
import json

import pytest


@pytest.fixture
def anderer_pc(monkeypatch):
    from app import dpapi

    monkeypatch.setattr(dpapi, "verfuegbar", lambda: True)
    monkeypatch.setattr(dpapi, "oeffnen", lambda b: (_ for _ in ()).throw(OSError("anderer Nutzer")))


def test_wiederherstellungscode_gilt_auf_anderem_pc(dbs, anderer_pc):
    from app.verwaltung import zweiter_faktor as zf

    zf.meta_schreiben(dbs, "verwaltung.totp.u9", json.dumps(
        {"geheim": "dpapi:QUJD", "zuletzt": 0, "codes": [zf._hash("ABCDE-FGHIJ")], "seit": 0}))
    assert not zf.pruefen(dbs, "u9", "123456")          # Code aus der App: nicht prüfbar
    assert zf.pruefen(dbs, "u9", "abcde-fghij")         # Wiederherstellungscode: gilt
    assert not zf.pruefen(dbs, "u9", "abcde-fghij")     # aber nur einmal
    assert zf.stand(dbs, "u9")["geheim"] == "dpapi:QUJD"  # das Geheimnis bleibt, wie es war


def test_keine_internen_kennungen_in_migrationen():
    import re
    from pathlib import Path

    for datei in (Path(__file__).resolve().parents[1] / "migrations" / "versions").glob("*.py"):
        assert not re.search(r"\((?:S|M|N|L|E)\d{1,2}(?:\.\d)?\)", datei.read_text(encoding="utf-8")), datei.name
