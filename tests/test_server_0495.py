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


# ---------------------------------------------------------------- E1: Host-Kopfzeile
@pytest.mark.parametrize("host,ok", [
    ("localhost:8000", True), ("127.0.0.1:8000", True), ("[::1]:8000", True), (f"{HEIMNETZ}:8000", True),
    ("LOCALHOST:8000", True),
    ("boese.example:8000", False), ("localhost", False), ("localhost:9000", False), ("192.168.178.99:8000", False),
    ("8.8.8.8:8000", False), ("taleward.local:8000", False), ("user@localhost:8000", False), ("", False),
])
def test_host_kopfzeile(ep, host, ok):
    r = ep.get("/api/v1/info", headers={"Host": host})
    assert (r.status_code == 200) is ok, (host, r.status_code)
    if not ok:
        assert r.status_code == 400 and r.content == b""


def test_neue_heimnetz_adresse_wird_nachgesehen(ep, monkeypatch):
    from app import einzelplatz

    monkeypatch.setattr(einzelplatz, "_ermitteln", lambda: (frozenset({"10.0.0.7"}), "10.0.0.7"))
    einzelplatz._adressen["zeit"] = -1e9 + 0  # Zwischenspeicher abgelaufen
    assert ep.get("/api/v1/info", headers={"Host": "10.0.0.7:8000"}).status_code == 200
    assert ep.get("/api/v1/info").json()["publicUrl"] == "http://10.0.0.7:8000"


# ---------------------------------------------------------------- E4/E2: Absender
def test_absender_aus_dem_internet_bekommt_404(ep):
    for pfad in ("/api/v1/info", "/app/", "/verwaltung/anmelden", "/einladung/ABCD"):
        assert _von(ep, "203.0.113.5", "GET", pfad).status_code == 404
    for adresse in ("2001:db8::5", "8.8.8.8", "100.64.1.1", "198.18.0.1", "192.0.0.8", "::ffff:8.8.8.8"):
        assert _von(ep, adresse, "GET", "/api/v1/info").status_code == 404, adresse
    for adresse in ("10.1.2.3", "172.20.0.4", "169.254.1.1", "fd00::5", "::ffff:192.168.1.9"):
        assert _von(ep, adresse, "GET", "/api/v1/info").status_code == 200, adresse


def test_heimnetz_nur_app_und_oeffentliche_seiten(ep):
    handy = "192.168.178.50"
    r = _von(ep, handy, "GET", "/api/v1/info")
    assert r.status_code == 200
    CONTRACT.check("GET", "/api/v1/info", 200, r.headers.get("content-type"), r.content)
    assert r.json()["publicUrl"] == f"http://{HEIMNETZ}:8000"
    for pfad in ("/verwaltung", "/verwaltung/anmelden", "/verwaltung/einrichtung", "/verwaltung/updates",
                 "/guthaben", "/worker/v1/config", "/api/v1/docs/../../verwaltung", "/docs"):
        assert _von(ep, handy, "GET", pfad).status_code == 404, pfad
    assert _von(ep, handy, "POST", "/worker/v1/pair", json={}).status_code == 404
    assert _von(ep, handy, "POST", "/verwaltung/einzelplatz/konto", json={}).status_code == 404
    # Proxy-Kopfzeilen machen aus dem Handy keinen PC
    r = _von(ep, handy, "GET", "/verwaltung/anmelden", headers={"X-Forwarded-For": "127.0.0.1", "Forwarded": "for=127.0.0.1"})
    assert r.status_code == 404
    assert _von(ep, handy, "GET", "/favicon.ico").status_code == 200
    assert _von(ep, handy, "GET", "/verwaltung/static/verwaltung.css").status_code == 200
    r = _von(ep, handy, "GET", "/", follow_redirects=False)
    assert r.status_code == 307 and r.headers["location"] == "/app/"


def test_datenschutzseite_mit_aktueller_adresse(ep):
    from app.db import session_factory
    from app.einstellungen import angaben, meta_schreiben, speichern

    with session_factory()() as db:
        meta_schreiben(db, "datenschutz.text", "Wir speichern wenig.")
        speichern(db, privacy_policy_url="http://localhost:8000/datenschutz")
        db.commit()
        assert angaben(db).privacy_policy_url == f"http://{HEIMNETZ}:8000/datenschutz"
        speichern(db, privacy_policy_url="https://verein.example/datenschutz")
        db.commit()
        assert angaben(db).privacy_policy_url == "https://verein.example/datenschutz"
    assert _von(ep, "192.168.178.50", "GET", "/datenschutz").status_code == 200


def test_vom_pc_gibt_es_die_verwaltung(ep):
    assert ep.get("/verwaltung/anmelden").status_code == 200
    assert ep.get("/", follow_redirects=False).headers["location"] == "/app/"


def test_pfade_aus_dem_netz():
    from app.einzelplatz import pfad_aus_dem_netz

    assert pfad_aus_dem_netz("/api/v1/campaigns") and pfad_aus_dem_netz("/app") and pfad_aus_dem_netz("/app/x.js")
    assert pfad_aus_dem_netz("/einladung/ABCD") and pfad_aus_dem_netz("/konto-loeschen")
    for pfad in ("/verwaltung", "/verwaltung/", "/worker/v1/claim", "/guthaben/", "/apps", "/api/v2/x", "/docs",
                 "/verwaltung/einzelplatz/konto", "/einladung"):
        assert not pfad_aus_dem_netz(pfad), pfad


# ---------------------------------------------------------------- E3: Startschlüssel und erstes Konto
def test_startschluessel_verlaesst_die_umgebung(ep):
    assert "TALEWARD_STARTSCHLUESSEL" not in os.environ


def test_erstes_konto_mit_startschluessel(ep):
    assert _konto(ep, schluessel="falsch-" + "x" * 40).status_code == 404
    r = _konto(ep)
    assert r.status_code == 201, r.text
    assert r.json() == {"anmeldename": "anna.muller"}
    # nur einmal
    assert _konto(ep).status_code == 404
    login = ep.post("/api/v1/auth/login", json={"username": "anna.muller", "password": "Lindwurm-Kessel-7"})
    assert login.status_code == 200
    from app.db import session_factory
    from app.einrichtung import braucht_einrichtung
    from app.einstellungen import angaben

    with session_factory()() as db:
        assert not braucht_einrichtung(db)
        assert angaben(db).server_name == "Taleward – Anna Müller" and angaben(db).server_operator == "Anna Müller"


def test_erstes_konto_nicht_aus_dem_browser(ep):
    r = ep.post("/verwaltung/einzelplatz/konto", headers={"Origin": "http://localhost:8000"},
                json={"schluessel": SCHLUESSEL, "name": "Anna", "passwort": "Lindwurm-Kessel-7"})
    assert r.status_code == 404
    assert _konto(ep).status_code == 201  # der Schlüssel ist dabei nicht verbraucht


def test_erstes_konto_pruefungen(ep):
    assert _konto(ep, passwort="kurz").json()["code"] == "password_too_short"
    assert _konto(ep, passwort="passwort").json()["code"] in ("password_too_short", "password_too_weak")
    assert _konto(ep, passwort="anna.muller").json()["code"] == "password_too_weak"
    assert _konto(ep, name="   ").json()["code"] == "name_missing"
    assert _konto(ep, anmeldename="admin").json()["code"] == "username_invalid"
    assert _konto(ep, anmeldename="a b").json()["code"] == "username_invalid"
    assert _konto(ep, betriebsart="irgendwas").json()["code"] == "validation_error"
    r = _konto(ep, anmeldename="Spielleitung", betriebsart="cloud")
    assert r.status_code == 201 and r.json()["anmeldename"] == "spielleitung"
    from app.db import session_factory
    from app.einrichtung import betriebsart

    with session_factory()() as db:
        assert betriebsart(db) == "cloud"


def test_schluessel_verfaellt(ep, monkeypatch):
    from app import einzelplatz

    monkeypatch.setattr(einzelplatz.time, "monotonic", lambda: einzelplatz._schluessel["bis"] + 1)
    assert _konto(ep).status_code == 404


def test_kein_einrichtungscode_im_einzelplatz(ep):
    from app import einrichtung
    from app.db import session_factory
    from app.einstellungen import meta_schreiben

    with session_factory()() as db:
        meta_schreiben(db, "einrichtung.code", "ABCD-EFGH")
        db.commit()
        assert not einrichtung.code_pruefen(db, "ABCD-EFGH")
    r = ep.post("/verwaltung/einrichtung", data={"code": "ABCD-EFGH", "username": "eva", "display_name": "Eva",
                                                  "password": "Lindwurm-Kessel-7", "password2": "Lindwurm-Kessel-7"})
    assert r.status_code == 400


def test_startschluessel_aus_datei(tmp_path, monkeypatch):
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.delenv("TALEWARD_STARTSCHLUESSEL", raising=False)
    datei = tmp_path / "start.txt"
    datei.write_text(SCHLUESSEL + "\n", encoding="utf-8")
    monkeypatch.setenv("TALEWARD_STARTSCHLUESSEL_DATEI", str(datei))
    for c in _starten():
        assert not datei.exists() and "TALEWARD_STARTSCHLUESSEL_DATEI" not in os.environ
        assert _konto(c).status_code == 201


def test_kurzer_schluessel_gilt_nicht(tmp_path, monkeypatch):
    _umgebung(monkeypatch, tmp_path)
    monkeypatch.setenv("TALEWARD_STARTSCHLUESSEL", "zu-kurz")
    for c in _starten():
        r = c.post("/verwaltung/einzelplatz/konto", json={"schluessel": "zu-kurz", "name": "Anna",
                                                          "passwort": "Lindwurm-Kessel-7"})
        assert r.status_code == 404


def test_ohne_einzelplatz_gibt_es_den_startweg_nicht(client):
    r = client.post("/verwaltung/einzelplatz/konto", json={"schluessel": SCHLUESSEL, "name": "Anna",
                                                           "passwort": "Lindwurm-Kessel-7"})
    assert r.status_code in (404, 405)
    assert client.get("/api/v1/info").json().get("publicUrl") is None


def test_anmeldename_aus_namen():
    from app.einzelplatz import anmeldename_aus

    assert anmeldename_aus("Anna Müller") == "anna.muller"
    assert anmeldename_aus("  Ö  ") == "spielleitung"
    assert anmeldename_aus("Admin") == "spielleitung"
    assert anmeldename_aus("Jörg-Peter O'Neill") == "jorg.peter.o.neill"


# ---------------------------------------------------------------- Einstellungen, Worker, Updates
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


def test_eingebauter_worker_startet_von_selbst(ep, monkeypatch):
    from app.db import session_factory
    from app.verwaltung import lokaler_worker

    gestartet = []
    knecht = lokaler_worker.LokalerKnecht()
    monkeypatch.setattr(knecht, "starten", lambda db, modus: gestartet.append(modus))
    monkeypatch.setattr(lokaler_worker, "ki_verfuegbar", lambda: True)
    with session_factory()() as db:
        knecht.autostart(db)
        assert gestartet == ["echt"]
        monkeypatch.setattr(lokaler_worker, "ki_verfuegbar", lambda: False)
        knecht.autostart(db)
        assert gestartet == ["echt"]  # ohne KI-Pakete nicht
