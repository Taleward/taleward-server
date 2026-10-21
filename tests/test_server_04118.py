"""Server 0.4.118: Die Verwaltung verlinkt den Windows-Worker nie über GitHubs „latest“ (zeigt dort auf eine alte
Fassung), sondern auf die freigegebene Fassung dieses Servers bzw. die Release-Seite."""
from tests.test_aktualisierung import gh  # noqa: F401  (Fixture)
from tests.test_verwaltung import admin  # noqa: F401  (Fixture)


def test_worker_link_ohne_latest(client, dbs, admin, gh):  # noqa: F811
    from app import aktualisierung

    seite = client.get("/verwaltung/transkription").text
    assert "releases/latest" not in seite
    assert 'href="https://github.com/Taleward/taleward-worker/releases"' in seite  # noch nichts freigegeben
    aktualisierung.pruefen(dbs, gh)
    seite = client.get("/verwaltung/transkription").text
    assert 'href="/downloads/worker-windows/0.1.0/TalewardWorker-Setup.exe"' in seite
    client.post("/verwaltung/assistent/betrieb/art", data={"csrf": admin, "art": "lokal"})
    seite = client.get("/verwaltung/assistent/betrieb").text
    assert "releases/latest" not in seite


def test_passwort_neu_ueber_standardeingabe(client, dbs, admin, make_user):  # noqa: F811
    from typer.testing import CliRunner

    from app.cli import app

    make_user("spielerin")
    r = CliRunner().invoke(app, ["verwalter"])
    assert r.exit_code == 0 and "spielerin" not in r.output.split() and r.output.strip()
    name = r.output.split()[0]
    r = CliRunner().invoke(app, ["passwort-neu", "-u", name], input="kurz\n")
    assert r.exit_code == 2
    r = CliRunner().invoke(app, ["passwort-neu", "-u", "niemand"], input="langes-passwort\n")
    assert r.exit_code == 1
    r = CliRunner().invoke(app, ["passwort-neu", "-u", name], input="ganz-neues-passwort\n")
    assert r.exit_code == 0 and "Passwort geändert." in r.output
    an = client.post("/api/v1/auth/login", json={"username": name, "password": "ganz-neues-passwort"})
    assert an.status_code == 200


def test_spiegel_mit_eigener_kennung(client, dbs, monkeypatch):
    import httpx
    import pytest

    from app import modellablage
    from tests.test_server_04117 import _spiegel_an

    assert modellablage.kennung().startswith("Taleward/0.4.")
    _spiegel_an(monkeypatch)
    gesehen = []

    def schutz(request: httpx.Request) -> httpx.Response:
        gesehen.append(request.headers.get("user-agent"))
        return httpx.Response(403, html="<html>Bot?</html>")

    with pytest.raises(modellablage.AblageFehler, match="antwortet mit 403"):
        modellablage.holen(dbs, klient=httpx.Client(transport=httpx.MockTransport(schutz),
                                                     headers={"User-Agent": modellablage.kennung()}))
    assert gesehen[0].startswith("Taleward/")
