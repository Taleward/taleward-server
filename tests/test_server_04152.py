"""Server 0.4.152: gespeicherte Zugänge nur mit frischem Faktor an ein neues Ziel (S57.3), Kopplungscode nur für die
Sitzung, die ihn erzeugt hat (S57.4), Sicherungsordner nie im Daten- oder Programmordner (S60), Teilen-Schlüssel ohne
Überschreiben und ohne Wettlauf (S61), `reset-password` über den gemeinsamen Weg (S55), Datenordner 700."""
import os
import time

import pytest

from app.einstellungen import meta_lesen, meta_schreiben
from tests.test_server_04143 import _chef, _faktor_an
from tests.test_verwaltung import admin  # noqa: F401


def _nicht_frisch(dbs, chef):
    meta_schreiben(dbs, f"verwaltung.faktor_frisch.verwaltung.{chef.id}", "1")
    dbs.commit()


def _bestaetigen(r) -> bool:
    return r.status_code == 303 and r.headers["location"].startswith("/verwaltung/bestaetigen")


# ---------------------------------------------------------------- S57.3
def test_gespeicherter_schluessel_nicht_an_neue_adresse(client, dbs, admin):  # noqa: F811
    chef = _chef(dbs)
    _faktor_an(client, dbs, chef.id)
    meta_schreiben(dbs, "llm.api_key", "sk-gespeichert-1234567890")
    meta_schreiben(dbs, "llm.api_url", "https://api.mistral.ai/v1")
    _nicht_frisch(dbs, chef)
    basis = {"csrf": admin, "art": "api", "api_modell": "m", "lokal_modell": "l", "ausserhalb_eu": "ja"}
    r = client.post("/verwaltung/zusammenfassung", follow_redirects=False,
                    data=basis | {"anbieter": "andere", "api_url": "https://fremd.example/v1"})
    assert _bestaetigen(r)
    assert meta_lesen(dbs, "llm.api_url") == "https://api.mistral.ai/v1"
    # gleiche Adresse, Schlüssel löschen oder neuer Schlüssel: wie bisher (neuer Schlüssel verlangt ohnehin den Faktor)
    r = client.post("/verwaltung/zusammenfassung", follow_redirects=False,
                    data=basis | {"anbieter": "andere", "api_url": "https://fremd.example/v1", "key_loeschen": "ja"})
    assert not _bestaetigen(r)
    r = client.post("/verwaltung/zusammenfassung", follow_redirects=False,
                    data=basis | {"anbieter": "andere", "api_url": "https://fremd.example/v1",
                                  "api_key": "sk-neu-123456789012345"})
    assert _bestaetigen(r)


def test_ntfy_und_smtp_nicht_umleiten(client, dbs, admin):  # noqa: F811
    chef = _chef(dbs)
    _faktor_an(client, dbs, chef.id)
    for k, v in {"ntfy_url": "https://ntfy.sh/verein", "ntfy_token": "tk_gespeichert", "smtp_host": "smtp.verein.example",
                 "smtp_port": "587", "smtp_tls": "starttls", "smtp_pass": "geheim"}.items():
        meta_schreiben(dbs, f"melden.{k}", v)
    _nicht_frisch(dbs, chef)
    gleich = {"csrf": admin, "ntfy_url": "https://ntfy.sh/verein", "smtp_host": "smtp.verein.example",
              "smtp_port": "587", "smtp_tls": "starttls", "stunden": "6", "sprache": "de"}
    for anders in ({"ntfy_url": "https://boese.example/thema"}, {"smtp_host": "smtp.boese.example"},
                   {"smtp_port": "25"}, {"smtp_tls": "aus"}):
        r = client.post("/verwaltung/benachrichtigungen", data=gleich | anders, follow_redirects=False)
        assert _bestaetigen(r), anders
    assert meta_lesen(dbs, "melden.ntfy_url") == "https://ntfy.sh/verein"
    assert meta_lesen(dbs, "melden.smtp_host") == "smtp.verein.example"
    r = client.post("/verwaltung/benachrichtigungen", data=gleich | {"email_an": "chef@verein.example"},
                    follow_redirects=False)
    assert not _bestaetigen(r)  # anderes ändern geht ohne Faktor
    r = client.post("/verwaltung/benachrichtigungen", follow_redirects=False,
                    data=gleich | {"smtp_host": "smtp.neu.example", "zugang_loeschen": "ja"})
    assert not _bestaetigen(r) and meta_lesen(dbs, "melden.smtp_pass") in ("", None)


def test_assistent_leitet_gespeicherten_schluessel_nicht_um(client, dbs, admin):  # noqa: F811
    chef = _chef(dbs)
    _faktor_an(client, dbs, chef.id)
    meta_schreiben(dbs, "llm.api_key", "sk-gespeichert-1234567890")
    meta_schreiben(dbs, "llm.api_url", "https://anderer.example/v1")
    _nicht_frisch(dbs, chef)
    r = client.post("/verwaltung/assistent/zusammenfassung", data={"csrf": admin, "wahl": "api"},
                    headers={"referer": "http://testserver/verwaltung/assistent/zusammenfassung"},
                    follow_redirects=False)
    assert _bestaetigen(r) and meta_lesen(dbs, "llm.api_url") == "https://anderer.example/v1"


# ---------------------------------------------------------------- S57.4
def test_kopplungscode_nur_fuer_die_erzeugende_sitzung(client, dbs, admin):  # noqa: F811
    from app import koppeln
    from tests.faktor_hilfe import sitzung

    code, _bis = koppeln.code_erzeugen(dbs, "andere-sitzung")
    assert koppeln.code_fuer(dbs, "andere-sitzung")[0] == code
    assert koppeln.code_fuer(dbs, "sitzung-b") is None and koppeln.code_fuer(dbs, "-") is None
    assert code not in client.get("/verwaltung/transkription").text
    assert code not in client.get("/verwaltung/assistent/betrieb").text
    eigener, _bis = koppeln.code_erzeugen(dbs, sitzung(client))
    assert eigener in client.get("/verwaltung/transkription").text
    koppeln.code_erzeugen(dbs)  # ohne Sitzung (z. B. Kommandozeile): niemandem in der Verwaltung angezeigt
    assert koppeln.code_fuer(dbs, sitzung(client)) is None


# ---------------------------------------------------------------- S60
def test_sicherungsordner_nicht_im_daten_oder_programmordner(client, dbs, admin, tmp_path_factory):  # noqa: F811
    from app import sicherung
    from app.config import get_settings

    daten = get_settings().data_dir
    innen = daten / "aktualisierung" / "web" / "1" / "app"
    innen.mkdir(parents=True)
    aussen = tmp_path_factory.mktemp("ausserhalb")  # die Tests legen den Datenordner selbst in tmp_path
    verknuepfung = aussen / "harmlos"
    verknuepfung.symlink_to(innen, target_is_directory=True)
    draussen = aussen / "storagebox"
    draussen.mkdir()
    programm = os.path.dirname(sicherung.__file__)
    for ordner in (str(daten), str(innen), str(verknuepfung), programm):
        r = client.post("/verwaltung/sicherung/einstellungen", data={"csrf": admin, "tage": "14", "ordner": ordner})
        assert r.status_code == 400 and "Daten- oder Programmordner" in r.text, ordner
    r = client.post("/verwaltung/sicherung/einstellungen", data={"csrf": admin, "tage": "14", "ordner": str(draussen)},
                    follow_redirects=False)
    assert r.status_code == 303 and meta_lesen(dbs, "sicherung.ordner") == str(draussen)
    # ältere Einstellung im Datenordner: keine Kopie mehr dorthin
    sicherung.erstellen(zusatz_ordner=str(innen))
    assert not list(innen.glob("*.zip"))
    # Ordner ändern nur mit frischem Faktor (mit eingeschaltetem zweitem Faktor)
    chef = _chef(dbs)
    _faktor_an(client, dbs, chef.id)
    _nicht_frisch(dbs, chef)
    r = client.post("/verwaltung/sicherung/einstellungen", data={"csrf": admin, "tage": "14", "ordner": ""},
                    headers={"referer": "http://testserver/verwaltung/"}, follow_redirects=False)
    assert _bestaetigen(r) and meta_lesen(dbs, "sicherung.ordner") == str(draussen)
    r = client.post("/verwaltung/sicherung/einstellungen", follow_redirects=False,
                    data={"csrf": admin, "tage": "30", "ordner": str(draussen)})
    assert not _bestaetigen(r) and meta_lesen(dbs, "sicherung.tage") == "30"  # Tage ändern geht weiter


def test_web_app_liefert_keine_archive(client, dbs, tmp_path_factory, monkeypatch):
    from app import aktualisierung

    tmp_path = tmp_path_factory.mktemp("web")
    (tmp_path / "index.html").write_text("<!doctype html>")
    (tmp_path / "taleward-sicherung-1.zip").write_bytes(b"PK")
    (tmp_path / "app.js").write_text("1")
    monkeypatch.setattr(aktualisierung, "web_ordner", lambda db: tmp_path)
    assert client.get("/app/app.js").status_code == 200
    assert client.get("/app/taleward-sicherung-1.zip").status_code == 404
    assert client.get("/app/TALEWARD.ZIP").status_code == 404


# ---------------------------------------------------------------- S61
def test_signierschluessel_wird_nie_ersetzt(dbs, monkeypatch):
    from app import teilen
    from app.models import ServerMeta

    erster = teilen.signierer(dbs)
    dbs.commit()
    assert teilen.signierer(dbs).id == erster.id
    # gespeicherter Wert lässt sich nicht öffnen → kein neuer Schlüssel, Teilen sagt „gerade nicht“
    dbs.get(ServerMeta, teilen.META_SCHLUESSEL).value = "kaputt"
    dbs.commit()
    with pytest.raises(teilen.NichtLesbar):
        teilen.signierer(dbs)
    assert dbs.get(ServerMeta, teilen.META_SCHLUESSEL).value == "kaputt"
    assert teilen.share_keys(dbs) == []


def test_gleichzeitige_erste_anlage(dbs, monkeypatch):
    from app import teilen
    from app.db import session_factory

    andere = session_factory()()
    zuerst = teilen.signierer(andere)  # die andere Anfrage war schneller
    andere.commit()
    echt = teilen._laden
    aufrufe = []

    def langsam(db):  # diese Anfrage hat vorher „nichts da“ gesehen
        aufrufe.append(1)
        return None if len(aufrufe) == 1 else echt(db)

    monkeypatch.setattr(teilen, "_laden", langsam)
    assert teilen.signierer(dbs).id == zuerst.id
    andere.close()


def test_gleichzeitiger_erster_mitgliedsschluessel(client, world, dbs, monkeypatch):
    from app import teilen
    from app.db import session_factory
    from app.models import Member

    andere = session_factory()()
    vorher = teilen.fuer_mich(andere, andere.get(Member, world["pl_member"]))
    andere.commit()
    andere.close()
    monkeypatch.setattr(teilen, "gueltiger", lambda db, m: None)  # hat den Schlüssel noch nicht gesehen
    assert teilen.fuer_mich(dbs, dbs.get(Member, world["pl_member"])) == vorher


# ---------------------------------------------------------------- S55 und Datenordner
def test_reset_password_ueber_den_gemeinsamen_weg(dbs, admin):  # noqa: F811
    from typer.testing import CliRunner

    from app.cli import app
    from app.models import User

    chef = _chef(dbs)
    meta_schreiben(dbs, f"pwlink.{chef.id}", "offen")
    chef.token_ausnahme = 123
    dbs.commit()
    r = CliRunner().invoke(app, ["reset-password", "-u", "chef"], input="sehrgeheim99\nsehrgeheim99\n")
    assert r.exit_code == 0, r.output
    dbs.expire_all()
    assert not meta_lesen(dbs, f"pwlink.{chef.id}")
    assert dbs.get(User, chef.id).token_ausnahme is None


def test_datenordner_nur_fuer_den_dienst(tmp_path):
    from app.db import _nur_fuer_den_dienst

    ordner = tmp_path / "daten"
    ordner.mkdir(mode=0o755)
    os.chmod(ordner, 0o755)
    _nur_fuer_den_dienst(ordner)
    if os.name != "nt":
        assert (ordner.stat().st_mode & 0o777) == 0o700
    time.sleep(0)


def test_ergebnisordner_wird_vorher_geprueft(tmp_path, monkeypatch):
    """Modellvergleich: der Ergebnisordner wird als voller Pfad angelegt und vor den Läufen geprüft (OneDrive)."""
    from pathlib import Path

    from app import modellvergleich as mv

    monkeypatch.chdir(tmp_path)
    voll = mv.ziel_vorbereiten(Path("messung/neu"))
    assert voll.is_absolute() and voll.is_dir() and not (voll / ".taleward-probe").exists()
    gesperrt = tmp_path / "gesperrt"
    gesperrt.write_text("eine Datei, kein Ordner")
    with pytest.raises(mv.VergleichFehler, match="außerhalb von OneDrive"):
        mv.ziel_vorbereiten(gesperrt / "unter")


def test_mail_hat_farben_fuer_outlook_dunkel():
    from app import mailvorlage as m

    h = m.html("bestaetigen", "de", link="https://x.example/l", name="A", benutzer="a", server="S", betreiber="B")
    for regel in ("[data-ogsb] .tw-grund", "[data-ogsb] .tw-karte", "[data-ogsc] .tw-ink", "[data-ogsc] .tw-muted",
                  "[data-ogsb] .tw-knopf", "[data-ogsb] .tw-linie"):
        assert regel in h, regel
    assert m.DUNKEL["paper"] in h.split("[data-ogsb] .tw-grund")[1].split("}")[0]
