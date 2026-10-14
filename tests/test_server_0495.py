"""Server 0.4.95: Betriebsart „Einzelplatz“ (Windows-Plan, Schritt 1)."""
import os
import secrets

import pytest
from fastapi.testclient import TestClient

from tests.contract import CONTRACT

HEIMNETZ = "192.168.178.24"
SCHLUESSEL = secrets.token_urlsafe(32)


def _umgebung(monkeypatch, tmp_path):
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.setenv("JWT_SECRET", "test-secret-" + "x" * 40)
    monkeypatch.setenv("CORS_ORIGINS", "http://localhost:5173,http://localhost")
    for name in ("MAINTENANCE_INTERVAL_SECONDS", "SUMMARIZER_INTERVAL_SECONDS", "EXTERNAL_INTERVAL_SECONDS"):
        monkeypatch.setenv(name, "0")
    monkeypatch.setenv("LOCAL_WORKER_AUTOSTART", "false")
    for name in ("EXTERNAL_TRANSCRIPTION", "MISTRAL_API_KEY", "DATABASE_URL", "TALEWARD_STARTSCHLUESSEL_DATEI"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("TALEWARD_EINZELPLATZ", "1")
    monkeypatch.setenv("EINZELPLATZ_PORT", "8000")
    monkeypatch.chdir(tmp_path)
    from app import einzelplatz

    einzelplatz.zuruecksetzen()
    monkeypatch.setattr(einzelplatz, "_ermitteln", lambda: (frozenset({HEIMNETZ}), HEIMNETZ))


@pytest.fixture()
def ep(tmp_path, monkeypatch):
    """Einzelplatz-Server; Anfragen kommen standardmäßig vom PC selbst."""
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.setenv("TALEWARD_STARTSCHLUESSEL", SCHLUESSEL)
    yield from _starten()


def _starten():
    from app import config, db
    from app.begrenzung import ZAEHLER
    from app.einstellungen import mindestversion_vergessen

    config.get_settings.cache_clear()
    db.reset_engine()
    mindestversion_vergessen()
    ZAEHLER.vergessen()
    from app.main import create_app

    with TestClient(create_app(), base_url="http://localhost:8000", client=("127.0.0.1", 50000)) as c:
        yield c
    db.reset_engine()
    config.get_settings.cache_clear()
    from app import einzelplatz

    einzelplatz.zuruecksetzen()


def _von(c, adresse, methode, pfad, **kw):
    """Anfrage mit anderer Absenderadresse (wie ein Handy im WLAN oder ein Gerät im Internet)."""
    c2 = TestClient(c.app, base_url=c.base_url, client=(adresse, 40000))
    return c2.request(methode, pfad, **kw)


def _konto(c, **mehr):
    body = {"schluessel": SCHLUESSEL, "name": "Anna Müller", "passwort": "Lindwurm-Kessel-7"} | mehr
    return c.post("/verwaltung/einzelplatz/konto", json=body)


def test_pfade_aus_dem_netz():
    from app.einzelplatz import pfad_aus_dem_netz

    assert pfad_aus_dem_netz("/api/v1/campaigns") and pfad_aus_dem_netz("/app") and pfad_aus_dem_netz("/app/x.js")
    assert pfad_aus_dem_netz("/einladung/ABCD") and pfad_aus_dem_netz("/konto-loeschen")
    for pfad in ("/verwaltung", "/verwaltung/", "/worker/v1/claim", "/guthaben/", "/apps", "/api/v2/x", "/docs",
                 "/verwaltung/einzelplatz/konto", "/einladung"):
        assert not pfad_aus_dem_netz(pfad), pfad


def test_anmeldename_aus_namen():
    from app.einzelplatz import anmeldename_aus

    assert anmeldename_aus("Anna Müller") == "anna.muller"
    assert anmeldename_aus("  Ö  ") == "spielleitung"
    assert anmeldename_aus("Admin") == "spielleitung"
    assert anmeldename_aus("Jörg-Peter O'Neill") == "jorg.peter.o.neill"


def test_einstellungen_im_einzelplatz(tmp_path, monkeypatch):
    from app import config

    monkeypatch.setenv("TALEWARD_EINZELPLATZ", "1")
    monkeypatch.delenv("DATA_DIR", raising=False)
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "lokal"))
    monkeypatch.setenv("WORKER_LLM_URL", "http://0.0.0.0:11434")
    monkeypatch.setenv("FORWARDED_ALLOW_IPS", "*")
    monkeypatch.setenv("EINRICHTUNG_IM_HEIMNETZ", "true")
    monkeypatch.chdir(tmp_path)
    config.get_settings.cache_clear()
    try:
        s = config.get_settings()
        assert s.data_dir == (tmp_path / "lokal" / "Taleward" / "Daten").resolve() and s.data_dir.is_dir()
        assert s.worker_llm_url == "http://127.0.0.1:11434"
        assert s.forwarded_allow_ips == "" and not s.einrichtung_im_heimnetz
        assert s.worker_server_url == "http://127.0.0.1:8000"
    finally:
        config.get_settings.cache_clear()
