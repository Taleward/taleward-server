"""Server 0.4.108: gesperrte Worker aus der Liste entfernen, Kopplungscode mit Knopf „Kopieren“."""
import re

from tests.test_verwaltung import admin  # noqa: F401  (Fixture)


def _worker(client, admin, name="werkstatt-pc"):  # noqa: F811
    client.post("/verwaltung/worker", data={"csrf": admin, "name": name})
    return name


def test_nur_gesperrte_worker_lassen_sich_entfernen(client, dbs, admin):  # noqa: F811
    from app.models import Job, Worker

    _worker(client, admin)
    wid = dbs.query(Worker).filter_by(name="werkstatt-pc").one().id
    seite = client.get("/verwaltung/transkription").text
    assert f"/verwaltung/worker/{wid}/entfernen" not in seite  # erst sperren
    assert client.post(f"/verwaltung/worker/{wid}/entfernen", data={"csrf": admin}).status_code == 409
    dbs.expire_all()
    assert dbs.get(Worker, wid) is not None

    # Ein Auftrag, den dieser Worker einmal bearbeitet hat, bleibt stehen
    j = Job(type="transcribe", state="done", required_capability="asr", progress=1.0, lease_worker_id=wid)
    dbs.add(j)
    dbs.commit()
    jid = j.id
    assert "werkstatt-pc" in client.get("/verwaltung/warteschlange").text

    client.post(f"/verwaltung/worker/{wid}/sperren", data={"csrf": admin})
    seite = client.get("/verwaltung/transkription").text
    assert f"/verwaltung/worker/{wid}/entfernen" in seite
    assert "„werkstatt-pc“ aus der Liste entfernen?" in seite  # Bestätigung nennt den Namen
    r = client.post(f"/verwaltung/worker/{wid}/entfernen", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code == 303
    dbs.expire_all()
    assert dbs.get(Worker, wid) is None
    assert dbs.get(Job, jid).lease_worker_id == wid
    seite = client.get(r.headers["location"]).text
    assert "Worker aus der Liste entfernt." in seite and "werkstatt-pc" not in seite
    assert "entfernter Worker" in client.get("/verwaltung/warteschlange").text
    # Name ist wieder frei, zweimal entfernen geht nicht
    assert client.post(f"/verwaltung/worker/{wid}/entfernen", data={"csrf": admin}).status_code == 404


def test_entfernen_braucht_csrf_und_verwalter(client, dbs, admin):  # noqa: F811
    from app.models import Worker

    _worker(client, admin, "alter-pc")
    w = dbs.query(Worker).filter_by(name="alter-pc").one()
    client.post(f"/verwaltung/worker/{w.id}/sperren", data={"csrf": admin})
    assert client.post(f"/verwaltung/worker/{w.id}/entfernen", data={"csrf": "falsch"}).status_code in (400, 403)
    client.cookies.clear()
    r = client.post(f"/verwaltung/worker/{w.id}/entfernen", data={"csrf": admin}, follow_redirects=False)
    assert r.status_code in (303, 401, 403)
    dbs.expire_all()
    assert dbs.get(Worker, w.id) is not None


def test_lokaler_worker_wird_nie_entfernt(client, dbs, admin):  # noqa: F811
    from app.models import Worker
    from app.db import utcnow

    w = Worker(name="lokal", token_hash="x", local=True, revoked_at=utcnow())
    dbs.add(w)
    dbs.commit()
    assert client.post(f"/verwaltung/worker/{w.id}/entfernen", data={"csrf": admin}).status_code == 409


def test_kopplungscode_mit_knopf_kopieren(client, admin):  # noqa: F811
    client.post("/verwaltung/assistent/koppeln", data={"csrf": admin, "weiter": "/verwaltung/transkription"})
    seite = client.get("/verwaltung/transkription").text
    code = re.search(r'<code class="koppelcode ganz" id="koppelcode">([A-Z0-9]{4}-[A-Z0-9]{4})</code>', seite)
    assert code, "Kopplungscode fehlt"
    knopf = re.search(r'<button[^>]*data-kopieren="koppelcode"[^>]*>', seite).group(0)
    assert "hidden" in knopf and 'type="button"' in knopf  # ohne JavaScript unsichtbar, Code bleibt markierbar
    assert 'data-fertig="Kopiert"' in knopf
    assert '<script src="/verwaltung/static/kopieren.js?v=' in seite  # 0.4.141: mit Fassung
    js = client.get("/verwaltung/static/kopieren.js").text
    assert "navigator.clipboard" in js and "data-kopieren" in js


def test_texte_auf_englisch():
    from app.verwaltung.i18n import EN

    for text in ("Aus der Liste entfernen", "„{name}“ aus der Liste entfernen?", "Kopieren", "Kopiert",
                 "Worker aus der Liste entfernt.", "entfernter Worker",
                 "Aufträge und Protokoll behalten ihren Eintrag. Zum Wiederverbinden den Worker neu koppeln."):
        assert text in EN, text
