"""Server 0.4.120: Cloud oder Worker je Kampagne – mit Cloud-API auf dem Server nutzen Kampagnen mit Cloud-Freigabe die
Cloud, alle anderen einen Worker mit lokalem Sprachmodell (wie in der YAML: „sonst lokales Modell“)."""
from types import SimpleNamespace

import pytest

from tests.test_korrektur_0415 import _entscheiden, _recap, _start
from tests.test_pruefung_046 import _zur_pruefung
from tests.test_step2b import _kleine_teile, zusammenfassen  # noqa: F401 (Fixture)

pytestmark = pytest.mark.usefixtures("_kleine_teile")

CAPS = ["asr", "llm", "llm_revise"]


def _api(monkeypatch):
    from app import einstellungen

    monkeypatch.setattr(einstellungen, "llm_konfig", lambda db: SimpleNamespace(
        art="api", api_key="geheim", lokal_modell="m", lokal_kontext=8192))


def _holen(client, h):
    return client.post("/worker/v1/jobs/claim", headers=h,
                       json={"capabilities": CAPS, "waitSeconds": 0, "info": {"llm": "Ollama 0.9"}})


def test_korrektur_ohne_cloud_freigabe_geht_an_den_worker(client, world, dbs, tmp_path, monkeypatch):
    from app.models import Campaign, GameSession, Worker
    from tests.test_step2a import worker_token

    w = world
    sid = _zur_pruefung(client, w, dbs, tmp_path)["id"]
    _api(monkeypatch)
    token = worker_token(dbs)
    dbs.query(Worker).update({"capabilities": "asr,llm"})
    dbs.commit()
    h = {"Authorization": f"Bearer {token}"}
    assert _holen(client, h).status_code == 204  # meldet sich mit Sprachmodell
    c = dbs.get(Campaign, dbs.get(GameSession, sid).campaign_id)
    assert not c.allow_cloud_summary
    assert _start(client, w, sid).status_code == 202
    assert _recap(client, w, sid).json()["revision"]["state"] == "running"  # nicht gleich „Cloud nicht freigegeben“
    a = _holen(client, h).json()
    assert a["type"] == "revise" and a["revise"]["model"] == "m"
    teile = a["revise"]["text"].split("\n\n")
    assert client.post(f"/worker/v1/jobs/{a['jobId']}/revision-result", headers=h, json={
        "changes": [{"index": 1, "before": teile[1], "after": "Pipo lebt."}],
        "notes": [{"text": "Pipo stirbt nicht.", "applied": True, "indexes": [1]}], "model": "ollama/m",
        "tokensIn": 10, "tokensOut": 5, "computeSeconds": 1.5}).status_code == 204
    assert _recap(client, w, sid).json()["revision"]["state"] == "ready"
    assert _entscheiden(client, w, sid, False).status_code == 200
    # Mit Cloud-Freigabe bleibt der Auftrag für die Cloud – der Worker bekommt ihn nicht
    c.allow_cloud_summary = True
    dbs.commit()
    assert _start(client, w, sid).status_code == 202
    assert _holen(client, h).status_code == 204


def test_ohne_worker_bleibt_der_hinweis(client, world, dbs, tmp_path, monkeypatch):
    """Ohne Worker mit Sprachmodell sagt die Korrektur wie bisher, dass die Cloud nicht freigegeben ist."""
    w = world
    sid = _zur_pruefung(client, w, dbs, tmp_path)["id"]
    _api(monkeypatch)
    assert _start(client, w, sid).status_code == 202
    rev = _recap(client, w, sid).json()["revision"]
    assert rev["state"] == "failed" and "Cloud" in rev["message"]


def test_worker_darf_nur_kampagnen_ohne_cloud(dbs):
    from app import queue

    ohne, mit = object(), object()
    erlaubt = {ohne: False, mit: True}
    alt = queue.cloud_erlaubt
    try:
        queue.cloud_erlaubt = lambda db, job: erlaubt[job.kampagne]
        for faehigkeit in ("llm", "llm_revise"):
            assert queue.worker_darf(dbs, SimpleNamespace(required_capability=faehigkeit, kampagne=ohne))
            assert not queue.worker_darf(dbs, SimpleNamespace(required_capability=faehigkeit, kampagne=mit))
        assert queue.worker_darf(dbs, SimpleNamespace(required_capability="asr", kampagne=mit))  # Abschrift wie immer
    finally:
        queue.cloud_erlaubt = alt
