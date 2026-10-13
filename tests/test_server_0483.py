"""Server 0.4.83: Einladungscodes – serverweite Grenzen sperren gültige Codes nicht (Vorschau getrennt, Erstversuch
je Netz, Registrierung zählt mit); Updates – neueste freigegebene Fassung ohne Rückfall, Update-Skript baut genau den
freigegebenen Commit."""
import shutil
import subprocess
import sys

import pytest

from app import begrenzung
from app.begrenzung import ZAEHLER
from tests.test_konto import daten, einladung
from tests.test_step2a import API


# ---------------------------------------------------------------- Einladungscodes
def _flut(art: str, anzahl: int):
    """anzahl falsche Codes von lauter verschiedenen Adressen (wie ein Angreifer mit vielen Netzen)."""
    from app.routers.campaigns import code_versuch

    for i in range(anzahl):
        assert code_versuch(f"198.51.{i // 200}.{i % 200}", vorschau=(art == "vorschau")) is not None


def test_netz():
    assert begrenzung.netz("192.0.2.7") == "192.0.2.0/24"
    assert begrenzung.netz("2001:db8:1:2::/64") == "2001:db8:1::/48"
    assert begrenzung.netz("testclient") == "testclient"


def test_volle_beitrittsgrenze_sperrt_gueltige_codes_nicht(client, world, make_user, login, monkeypatch):
    from app.routers import campaigns

    monkeypatch.setattr(campaigns, "JOIN_SERVERWEIT", 20)
    code = client.post(f"{API}/campaigns/{world['cid']}/invites", headers=world["gm"]).json()["code"]
    _flut("beitritt", 20)
    assert ZAEHLER.voll("join-alle", 20, campaigns.JOIN_SERVER_FENSTER)
    # Ein anderes Konto von einer neuen Adresse tritt mit gültigem Code trotzdem bei
    r = client.post(f"{API}/campaigns/join", json={"code": code, "characterName": "Lio"}, headers=world["out"])
    assert r.status_code == 200
    # Raten bleibt gebremst: von hier nur noch ein Erstversuch, danach 429 – auch mit frischem Konto
    make_user("dina")
    make_user("emil")
    r = client.post(f"{API}/campaigns/join", json={"code": "RABE-00000001"}, headers=login("dina"))
    assert r.status_code == 404
    for wer in ("dina", "emil"):
        r = client.post(f"{API}/campaigns/join", json={"code": "RABE-00000002"}, headers=login(wer))
        assert r.status_code == 429
    # … und der gültige Code ginge jetzt von hier auch nicht mehr (die Adresse hat schon falsch geraten)
    r = client.post(f"{API}/campaigns/join", json={"code": code}, headers=login("emil"))
    assert r.status_code == 429


def test_erstversuch_je_netz(client, monkeypatch):
    from app.routers import campaigns
    from app.routers.campaigns import code_versuch

    monkeypatch.setattr(campaigns, "JOIN_SERVERWEIT", 5)
    _flut("beitritt", 5)
    # Erstversuch aus 203.0.113.1 (falsch, wird nicht freigegeben) – danach ist das ganze /24 dran
    assert code_versuch("203.0.113.1", "konto-a") is not None
    assert code_versuch("203.0.113.2", "konto-b") is None
    assert code_versuch("203.0.114.2", "konto-b") is not None  # anderes Netz
    # IPv6: ein /48 zählt als ein Netz
    assert code_versuch("2001:db8:7:1::/64", "konto-c") is not None
    assert code_versuch("2001:db8:7:2::/64", "konto-d") is None
    # Gültig: freigeben nimmt alles zurück, auch den doppelten Netz-Eintrag
    v = code_versuch("2001:db8:8:1::/64", "konto-e")
    assert v is not None and ZAEHLER.voll("join-netz:2001:db8:8::/48", 2, campaigns.JOIN_FENSTER)
    ZAEHLER.freigeben(v)
    assert not ZAEHLER.voll("join-netz:2001:db8:8::/48", 1, campaigns.JOIN_FENSTER)
    assert not ZAEHLER.voll("join-konto:konto-e", 1, campaigns.JOIN_FENSTER)


def test_volle_vorschau_grenze_trifft_nur_die_vorschau(client, world, monkeypatch):
    from app.routers import campaigns

    monkeypatch.setattr(campaigns, "VORSCHAU_SERVERWEIT", 10)
    code = client.post(f"{API}/campaigns/{world['cid']}/invites", headers=world["gm"]).json()["code"]
    _flut("vorschau", 10)
    assert client.get(f"{API}/invites/{code}").status_code == 429  # die App macht dann ohne Titel weiter
    # Einladungsseite: neutral, ohne Aussage über den Code
    for c in (code, "RABE-12345678"):
        r = client.get(f"/einladung/{c}")
        assert r.status_code == 200
        assert "Ob die Einladung noch gilt, zeigt dir die App." in r.text
        assert "gültig bis" not in r.text and "gilt nicht mehr" not in r.text
    r = client.get(f"/einladung/{code}", headers={"Accept-Language": "en"})
    assert "The app will tell you whether the invite is still valid." in r.text
    # Beitritt geht ungestört
    assert not ZAEHLER.voll("join-alle", 1, campaigns.JOIN_SERVER_FENSTER)
    r = client.post(f"{API}/campaigns/join", json={"code": code}, headers=world["out"])
    assert r.status_code == 200


def test_einladungsseite_normal(client, world):
    code = client.post(f"{API}/campaigns/{world['cid']}/invites", headers=world["gm"]).json()["code"]
    r = client.get(f"/einladung/{code}")
    assert r.status_code == 200 and "gültig bis" in r.text and "zeigt dir die App" not in r.text
    assert client.get("/einladung/RABE-12345678").status_code == 404


def test_registrierung_zaehlt_falsche_codes_mit(client, world):
    from app.routers import campaigns

    code = einladung(client, world)
    r = client.post(f"{API}/auth/register", json=daten(inviteCode="RATEN-0001"))
    assert r.status_code == 404
    assert ZAEHLER.voll("join-adr:testclient", 1, campaigns.JOIN_FENSTER)
    assert ZAEHLER.voll("join-alle", 1, campaigns.JOIN_SERVER_FENSTER)
    ZAEHLER.vergessen("join-")
    r = client.post(f"{API}/auth/register", json=daten(inviteCode=code))
    assert r.status_code == 201
    assert not ZAEHLER.voll("join-adr:testclient", 1, campaigns.JOIN_FENSTER)  # gültig zählt nicht


def test_registrierung_bei_voller_grenze_mit_gueltigem_code(client, world, monkeypatch):
    from app.routers import campaigns

    monkeypatch.setattr(campaigns, "JOIN_SERVERWEIT", 5)
    code = einladung(client, world)
    _flut("beitritt", 5)
    assert client.post(f"{API}/auth/register", json=daten(inviteCode=code)).status_code == 201


# ---------------------------------------------------------------- Updates (App, Worker, Server)
def test_kein_rueckfall_auf_aeltere_freigabe(client, dbs):
    from app import aktualisierung
    from app.einstellungen import meta_lesen
    from tests.test_aktualisierung import APK, github, release

    dateien = {"/v1.3.0/taleward-1.3.0.apk": APK + b"neu", "/v1.2.0/taleward-1.2.0.apk": APK}
    gh = github([release("v1.3.0", "taleward-1.3.0.apk", APK + b"neu"), release("v1.2.0", "taleward-1.2.0.apk", APK)],
                [], ["v0.4.1", "v0.5.0"], dateien)
    assert aktualisierung.pruefen(dbs, gh)["app"] == "1.3.0"
    # Die Freigabe von 1.3.0 ist weg (z. B. Anhänge entfernt): es bleibt bei 1.3.0, kein Rückfall auf 1.2.0
    gh = github([release("v1.3.0", "taleward-1.3.0.apk", APK + b"neu", freigegeben=False),
                 release("v1.2.0", "taleward-1.2.0.apk", APK)], [], ["v0.4.1"], dateien)
    ergebnis = aktualisierung.pruefen(dbs, gh)
    assert ergebnis["app"] == "1.3.0" and ergebnis["server"] == "0.5.0"
    assert aktualisierung.stand(dbs, "app")["version"] == "1.3.0"
    assert meta_lesen(dbs, "update.freigegeben.app") == "1.3.0"
    assert aktualisierung.stand(dbs, "server")["version"] == "0.5.0"


def test_tag_ohne_freigabe_blockiert_nicht(client, dbs):
    from app import aktualisierung
    from tests.test_aktualisierung import github

    gh = github([], [], ["v0.4.1", "v99.0.0", "v98.0.0", "v1.0.0"], {}, unsigniert=("v99.0.0", "v98.0.0"))
    assert aktualisierung.pruefen(dbs, gh)["server"] == "1.0.0"
    assert aktualisierung.server_update(dbs)["version"] == "1.0.0"
    # Eine kaputte Unterschrift an einem neueren Tag wird gemeldet, die gültige ältere gilt trotzdem
    import httpx

    from app.einstellungen import meta_lesen
    from tests.freigabe_hilfe import unterschreiben

    gh = github([], [], ["v1.0.0", "v1.1.0"], {})
    echt = gh._transport.handler

    def handler(req):
        if req.url.path.endswith("/v1.1.0/freigabe.txt.sig"):
            return httpx.Response(200, content=unterschreiben(b"etwas anderes").encode())
        return echt(req)

    gh._transport.handler = handler
    assert aktualisierung.pruefen(dbs, gh)["server"] == "1.0.0"
    assert "v1.1.0" in meta_lesen(dbs, "update.fehler")


# ---------------------------------------------------------------- Update-Skript (Docker-Paket)
LINUX = pytest.mark.skipif(not shutil.which("ssh-keygen") or not shutil.which("git") or sys.platform == "win32",
                           reason="das Update-Skript läuft nur auf Linux (mit git und ssh-keygen)")


def _git(ordner, *args) -> str:
    return subprocess.run(["git", "-C", str(ordner), *args], check=True, capture_output=True, text=True,
                          env={"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@example.org", "GIT_COMMITTER_NAME": "t",
                               "GIT_COMMITTER_EMAIL": "t@example.org", "PATH": __import__("os").environ["PATH"],
                               "HOME": str(ordner)}).stdout.strip()


@pytest.fixture()
def paket(tmp_path):
    """Nachgebautes GitHub (lokales Repo, Downloads als Dateien), ein Docker zum Mitschreiben und /opt/taleward."""
    from tests.test_freigabe import _skript_mit_testschluessel

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-q", "-b", "main")
    commits = {}
    for name in ("alt", "gut", "boese"):
        (repo / "datei").write_text(name)
        _git(repo, "add", "datei")
        _git(repo, "commit", "-q", "-m", name)
        commits[name] = _git(repo, "rev-parse", "HEAD")
    _git(repo, "tag", "v0.4.80", commits["alt"])
    _git(repo, "tag", "-a", "-m", "gut", "v0.4.81", commits["gut"])  # Tag mit Text: aufgelöst zählt
    # Lockvögel: ein Branch mit dem Namen des Tags, ein Tag, das nur so endet, neuere Tags ohne Freigabe
    _git(repo, "branch", "v0.4.81", commits["boese"])
    _git(repo, "tag", "zz/refs/tags/v0.4.81", commits["boese"])
    _git(repo, "tag", "zz/refs/tags/v9.9.9", commits["boese"])
    _git(repo, "tag", "v0.4.90", commits["boese"])
    _git(repo, "tag", "v99.0.0", commits["boese"])
    dl = tmp_path / "dl"
    skript = _skript_mit_testschluessel(tmp_path)
    text = skript.read_text(encoding="utf-8")
    for alt, neu in (("REPO=https://github.com/$REPO_NAME.git", f"REPO={repo}"),
                     ("DOWNLOAD=https://github.com/$REPO_NAME/releases/download", f"DOWNLOAD=file://{dl}"),
                     ("ROH=https://raw.githubusercontent.com/$REPO_NAME", f"ROH=file://{tmp_path / 'roh'}")):
        assert alt in text
        text = text.replace(alt, neu)
    skript.write_text(text, encoding="utf-8")
    bin_ = tmp_path / "bin"
    bin_.mkdir()
    (bin_ / "docker").write_text('#!/usr/bin/env bash\necho "$*" >> "$DOCKER_LOG"\n'
                                 'case "$*" in *"chronik sicherung"*) echo "Sicherung angelegt: /daten/s.zip";; esac\n')
    (bin_ / "docker").chmod(0o755)
    ziel = tmp_path / "ziel"
    ziel.mkdir()
    (ziel / ".env").write_text("TALEWARD_MODUS=domain\nTALEWARD_VERSION=v0.4.80\n")

    def freigeben(tag, commit):
        from tests.freigabe_hilfe import freigabe

        (dl / tag).mkdir(parents=True, exist_ok=True)
        t, sig = freigabe("Tinkerworkss/taleward-server", tag, commit=commit)
        (dl / tag / "freigabe.txt").write_bytes(t)
        (dl / tag / "freigabe.txt.sig").write_text(sig)

    def laufen(*args):
        import os

        env = {**os.environ, "PATH": f"{bin_}:{os.environ['PATH']}", "TALEWARD_ZIEL": str(ziel),
               "DOCKER_LOG": str(tmp_path / "docker.log")}
        return subprocess.run(["bash", str(skript), *args], capture_output=True, text=True, env=env, timeout=120)

    def env():
        return dict(z.split("=", 1) for z in (ziel / ".env").read_text().splitlines() if "=" in z)

    def gebaut():
        log = tmp_path / "docker.log"
        return log.exists() and "build" in log.read_text()

    return {"repo": repo, "commits": commits, "freigeben": freigeben, "laufen": laufen, "env": env,
            "gebaut": gebaut, "ziel": ziel}


@LINUX
def test_skript_kandidaten_und_tag_commit(paket):
    p = paket
    r = p["laufen"]("--kandidaten", str(p["repo"]))
    assert r.stdout.split() == ["v99.0.0", "v0.4.90", "v0.4.81", "v0.4.80"]
    r = p["laufen"]("--tag-commit", str(p["repo"]), "v0.4.81")
    assert r.returncode == 0 and r.stdout.strip() == p["commits"]["gut"]
    assert p["laufen"]("--tag-commit", str(p["repo"]), "v0.4.82").returncode != 0
    assert p["laufen"]("--tag-commit", str(p["repo"]), "v9.9.9").returncode != 0  # nur der Lockvogel


@LINUX
def test_skript_baut_genau_den_freigegebenen_commit(paket):
    p = paket
    p["freigeben"]("v0.4.81", p["commits"]["gut"])
    r = p["laufen"]("--jetzt")
    assert r.returncode == 0, r.stdout + r.stderr
    env = p["env"]()
    assert env["TALEWARD_VERSION"] == "v0.4.81" and env["TALEWARD_COMMIT"] == p["commits"]["gut"]
    assert p["gebaut"]()
    assert '"ergebnis": "aktualisiert"' in (p["ziel"] / "daten/aktualisierung/server-status.json").read_text()


@LINUX
def test_skript_lehnt_verschobenes_tag_ab(paket):
    p = paket
    p["freigeben"]("v0.4.81", p["commits"]["alt"])  # Freigabe für einen anderen Stand als das Tag
    r = p["laufen"]("--jetzt")
    assert r.returncode == 1
    assert p["env"]() == {"TALEWARD_MODUS": "domain", "TALEWARD_VERSION": "v0.4.80"}
    assert not p["gebaut"]()
    assert "zeigt nicht auf den freigegebenen Stand" in (p["ziel"] / "daten/aktualisierung/server-status.json") \
        .read_text()


@LINUX
def test_skript_wartet_und_geht_nie_zurueck(paket):
    p = paket
    # Nichts freigegeben: wartet auf die neueste, ändert nichts
    r = p["laufen"]("--jetzt")
    assert r.returncode == 0 and not p["gebaut"]()
    assert '"ergebnis": "wartet"' in (p["ziel"] / "daten/aktualisierung/server-status.json").read_text()
    # Läuft schon eine neuere Fassung, wird die ältere freigegebene nicht genommen
    (p["ziel"] / ".env").write_text("TALEWARD_VERSION=v0.4.85\n")
    p["freigeben"]("v0.4.81", p["commits"]["gut"])
    r = p["laufen"]("--jetzt")
    assert r.returncode == 0 and not p["gebaut"]()
    assert p["env"]() == {"TALEWARD_VERSION": "v0.4.85"}


def test_compose_baut_den_commit():
    for name in ("deploy/docker-compose.yml", "deploy/docker-compose.heimnetz.yml"):
        text = open(name, encoding="utf-8").read()
        assert "git#${TALEWARD_VERSION" not in text
        assert text.count("git#${TALEWARD_COMMIT:-${TALEWARD_VERSION:-main}}") == 3
