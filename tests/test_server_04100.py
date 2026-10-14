"""Server 0.4.100: Zugänge unter Windows mit dem Windows-Datenschutz ablegen."""
import pytest


# ---------------------------------------------------------------- Windows-Datenschutz (nachgestellt)
@pytest.fixture()
def windows(monkeypatch):
    """Stellt den Windows-Datenschutz nach: nur „dieser PC“ (Kennung DIESER) kann öffnen."""
    from app import dpapi

    def schuetzen(b: bytes) -> bytes:
        return b"DIESER" + bytes(x ^ 0x5A for x in b)

    def oeffnen(b: bytes) -> bytes:
        if not b.startswith(b"DIESER"):
            raise OSError("anderer PC")
        return bytes(x ^ 0x5A for x in b[6:])

    monkeypatch.setattr(dpapi, "verfuegbar", lambda: True)
    monkeypatch.setattr(dpapi, "schuetzen", schuetzen)
    monkeypatch.setattr(dpapi, "oeffnen", oeffnen)
    return dpapi


def test_zugaenge_werden_geschuetzt_abgelegt(dbs, windows):
    from app.einstellungen import meta_lesen, meta_schreiben
    from app.models import ServerMeta

    for k in ("extern.api_key", "llm.api_key", "hf.token", "melden.ntfy_token", "melden.smtp_pass",
              "oidc.google.client_secret", "oidc.apple.private_key"):
        meta_schreiben(dbs, k, "geheim-123")
        assert dbs.get(ServerMeta, k).value.startswith("dpapi:")
        assert "geheim-123" not in dbs.get(ServerMeta, k).value
        assert meta_lesen(dbs, k) == "geheim-123"
    meta_schreiben(dbs, "oidc.google.client_id", "offen")  # keine Geheimnisse bleiben, wie sie sind
    meta_schreiben(dbs, "llm.api_url", "https://beispiel.invalid")
    assert dbs.get(ServerMeta, "oidc.google.client_id").value == "offen"
    assert dbs.get(ServerMeta, "llm.api_url").value == "https://beispiel.invalid"
    meta_schreiben(dbs, "hf.token", "")  # leer = entfernt
    assert meta_lesen(dbs, "hf.token") == ""


def test_fremder_oder_alter_wert(dbs, windows, monkeypatch):
    from app import dpapi
    from app.einstellungen import extern_konfig, meta_lesen
    from app.models import ServerMeta

    dbs.add(ServerMeta(key="hf.token", value="hf_klartext"))  # aus einer älteren Fassung
    dbs.add(ServerMeta(key="extern.api_key", value="dpapi:" + "QU5ERVJFUg=="))  # von einem anderen PC
    dbs.commit()
    assert meta_lesen(dbs, "hf.token") == "hf_klartext"
    assert meta_lesen(dbs, "extern.api_key") is None
    assert not extern_konfig(dbs).api_key  # gilt als nicht eingetragen
    assert dpapi.nachziehen(dbs) == 1
    assert dbs.get(ServerMeta, "hf.token").value.startswith("dpapi:")
    assert meta_lesen(dbs, "hf.token") == "hf_klartext"
    assert dpapi.nachziehen(dbs) == 0


def test_ohne_windows_aendert_sich_nichts(dbs):
    from app import dpapi
    from app.einstellungen import meta_lesen, meta_schreiben
    from app.models import ServerMeta

    assert not dpapi.verfuegbar()
    meta_schreiben(dbs, "hf.token", "hf_x")
    assert dbs.get(ServerMeta, "hf.token").value == "hf_x"
    assert dpapi.nachziehen(dbs) == 0
    dbs.get(ServerMeta, "hf.token").value = "dpapi:QUJD"  # unter Windows geschützt, hier nicht lesbar
    assert meta_lesen(dbs, "hf.token") is None


def test_anmeldegeheimnis(tmp_path, windows):
    from app.config import _geheimnis

    datei = tmp_path / "geheimnis.txt"
    datei.write_text("a" * 40)  # aus einer älteren Fassung
    assert _geheimnis(tmp_path) == "a" * 40
    gespeichert = datei.read_text()
    assert gespeichert.startswith("dpapi:") and "a" * 40 not in gespeichert
    assert _geheimnis(tmp_path) == "a" * 40  # danach weiter dasselbe Geheimnis
    datei.write_text("dpapi:QU5ERVJFUg==")  # von einem anderen PC kopiert: neu erzeugen
    neu = _geheimnis(tmp_path)
    assert len(neu) >= 32 and neu != "a" * 40 and datei.read_text().startswith("dpapi:")


def test_worker_schluessel_nur_geschuetzt_wenn_das_programm_es_kann(dbs, tmp_path, windows):
    from app.verwaltung.lokaler_worker import schluessel_datei_schreiben

    datei = schluessel_datei_schreiben(dbs, tmp_path / "w" / "worker.key")
    assert datei.read_text().startswith("wk.")  # ältere Programme lesen den Schlüssel im Klartext
    datei = schluessel_datei_schreiben(dbs, tmp_path / "w" / "worker.key", geschuetzt=True)
    inhalt = datei.read_text().strip()
    assert inhalt.startswith("dpapi:") and windows.text_oeffnen(inhalt).startswith("wk.")


def test_ohne_funktionierenden_datenschutz_wird_trotzdem_gespeichert(dbs, windows, monkeypatch):
    from app.einstellungen import meta_lesen, meta_schreiben

    monkeypatch.setattr(windows, "schuetzen", lambda b: (_ for _ in ()).throw(OSError("geht nicht")))
    meta_schreiben(dbs, "hf.token", "hf_abc")
    assert meta_lesen(dbs, "hf.token") == "hf_abc"
