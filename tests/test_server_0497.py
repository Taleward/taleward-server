"""Server 0.4.97: Das Taleward-Programm fragt vor einem Update, ob gerade etwas läuft (GET /worker/v1/busy)."""
import os
import time
from datetime import timedelta

from tests.test_server_0496 import _token, ep  # noqa: F401 – Fixture


def _busy(ep, tmp_path) -> dict:  # noqa: F811
    r = ep.get("/worker/v1/busy", headers={"Authorization": f"Bearer {_token(tmp_path)}"})
    assert r.status_code == 200
    return r.json()


def _runde(db):
    from app.db import utcnow
    from app.models import Campaign, GameSession

    k = Campaign(title="Kampagne")
    db.add(k)
    db.flush()
    s = GameSession(campaign_id=k.id, number=1, played_at=utcnow())
    db.add(s)
    db.flush()
    return s


def test_ruhig_ohne_arbeit_und_nur_mit_worker_schluessel(ep, tmp_path):  # noqa: F811
    assert _busy(ep, tmp_path) == {"busy": False, "reason": None}
    assert ep.get("/worker/v1/busy").status_code == 401


def test_auftrag_in_arbeit(ep, tmp_path):  # noqa: F811
    from app.db import session_factory, utcnow
    from app.models import Job

    with session_factory()() as db:
        s = _runde(db)
        j = Job(type="transcribe", session_id=s.id, required_capability="asr", state="queued")
        db.add(j)
        db.commit()
        jid = j.id
    assert _busy(ep, tmp_path)["busy"] is False  # wartende Aufträge stören einen Neustart nicht
    with session_factory()() as db:
        j = db.get(Job, jid)
        j.state, j.lease_expires_at = "leased", utcnow() + timedelta(minutes=5)
        db.commit()
    assert _busy(ep, tmp_path) == {"busy": True, "reason": "job"}
    with session_factory()() as db:
        db.get(Job, jid).lease_expires_at = utcnow() - timedelta(minutes=1)  # Frist abgelaufen
        db.commit()
    assert _busy(ep, tmp_path)["busy"] is False


def test_hochladen_nur_solange_sich_etwas_tut(ep, tmp_path):  # noqa: F811
    from app import storage
    from app.db import session_factory, utcnow
    from app.models import Upload

    with session_factory()() as db:
        s = _runde(db)
        up = Upload(session_id=s.id, source="table", chunk_size=1024, created_at=utcnow() - timedelta(hours=5))
        db.add(up)
        db.commit()
        uid = up.id
    assert _busy(ep, tmp_path)["busy"] is False  # verlassen: blockiert nicht
    ordner = storage.upload_dir(uid)
    ordner.mkdir(parents=True, exist_ok=True)
    teil = ordner / "0.part"
    teil.write_bytes(b"x")
    assert _busy(ep, tmp_path) == {"busy": True, "reason": "upload"}
    alt = time.time() - 3600
    os.utime(teil, (alt, alt))
    assert _busy(ep, tmp_path)["busy"] is False
    with session_factory()() as db:
        db.get(Upload, uid).created_at = utcnow()  # eben begonnen, noch kein Teil
        db.commit()
    assert _busy(ep, tmp_path)["reason"] == "upload"
    with session_factory()() as db:
        db.get(Upload, uid).state = "completed"
        db.commit()
    assert _busy(ep, tmp_path)["busy"] is False


def test_runde_in_verarbeitung_aber_nicht_haengengeblieben(ep, tmp_path):  # noqa: F811
    from app.db import session_factory, utcnow
    from app.models import GameSession

    with session_factory()() as db:
        s = _runde(db)
        s.state, s.state_updated_at = "transcribing", utcnow()
        db.commit()
        sid = s.id
    assert _busy(ep, tmp_path) == {"busy": True, "reason": "processing"}
    with session_factory()() as db:
        db.get(GameSession, sid).state_updated_at = utcnow() - timedelta(hours=5)
        db.commit()
    assert _busy(ep, tmp_path)["busy"] is False
