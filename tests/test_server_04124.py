"""Server 0.4.124: Website neben dem Server – die Regeln der Website werden an dem geprüft, was Caddy daraus macht
(nur erlaubte Bausteine, nur die eingetragenen Adressen, Dateien nur aus /srv/website); Verknüpfungen im
Website-Ordner führen zum Abbruch und werden beim Hochladen unschädlich gemacht."""
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

WURZEL = Path(__file__).resolve().parent.parent
DEPLOY = WURZEL / "deploy"
FAELLE = json.loads((Path(__file__).parent / "website_regeln_faelle.json").read_text(encoding="utf-8"))
SERVER = "srv.example.org"
WEBSITE = ["example.org", "www.example.org", "app.example.org"]

sys.path.insert(0, str(DEPLOY))
import website_regeln as wr  # noqa: E402


def _caddy() -> str | None:
    return os.environ.get("CADDY") or shutil.which("caddy")


def _adapt(name: str, tmp_path: Path):
    """Mit echtem Caddy übersetzen, wenn vorhanden – sonst die gespeicherte Übersetzung (Caddy 2.10.2)."""
    caddy = _caddy()
    if not caddy:
        return FAELLE[name]["adapt"]
    datei = tmp_path / f"{name}.caddy"
    datei.write_text(FAELLE[name]["quelle"], encoding="utf-8")
    r = subprocess.run([caddy, "adapt", "--adapter", "caddyfile", "--config", str(datei)], capture_output=True, text=True)
    return json.loads(r.stdout) if r.returncode == 0 else None


@pytest.mark.parametrize("name", sorted(FAELLE))
def test_regeln_nach_der_uebersetzung(name, tmp_path):
    """(a) Server-Domain – mit Pfad, http:// oder Port – abgelehnt; (b) vars root, map, file_server { root } abgelehnt;
    (d) die Regeln der Website (Nachbildung von scripts/caddy.mjs) laufen durch."""
    fall = FAELLE[name]
    try:
        wr.vorab(fall["quelle"])
        daten = _adapt(name, tmp_path)
        assert daten is not None, "Caddy versteht die Regeln nicht"
        wr.json_pruefen(daten, SERVER, WEBSITE)
        ergebnis = "ok"
    except (wr.Abgelehnt, AssertionError):
        ergebnis = "abgelehnt"
    assert ergebnis == fall["erwartet"], name


def test_faelle_decken_die_pruefkriterien_ab():
    quellen = {n: f["quelle"] for n, f in FAELLE.items()}
    assert any(q.startswith(SERVER + "/verwaltung") for q in quellen.values())
    assert any(q.startswith("http://" + SERVER) for q in quellen.values())
    assert any(q.startswith(SERVER + ":") for q in quellen.values())
    assert any("vars root /data" in q for q in quellen.values()) and any("{vars.root}" in q for q in quellen.values())
    assert any("root /data" in q and "file_server {" in q for q in quellen.values())
    assert FAELLE["echt"]["erwartet"] == "ok" and "import taleward_website_kopf" in quellen["echt"]


@pytest.mark.parametrize("liste", [[SERVER], ["https://example.org"], ["example.org:8443"], ["example.org/pfad"],
                                   ["*.example.org"], [], ["Example.org ", SERVER.upper()]])
def test_adressen_der_website(liste):
    with pytest.raises(wr.Abgelehnt):
        wr.adressen(SERVER, liste)


def test_adressen_werden_vereinheitlicht():
    assert wr.adressen(SERVER, ["Example.org.", "www.example.org"]) == ["example.org", "www.example.org"]


@pytest.mark.parametrize("text", ["import /etc/caddy/Caddyfile", "import ../x", "import *.caddy",
                                  "x { respond {$TALEWARD_DOMAIN} }", "x { respond {env.HOME} }",
                                  "x { respond {file./data/x} }", "x { root * {vars.root} }",
                                  "x {\n\timport fremd\n}", "(eigen) {\n}\nx {\n\timport fremd\n}"])
def test_vorab(text):
    with pytest.raises(wr.Abgelehnt):
        wr.vorab(text)


def test_vorab_snippets_aus_derselben_datei():
    wr.vorab("(kopf) {\n\theader X-A 1\n}\nexample.org {\n\timport kopf\n}\n")


def _fake_docker(ordner: Path) -> Path:
    """`docker compose exec … caddy adapt|reload` nachgestellt: adapt liefert die gespeicherte Übersetzung des Falls,
    dessen Text in pruefen.txt steht; reload gelingt."""
    bin_ = ordner / "bin"
    bin_.mkdir()
    skript = bin_ / "docker"
    skript.write_text(f"""#!{sys.executable}
import json, os, sys
faelle = json.load(open({str(Path(__file__).parent / 'website_regeln_faelle.json')!r}, encoding="utf-8"))
if "adapt" in sys.argv:
    text = open(os.path.join(os.environ["TALEWARD_ZIEL"], "website-caddy", "pruefen.txt"), encoding="utf-8").read()
    for f in faelle.values():
        if f["quelle"].rstrip() == text.rstrip() and f["adapt"] is not None:
            print(json.dumps(f["adapt"])); sys.exit(0)
    sys.exit(1)
sys.exit(0)
""", encoding="utf-8")
    skript.chmod(0o755)
    return bin_


def _einrichten(tmp_path: Path, fall: str = "echt") -> tuple[Path, Path, dict]:
    ziel, web = tmp_path / "ziel", tmp_path / "web"
    (web / "_caddy").mkdir(parents=True)
    (web / "index.html").write_text("hallo", encoding="utf-8")
    (web / "_caddy" / "taleward.caddy").write_text(FAELLE[fall]["quelle"], encoding="utf-8")
    ziel.mkdir()
    shutil.copy(DEPLOY / "website_regeln.py", ziel / "website_regeln.py")
    (ziel / ".env").write_text(f"TALEWARD_DOMAIN={SERVER}\nWEBSITE_ORDNER={web}\nWEBSITE_DOMAINS={' '.join(WEBSITE)}\n",
                               encoding="utf-8")
    env = {**os.environ, "TALEWARD_ZIEL": str(ziel), "PATH": f"{_fake_docker(tmp_path)}:{os.environ['PATH']}"}
    return ziel, web, env


def _neu_laden(env):
    return subprocess.run(["bash", str(DEPLOY / "website-neu-laden.sh")], capture_output=True, text=True, env=env)


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_neu_laden_uebernimmt_gepruefte_regeln(tmp_path):
    ziel, _web, env = _einrichten(tmp_path)
    r = _neu_laden(env)
    assert r.returncode == 0, r.stderr
    assert (ziel / "website-caddy" / "taleward.caddy").read_text(encoding="utf-8") == FAELLE["echt"]["quelle"]
    assert not (ziel / "website-caddy" / "pruefen.txt").exists()


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
@pytest.mark.parametrize("fall", ["a1", "b1", "b5"])
def test_neu_laden_lehnt_ab_und_laesst_alles_stehen(tmp_path, fall):
    ziel, _web, env = _einrichten(tmp_path, fall)
    (ziel / "website-caddy").mkdir()
    (ziel / "website-caddy" / "alt.caddy").write_text("alt", encoding="utf-8")
    r = _neu_laden(env)
    assert r.returncode != 0 and "Abgelehnt" in r.stderr
    assert sorted(p.name for p in (ziel / "website-caddy").iterdir()) == ["alt.caddy"]


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_snippet_aus_anderer_datei_wird_abgelehnt(tmp_path):
    """Jede Datei für sich wäre unauffällig – zusammen eingebunden holte b.caddy den Baustein aus a.caddy."""
    ziel, web, env = _einrichten(tmp_path)
    (web / "_caddy" / "a.caddy").write_text("(weiter) {\n\treverse_proxy server:8000\n}\n", encoding="utf-8")
    (web / "_caddy" / "b.caddy").write_text("example.org {\n\timport weiter\n}\n", encoding="utf-8")
    r = _neu_laden(env)
    assert r.returncode != 0 and "Snippets aus derselben Datei" in r.stderr
    assert not (ziel / "website-caddy").exists() or not list((ziel / "website-caddy").glob("*.caddy"))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_dateien_werden_zusammen_uebersetzt(tmp_path):
    """Caddy bindet alle Dateien zusammen ein – genau das wird übersetzt und geprüft."""
    ziel, web, env = _einrichten(tmp_path)
    (web / "_caddy" / "zwei.caddy").write_text(FAELLE["b5"]["quelle"], encoding="utf-8")
    r = _neu_laden(env)
    assert r.returncode != 0  # echt + b5 zusammen: Caddy-Nachbildung kennt die Kombination nicht → abgelehnt
    assert not list((ziel / "website-caddy").glob("*.caddy"))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_c_verknuepfung_im_website_ordner_bricht_ab(tmp_path):
    ziel, web, env = _einrichten(tmp_path)
    (web / "zertifikat.pem").symlink_to("/etc/hostname")
    r = _neu_laden(env)
    assert r.returncode != 0 and "Verknüpfungen" in r.stderr
    assert not (ziel / "website-caddy").exists() or not list((ziel / "website-caddy").glob("*.caddy"))


@pytest.mark.skipif(shutil.which("bash") is None, reason="bash fehlt")
def test_ohne_website_adressen_nichts(tmp_path):
    ziel, _web, env = _einrichten(tmp_path)
    (ziel / ".env").write_text((ziel / ".env").read_text(encoding="utf-8").replace("WEBSITE_DOMAINS=", "X="),
                               encoding="utf-8")
    r = _neu_laden(env)
    assert r.returncode != 0 and "WEBSITE_DOMAINS" in r.stderr


def test_e_hochladen_nur_mit_unschaedlichen_verknuepfungen():
    z = (DEPLOY / "website-zugang.sh").read_text(encoding="utf-8")
    assert '"rsync --server "*) exec /usr/bin/rrsync -wo -munge $ORDNER ;;' in z  # rrsync 3.2.7: -munge
    assert 'setzen WEBSITE_DOMAINS "$DOMAINS"' in z and "website_regeln.py\" adressen" in z
    for datei, zeile in ((WURZEL / "install.sh", "website_regeln.py test-instanz.sh; do"),
                         (DEPLOY / "aktualisieren.sh", "website_regeln.py:website_regeln.py test-instanz.sh:test-instanz.sh; do")):  # noqa: E501
        assert zeile in datei.read_text(encoding="utf-8")
