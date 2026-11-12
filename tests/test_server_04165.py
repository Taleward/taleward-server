"""Server 0.4.165 (S68): Taleward-Cookies unter https mit Vorsatz „__Host-“, doppelte Namen gelten nicht."""
import pytest

from tests.test_verwaltung import csrf_von

HTTPS = "https://testserver"


def _konten(dbs):
    from app.cli import _neues_konto

    _neues_konto(dbs, "chef", "Chefin", "geheim123", admin=True)
    dbs.commit()


def _gesetzt(antwort) -> dict[str, str]:
    """Name → ganze Set-Cookie-Zeile."""
    return {z.split("=", 1)[0]: z for z in antwort.headers.get_list("set-cookie")}


def test_unter_https_mit_vorsatz(client, dbs):
    _konten(dbs)
    r = client.post(f"{HTTPS}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                    follow_redirects=False)
    assert r.status_code == 303
    gesetzt = _gesetzt(r)
    for name in ("__Host-tw_verwaltung", "__Host-tw_geraet"):
        zeile = gesetzt[name]
        assert "Path=/;" in zeile + ";" and "Secure" in zeile and "Domain" not in zeile, zeile
    assert "tw_verwaltung" not in gesetzt and "tw_geraet" not in gesetzt
    # die Sitzung gilt unter dem neuen Namen
    seite = client.get(f"{HTTPS}/verwaltung/", follow_redirects=False)
    assert seite.status_code == 200
    csrf = csrf_von(seite.text)
    # Sprache unter https ebenso
    r = client.get(f"{HTTPS}/verwaltung/sprache/en?weiter=/verwaltung/", follow_redirects=False)
    assert "__Host-tw_sprache" in _gesetzt(r)
    assert "Overview" in client.get(f"{HTTPS}/verwaltung/").text
    # Abmelden löscht unter dem neuen Namen
    r = client.post(f"{HTTPS}/verwaltung/abmelden", data={"csrf": csrf}, follow_redirects=False)
    assert "__Host-tw_verwaltung" in _gesetzt(r)


def test_ohne_https_wie_bisher(client, dbs):
    _konten(dbs)
    r = client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                    follow_redirects=False)
    gesetzt = _gesetzt(r)
    assert "Path=/verwaltung" in gesetzt["tw_verwaltung"] and "Secure" not in gesetzt["tw_verwaltung"]
    assert not any(n.startswith("__Host-") for n in gesetzt)


def test_alter_name_gilt_unter_https_nicht_mehr_und_wird_geloescht(client, dbs):
    _konten(dbs)
    client.post("/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"}, follow_redirects=False)
    alt = client.cookies.get("tw_verwaltung")
    assert alt
    client.cookies.clear()
    r = client.get(f"{HTTPS}/verwaltung/", headers={"cookie": f"tw_verwaltung={alt}; tw_sprache=en"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/anmelden"  # nicht angemeldet
    geloescht = _gesetzt(r)
    assert 'tw_verwaltung=""' in geloescht["tw_verwaltung"] and "Path=/verwaltung" in geloescht["tw_verwaltung"]
    assert 'tw_sprache=""' in geloescht["tw_sprache"]
    # ohne https bleiben die Namen und werden nicht gelöscht
    r = client.get("/verwaltung/", headers={"cookie": f"tw_verwaltung={alt}"}, follow_redirects=False)
    assert r.status_code == 200 and "tw_verwaltung" not in _gesetzt(r)


@pytest.mark.parametrize("basis", ["", HTTPS])
def test_doppelte_cookies_gelten_nicht(client, dbs, basis):
    _konten(dbs)
    r = client.post(f"{basis}/verwaltung/anmelden", data={"username": "chef", "password": "geheim123"},
                    follow_redirects=False)
    name = "__Host-tw_verwaltung" if basis else "tw_verwaltung"
    wert = r.headers["set-cookie"].split(f"{name}=", 1)[1].split(";", 1)[0]
    client.cookies.clear()
    assert client.get(f"{basis}/verwaltung/", headers={"cookie": f"{name}={wert}"},
                      follow_redirects=False).status_code == 200
    r = client.get(f"{basis}/verwaltung/", headers={"cookie": f"{name}={wert}; {name}=untergeschoben"},
                   follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/verwaltung/anmelden"


def test_kekse_einzeln():
    from starlette.requests import Request

    from app import kekse

    def anfrage(schema, cookie):
        return Request({"type": "http", "scheme": schema, "server": ("t", 443), "path": "/", "query_string": b"",
                        "headers": [(b"cookie", cookie.encode())]})

    assert kekse.lesen(anfrage("https", "__Host-tw_guthaben=a"), "tw_guthaben") == "a"
    assert kekse.lesen(anfrage("https", "tw_guthaben=a"), "tw_guthaben") is None
    assert kekse.lesen(anfrage("http", "tw_guthaben=a"), "tw_guthaben") == "a"
    assert kekse.lesen(anfrage("http", "__Host-tw_guthaben=a"), "tw_guthaben") is None
    assert kekse.lesen(anfrage("https", "__Host-tw_guthaben=a; __Host-tw_guthaben=b"), "tw_guthaben") is None
    assert kekse.lesen(anfrage("http", 'tw_sprache="en"'), "tw_sprache") == "en"
    assert kekse.lesen(anfrage("http", "tw_oidc_x=1"), "tw_oidc_x", sicher=True) is None
