"""Server 0.4.169: Warteschlange aufräumen (wartenden Auftrag abbrechen, fehlgeschlagenen ausblenden) und eigene
Adresse je Sprache auf öffentlichen Seiten (?lang=en)."""
from sqlalchemy import select

from tests.test_server_04166 import _wartend
from tests.test_verwaltung import admin  # noqa: F401 (Fixture)


def _auftrag(dbs, sid):
    from app.models import Job

    dbs.expire_all()
    return dbs.scalar(select(Job).where(Job.session_id == sid).order_by(Job.created_at.desc()))


def test_wartenden_auftrag_abbrechen(client, world, dbs, admin):  # noqa: F811
    from app.models import GameSession

    sid = _wartend(dbs, world, client, art="summarize")
    j = _auftrag(dbs, sid)
    seite = client.get("/verwaltung/warteschlange").text
    assert f"/verwaltung/auftraege/{j.id}/abbrechen" in seite and "/ausblenden" not in seite
    r = client.post(f"/verwaltung/auftraege/{j.id}/abbrechen", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303
    j = _auftrag(dbs, sid)
    s = dbs.get(GameSession, sid)
    assert j.state == "failed" and j.error_code == "cancelled_by_operator" and s.state == "failed"
    assert "Vom Betreiber abgebrochen." in s.status_message
    seite = client.get("/verwaltung/warteschlange").text
    assert "Vom Betreiber abgebrochen." in seite and "Erneut versuchen" in seite and "/abbrechen" not in seite
    # ein zweites Mal geht nicht (wartet nicht mehr), ohne Schutzwert gar nicht, Unbekanntes 404
    r = client.post(f"/verwaltung/auftraege/{j.id}/abbrechen", data={"csrf": admin})
    assert r.status_code == 409 and "wartet nicht mehr" in r.text
    assert client.post(f"/verwaltung/auftraege/{j.id}/abbrechen", data={}).status_code in (400, 403)
    assert client.post("/verwaltung/auftraege/gibt-es-nicht/abbrechen", data={"csrf": admin}).status_code == 404
    # „Erneut versuchen“ reiht wieder ein
    r = client.post(f"/verwaltung/sessions/{sid}/neustart", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303 and _auftrag(dbs, sid).state == "queued"


def test_auftrag_in_arbeit_laesst_sich_nicht_abbrechen(client, world, dbs, admin):  # noqa: F811
    sid = _wartend(dbs, world, client)
    j = _auftrag(dbs, sid)
    j.state = "leased"
    dbs.commit()
    assert f"/auftraege/{j.id}/abbrechen" not in client.get("/verwaltung/warteschlange").text
    r = client.post(f"/verwaltung/auftraege/{j.id}/abbrechen", data={"csrf": admin})
    assert r.status_code == 409 and _auftrag(dbs, sid).state == "leased"


def test_fehlgeschlagenen_auftrag_ausblenden(client, world, dbs, admin):  # noqa: F811
    from app.models import GameSession

    sid = _wartend(dbs, world, client, art="summarize")
    j = _auftrag(dbs, sid)
    client.post(f"/verwaltung/auftraege/{j.id}/abbrechen", data={"csrf": admin})
    kachel = '>fehlgeschlagen</span><strong class="kennzahlen__wert">{}<'
    assert kachel.format(1) in client.get("/verwaltung/").text
    r = client.post(f"/verwaltung/auftraege/{j.id}/ausblenden", data={"csrf": admin, "an": "1"}, follow_redirects=False)
    assert r.status_code == 303
    # weg aus Liste, Filter und Übersicht – die Runde selbst bleibt, wie sie ist
    seite = client.get("/verwaltung/warteschlange").text
    assert "Vom Betreiber abgebrochen." not in seite and "Ausgeblendete anzeigen (1)" in seite
    assert "Vom Betreiber abgebrochen." not in client.get("/verwaltung/warteschlange?zeigen=fehler").text
    uebersicht = client.get("/verwaltung/").text
    assert kachel.format(0) in uebersicht and "ist fehlgeschlagen" not in uebersicht
    dbs.expire_all()
    assert dbs.get(GameSession, sid).state == "failed"
    seite = client.get("/verwaltung/warteschlange?zeigen=ausgeblendet").text
    assert "Vom Betreiber abgebrochen." in seite and "Wieder einblenden" in seite and "Nur ausgeblendete Aufträge" in seite
    # ein neuer Versuch ist ein neuer Auftrag und steht wieder da
    client.post(f"/verwaltung/sessions/{sid}/neustart", data={"csrf": admin})
    neu = _auftrag(dbs, sid)
    assert neu.id != j.id and f"/auftraege/{neu.id}/abbrechen" in client.get("/verwaltung/warteschlange").text
    # zurückholen; nur Fehlgeschlagenes lässt sich ausblenden
    r = client.post(f"/verwaltung/auftraege/{j.id}/ausblenden", data={"csrf": admin, "an": "0"}, follow_redirects=False)
    assert r.status_code == 303 and "Ausgeblendete anzeigen" not in client.get("/verwaltung/warteschlange").text
    assert client.post(f"/verwaltung/auftraege/{neu.id}/ausblenden", data={"csrf": admin}).status_code == 404
    en = client.get("/verwaltung/warteschlange", headers={"Accept-Language": "en"}).text
    assert ">Cancel<" in en and ">Hide<" in en


def test_sprache_ueber_die_adresse_nur_auf_oeffentlichen_seiten(client, dbs, admin):  # noqa: F811
    from app import rechtstexte_bot  # noqa: F401
    from app.verwaltung.i18n import sprache_von

    class Anfrage:
        def __init__(self, pfad, lang=None, kopf=""):
            self.url = type("U", (), {"path": pfad, "scheme": "http"})()
            self.query_params = {"lang": lang} if lang else {}
            self.headers = {"accept-language": kopf}
            self.cookies = {}

    assert sprache_von(Anfrage("/discord-bot/datenschutz", "en")) == "en"
    assert sprache_von(Anfrage("/discord-bot/datenschutz", "de", "en")) == "de"
    assert sprache_von(Anfrage("/discord-bot/datenschutz", "xx")) == "de"
    assert sprache_von(Anfrage("/verwaltung/warteschlange", "en")) == "de"  # die Verwaltung bleibt beim Cookie
    # der Sprachwechsel auf öffentlichen Seiten geht ohne Anmeldung und führt zurück auf die Seite
    fremd = client.__class__(client.app)
    r = fremd.get("/verwaltung/sprache/en?weiter=/discord-bot/datenschutz", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/discord-bot/datenschutz"
    de = fremd.__class__(client.app).get("/impressum?lang=de", headers={"Accept-Language": "en"})
    en = fremd.__class__(client.app).get("/impressum?lang=en")
    assert de.status_code == en.status_code and 'lang="de"' in de.text and 'lang="en"' in en.text
