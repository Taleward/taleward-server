"""Server 0.4.112: Taleward Desktop zeigt App und Verwaltung im Rahmen seiner Programmoberfläche.

Das Programm nennt dem Server beim Start die genaue Herkunft der Oberfläche (EINZELPLATZ_HUELLE). Nur dann, nur im
Einzelplatz und nur für genau diese Herkunft erlauben App und Verwaltung das Einbetten (frame-ancestors, ohne
X-Frame-Options). Sonst bleibt es bei frame-ancestors 'none' und DENY.
"""
import pytest

from tests.test_server_0495 import _starten, _umgebung, _von

HUELLE = "http://localhost:51234"


@pytest.fixture()
def mit_huelle(tmp_path, monkeypatch):
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.setenv("EINZELPLATZ_HUELLE", HUELLE)
    yield from _starten()


@pytest.fixture()
def ohne_huelle(tmp_path, monkeypatch):
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.delenv("EINZELPLATZ_HUELLE", raising=False)
    yield from _starten()


def _csp(r) -> str:
    return r.headers.get("content-security-policy", "")


@pytest.mark.parametrize("pfad", ["/verwaltung/anmelden", "/verwaltung/einrichtung", "/app/"])
def test_einbetten_nur_durch_die_programmoberflaeche(mit_huelle, pfad):
    r = mit_huelle.get(pfad, follow_redirects=False)
    assert f"frame-ancestors {HUELLE}" in _csp(r), (pfad, _csp(r))
    assert "frame-ancestors 'none'" not in _csp(r)
    assert "x-frame-options" not in r.headers


@pytest.mark.parametrize("pfad", ["/verwaltung/anmelden", "/app/"])
def test_ohne_angabe_bleibt_es_verboten(ohne_huelle, pfad):
    r = ohne_huelle.get(pfad, follow_redirects=False)
    assert "frame-ancestors 'none'" in _csp(r) and r.headers["x-frame-options"] == "DENY"


def test_andere_seiten_bleiben_nicht_einbettbar(mit_huelle):
    for pfad in ("/datenschutz", "/einladung/xyz"):
        r = mit_huelle.get(pfad, follow_redirects=False)
        if _csp(r):
            assert "frame-ancestors 'none'" in _csp(r) and r.headers.get("x-frame-options") == "DENY", pfad


def test_verwaltung_aus_dem_heimnetz_bleibt_404(mit_huelle):
    r = _von(mit_huelle, "192.168.178.50", "GET", "/verwaltung/anmelden")
    assert r.status_code == 404 and HUELLE not in _csp(r)


@pytest.mark.parametrize("wert,erwartet", [
    ("http://localhost:51234", "http://localhost:51234"),
    (" http://localhost:51234 ", "http://localhost:51234"),
    ("http://localhost:8000", ""),           # der Server selbst
    ("http://localhost:80", ""), ("http://localhost:99999", ""), ("http://localhost:0123", ""),
    ("http://localhost:*", ""), ("http://localhost", ""), ("https://localhost:51234", ""),
    ("http://127.0.0.1:51234", ""), ("http://localhost:51234/", ""), ("http://localhost:51234 https://boese.example", ""),
    ("http://localhost.boese.example:51234", ""), ("'none'", ""), ("*", ""), ("", ""),
])
def test_nur_genau_diese_form(wert, erwartet):
    from app.config import huelle_pruefen

    assert huelle_pruefen(wert, 8000) == erwartet


def test_ausserhalb_des_einzelplatzes_nie(monkeypatch, tmp_path):
    from app import config

    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    monkeypatch.delenv("TALEWARD_EINZELPLATZ", raising=False)
    monkeypatch.setenv("EINZELPLATZ_HUELLE", HUELLE)
    config.get_settings.cache_clear()
    try:
        assert config.get_settings().einzelplatz_huelle == ""
    finally:
        config.get_settings.cache_clear()


def test_einbettbar_aendert_die_vorlage_nicht():
    from app.verwaltung.router import SICHERHEITS_KOPFZEILEN, WEBAPP_KOPFZEILEN, einbettbar

    vorher = (dict(SICHERHEITS_KOPFZEILEN), dict(WEBAPP_KOPFZEILEN))
    einbettbar(SICHERHEITS_KOPFZEILEN, HUELLE)
    einbettbar(WEBAPP_KOPFZEILEN, HUELLE)
    assert (SICHERHEITS_KOPFZEILEN, WEBAPP_KOPFZEILEN) == vorher
    assert einbettbar(WEBAPP_KOPFZEILEN, "") is WEBAPP_KOPFZEILEN
