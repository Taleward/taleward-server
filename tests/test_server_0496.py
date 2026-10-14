"""Server 0.4.96: Einzelplatz – der Worker des Programms koppelt sich über eine Schlüsseldatei."""
import pytest

from tests.test_server_0495 import SCHLUESSEL, _konto, _starten, _umgebung, _von


@pytest.fixture()
def ep(tmp_path, monkeypatch):
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.setenv("TALEWARD_STARTSCHLUESSEL", SCHLUESSEL)
    monkeypatch.setenv("EINZELPLATZ_WORKER_DATEI", str(tmp_path / "programm" / "worker.key"))
    yield from _starten()


def _token(tmp_path) -> str:
    return (tmp_path / "programm" / "worker.key").read_text(encoding="utf-8").strip()


def test_schluesseldatei_bei_jedem_start(ep, tmp_path):
    token = _token(tmp_path)
    assert token.startswith("wk.")
    r = ep.get("/worker/v1/config", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200
    # aus dem Heimnetz gibt es die Worker-Schnittstelle nicht
    assert _von(ep, "192.168.178.50", "GET", "/worker/v1/config",
                headers={"Authorization": f"Bearer {token}"}).status_code == 404
    from app.db import session_factory
    from app.verwaltung.lokaler_worker import programm_worker_koppeln

    with session_factory()() as db:  # wie ein Neustart: neuer Schlüssel, der alte gilt nicht mehr
        programm_worker_koppeln(db)
    neu = _token(tmp_path)
    assert neu != token
    assert ep.get("/worker/v1/config", headers={"Authorization": f"Bearer {token}"}).status_code == 401
    assert ep.get("/worker/v1/config", headers={"Authorization": f"Bearer {neu}"}).status_code == 200


def test_server_startet_keinen_eigenen_worker(ep, monkeypatch):
    from app.db import session_factory
    from app.verwaltung import lokaler_worker

    gestartet = []
    knecht = lokaler_worker.LokalerKnecht()
    monkeypatch.setattr(knecht, "starten", lambda db, modus: gestartet.append(modus))
    monkeypatch.setattr(lokaler_worker, "ki_verfuegbar", lambda: True)
    with session_factory()() as db:
        knecht.autostart(db)
    assert gestartet == []


def test_verwaltung_zeigt_den_worker_des_programms(ep):
    from tests.test_verwaltung import csrf_von

    assert _konto(ep).status_code == 201
    assert ep.post("/verwaltung/anmelden", data={"username": "anna.muller", "password": "Lindwurm-Kessel-7"},
                   follow_redirects=False).status_code == 303
    seite = ep.get("/verwaltung/transkription")
    assert seite.status_code == 200 and "Worker des Taleward-Programms" in seite.text
    assert "/verwaltung/worker/lokal/start" not in seite.text
    csrf = csrf_von(ep.get("/verwaltung/").text)
    assert ep.post("/verwaltung/worker/lokal/start", data={"csrf": csrf, "modus": "echt"}).status_code == 404


def test_ohne_datei_bleibt_es_beim_eigenen_worker(tmp_path, monkeypatch):
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.delenv("EINZELPLATZ_WORKER_DATEI", raising=False)
    for _c in _starten():
        from app.einzelplatz import programm_worker

        assert not programm_worker()
        assert not (tmp_path / "programm").exists()


def test_stoppdatei_beendet_geordnet(tmp_path, monkeypatch):
    import time

    from app import einzelplatz

    _umgebung(monkeypatch, tmp_path)
    datei = tmp_path / "programm" / "server.stopp"
    datei.parent.mkdir(parents=True)
    datei.write_text("alt", encoding="utf-8")  # vom letzten Mal – zählt nicht
    monkeypatch.setenv("EINZELPLATZ_STOPP_DATEI", str(datei))
    signale = []
    monkeypatch.setattr(einzelplatz, "_stopp_signal", lambda: signale.append(1))
    from app import config

    config.get_settings.cache_clear()
    ende = einzelplatz.stopp_waechter_starten(takt=0.05)
    try:
        assert ende is not None and not datei.exists()
        time.sleep(0.2)
        assert signale == []
        datei.write_text("stopp", encoding="utf-8")
        for _ in range(100):
            if signale:
                break
            time.sleep(0.02)
        assert signale == [1] and not datei.exists()
    finally:
        ende.set()
        config.get_settings.cache_clear()


def test_ohne_einzelplatz_kein_stoppwaechter(tmp_path, monkeypatch):
    from app import config, einzelplatz

    monkeypatch.delenv("TALEWARD_EINZELPLATZ", raising=False)
    monkeypatch.setenv("EINZELPLATZ_STOPP_DATEI", str(tmp_path / "x"))
    monkeypatch.setenv("DATA_DIR", str(tmp_path))
    config.get_settings.cache_clear()
    try:
        assert einzelplatz.stopp_waechter_starten() is None
    finally:
        config.get_settings.cache_clear()
