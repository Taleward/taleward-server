"""Server 0.4.110: Verarbeitungsstand für Taleward Desktop (GET /worker/v1/overview).

Nur für den Worker des Programms und nur vom PC selbst. Titel und Kapitelnummer nur für Kampagnen, die das Konto des
Programms leitet; alles andere als „Runde einer anderen Spielleitung“ ohne Angaben. Keine Inhalte.
"""
from datetime import datetime, timedelta, timezone

import pytest

from tests.test_server_0495 import _konto, _von
from tests.test_server_0496 import _token, ep  # noqa: F401  (Fixture)

PFAD = "/worker/v1/overview"


def _welt(db):
    """Kampagnen der Programm-Person (Anna) und einer anderen Spielleitung (Ben), dazu Aufträge."""
    from sqlalchemy import select

    from app.cli import _neues_konto
    from app.models import Campaign, CampaignDocument, GameSession, Job, Member, User

    anna = db.scalar(select(User).where(User.username == "anna.muller"))
    ben = _neues_konto(db, "ben", "Ben", "Lindwurm-Kessel-8")
    eigen = Campaign(title="Die Schatten von Grauwacht")
    fremd = Campaign(title="Geheime Kampagne von Ben")
    db.add_all([eigen, fremd])
    db.flush()
    db.add_all([Member(campaign_id=eigen.id, user_id=anna.id, role="gm"),
                Member(campaign_id=fremd.id, user_id=ben.id, role="gm"),
                Member(campaign_id=fremd.id, user_id=anna.id, role="player")])  # Anna spielt bei Ben nur mit
    jetzt = datetime.now(timezone.utc)
    s13 = GameSession(campaign_id=eigen.id, number=13, played_at=jetzt - timedelta(days=1), state="transcribing",
                      duration_seconds=3 * 3600, progress=0.4)
    s12 = GameSession(campaign_id=eigen.id, number=12, played_at=jetzt - timedelta(days=8), state="awaiting_review",
                      duration_seconds=4 * 3600)
    s11 = GameSession(campaign_id=eigen.id, number=11, played_at=jetzt - timedelta(days=15), state="published")
    sf = GameSession(campaign_id=fremd.id, number=5, played_at=jetzt - timedelta(days=2), state="awaiting_review",
                     duration_seconds=7200)
    db.add_all([s13, s12, s11, sf])
    db.flush()
    doc = CampaignDocument(campaign_id=fremd.id, title="Geheime Notizen", file_name="n.pdf", kind="gm", size_bytes=1)
    db.add(doc)
    db.flush()
    db.add_all([
        Job(type="transcribe", session_id=s13.id, state="leased", required_capability="asr", progress=0.4,
            started_at=jetzt - timedelta(minutes=12)),
        Job(type="summarize", session_id=sf.id, state="queued", required_capability="llm"),
        Job(type="document", document_id=doc.id, state="queued", required_capability="llm"),
        Job(type="summarize", session_id=s12.id, state="done", required_capability="llm",
            started_at=jetzt - timedelta(hours=2, minutes=5), finished_at=jetzt - timedelta(hours=2)),
        Job(type="transcribe", session_id=s12.id, state="done", required_capability="asr",
            started_at=jetzt - timedelta(hours=3), finished_at=jetzt - timedelta(hours=2, minutes=20)),
        Job(type="transcribe", session_id=sf.id, state="failed", required_capability="asr",
            started_at=jetzt - timedelta(days=1, minutes=30), finished_at=jetzt - timedelta(days=1)),
        Job(type="document", document_id=doc.id, state="done", required_capability="llm",
            started_at=jetzt - timedelta(days=3, minutes=2), finished_at=jetzt - timedelta(days=3)),
    ])
    db.commit()


@pytest.fixture()
def bereit(ep, tmp_path):  # noqa: F811
    from app.db import session_factory

    assert _konto(ep).status_code == 201
    with session_factory()() as db:
        _welt(db)
    return {"Authorization": f"Bearer {_token(tmp_path)}"}


def test_laufender_auftrag_und_zaehler(ep, bereit):  # noqa: F811
    r = ep.get(PFAD, headers=bereit)
    assert r.status_code == 200, r.text
    d = r.json()
    assert r.headers["cache-control"] == "no-store"
    lauf = d["current"]
    assert lauf["step"] == "transcribing" and lauf["own"] is True
    assert lauf["campaignTitle"] == "Die Schatten von Grauwacht" and lauf["chapterNumber"] == 13
    assert lauf["durationSeconds"] == 3 * 3600 and lauf["playedAt"]
    assert lauf["progress"] == pytest.approx(0.4) and lauf["startedAt"]
    assert d["queued"] == 2
    assert d["awaitingReview"] == 1  # nur die eigene Kampagne; Bens Kapitel zählt nicht


def test_zuletzt_fertig_ohne_fremde_angaben(ep, bereit):  # noqa: F811
    d = ep.get(PFAD, headers=bereit).json()
    fertig = d["recent"]
    assert [f["step"] for f in fertig] == ["summarizing", "transcribing", "transcribing", "document"]
    assert fertig[0]["result"] == "done" and fertig[0]["seconds"] == 300
    assert fertig[0]["campaignTitle"] == "Die Schatten von Grauwacht" and fertig[0]["chapterNumber"] == 12
    fremd = [f for f in fertig if not f["own"]]
    assert len(fremd) == 2 and {f["result"] for f in fremd} == {"failed", "done"}
    for f in fremd:  # Runde einer anderen Spielleitung: nichts, woraus man etwas ablesen könnte
        assert f["campaignTitle"] is None and f["chapterNumber"] is None
        assert f["playedAt"] is None and f["durationSeconds"] is None
    text = str(d)
    for geheim in ("Geheime Kampagne", "Geheime Notizen", "Ben"):
        assert geheim not in text


def test_hoechstens_zehn(ep, bereit):  # noqa: F811
    from sqlalchemy import select

    from app.db import session_factory
    from app.models import GameSession, Job

    jetzt = datetime.now(timezone.utc)
    with session_factory()() as db:
        s = db.scalar(select(GameSession).where(GameSession.number == 11))
        for i in range(15):
            db.add(Job(type="transcribe", session_id=s.id, state="done", required_capability="asr",
                       started_at=jetzt - timedelta(days=30, minutes=i + 1), finished_at=jetzt - timedelta(days=30, minutes=i)))
        db.commit()
    assert len(ep.get(PFAD, headers=bereit).json()["recent"]) == 10


def test_nichts_zu_tun(ep, tmp_path):  # noqa: F811
    assert _konto(ep).status_code == 201
    d = ep.get(PFAD, headers={"Authorization": f"Bearer {_token(tmp_path)}"}).json()
    assert d == {"current": None, "queued": 0, "recent": [], "awaitingReview": 0}


def test_nur_programm_worker_und_nur_vom_pc(ep, bereit):  # noqa: F811
    from app.db import session_factory
    from app.models import Worker
    from app.routers.worker import token_hash

    assert ep.get(PFAD).status_code == 401
    # aus dem Heimnetz gibt es die Worker-Schnittstelle gar nicht
    assert _von(ep, "192.168.178.50", "GET", PFAD, headers=bereit).status_code == 404
    # ein anderer (gekoppelter) Worker bekommt nichts – 404, als gäbe es den Weg nicht
    with session_factory()() as db:
        w = Worker(name="anderer-pc", token_hash=token_hash("geheim-geheim"))
        db.add(w)
        db.commit()
        anderer = {"Authorization": f"Bearer wk.{w.id}.geheim-geheim"}
    assert ep.get(PFAD, headers=anderer).status_code == 404


def test_ohne_einzelplatz_gibt_es_den_weg_nicht(client, dbs):
    from app.models import Worker
    from app.routers.worker import token_hash

    w = Worker(name="lokal", token_hash=token_hash("geheim-geheim"), local=True)
    dbs.add(w)
    dbs.commit()
    r = client.get(PFAD, headers={"Authorization": f"Bearer wk.{w.id}.geheim-geheim"})
    assert r.status_code == 404


def test_aeltere_einrichtung_ohne_gemerktes_konto(ep, bereit):  # noqa: F811
    from app.db import session_factory
    from app.desktop import META_KONTO
    from app.models import ServerMeta

    with session_factory()() as db:
        db.delete(db.get(ServerMeta, META_KONTO))
        db.commit()
    d = ep.get(PFAD, headers=bereit).json()  # ältestes Verwalter-Konto = Anna
    assert d["current"]["own"] is True and d["awaitingReview"] == 1
