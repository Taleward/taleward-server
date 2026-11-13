"""0.4.36: Wartegrund statt Sackgasse (ProcessingStatus.waitReason und waitActions).

Wartet eine Runde länger als 10 Minuten in queued, transcribing oder summarizing und ist gerade kein Weg frei, nennt
der Server der SL den Grund. Ein Weg ist frei, wenn ein passender Worker erreichbar ist (auch wenn er gerade
beschäftigt ist) oder die Cloud den Schritt für diese Kampagne übernehmen darf. Reihenfolge der Gründe:

1. credit – der Auftrag wartet wegen der Abrechnung (Einzelheiten in creditWait),
2. cloud_not_allowed – die Cloud könnte es, die Kampagne hat sie für diesen Schritt nicht freigegeben,
3. paused – ein passender Worker ist pausiert, das Kostenlimit für Cloud-Dienste ist erreicht oder die
   Zusammenfassung ist auf dem Server abgeschaltet (alles Entscheidungen des Betreibers),
4. no_worker – sonst.

Nur die SL der Kampagne (Rolle serverseitig geprüft) bekommt den Grund und waitActions; Spielende bekommen bei jedem
Grund nur „waiting“. waitActions nennt nur Schalter, die der Server anbietet (nicht in den Sperren) und die gerade aus
sind. Geschaltet wird wie immer über PATCH …/settings – hier gibt es keinen eigenen Weg.
"""
from __future__ import annotations

from datetime import timedelta

from sqlalchemy.orm import Session

from app.db import utcnow
from app.models import Campaign, GameSession, Job, Member

WARTEZEIT = timedelta(minutes=10)
ZUSTAENDE = ("queued", "transcribing", "summarizing")


def _wartender_auftrag(db: Session, s: GameSession) -> Job | None:
    from app import queue

    job = queue.current_job(db, s.id, None)
    if job is None or job.state != "queued":
        return None  # nichts in der Schlange oder ein Worker arbeitet schon daran
    return job


def _abschrift(db: Session, c: Campaign, job: Job) -> tuple[str | None, list[str]]:
    from app import kosten, queue
    from app.kampagnen_einstellungen import sperren

    if queue.worker_online(db, "asr"):
        return None, []
    cloud_moeglich = "allowExternalTranscription" not in sperren(db)
    if cloud_moeglich and c.allow_external_transcription:
        if kosten.erreicht(db):
            return "paused", []
        return None, []  # die Cloud übernimmt zur eingestellten Zeit (Hinweis steht in message)
    if cloud_moeglich:
        return "cloud_not_allowed", ["allow_external_transcription"]
    if queue.pausierte_worker(db, "asr"):
        return "paused", []
    return "no_worker", []


def _sprachmodell(db: Session, c: Campaign, job: Job) -> tuple[str | None, list[str]]:
    from app import kosten, queue
    from app.einstellungen import llm_konfig
    from app.kampagnen_einstellungen import sperren

    k = llm_konfig(db)
    if k.art == "attrappe":
        return None, []  # Testmodus: der Server schreibt selbst
    if k.art == "aus":
        return "paused", []  # Zusammenfassung vom Betreiber abgeschaltet
    cloud_moeglich = k.art == "api" and "allowCloudSummary" not in sperren(db)
    if cloud_moeglich and c.allow_cloud_summary:
        # 0.4.120: Kampagnen mit Freigabe nimmt nur die Cloud, ein Worker greift hier nicht
        return ("paused", []) if kosten.erreicht(db) else (None, [])
    if queue.llm_worker_online(db):
        return None, []
    if cloud_moeglich:
        return "cloud_not_allowed", ["allow_cloud_summary"]
    if queue.pausierte_worker(db, "llm"):
        return "paused", []
    return "no_worker", []


def grund(db: Session, s: GameSession) -> tuple[str | None, list[str]]:
    """(waitReason, waitActions) aus Sicht der SL – ohne Rollenprüfung, die macht `fuer`."""
    if s.state not in ZUSTAENDE:
        return None, []
    job = _wartender_auftrag(db, s)
    if job is None or utcnow() - job.created_at <= WARTEZEIT:
        return None, []
    from app import abrechnung, queue

    if abrechnung.an(db) and abrechnung.warte_grund(db, job) is not None:
        return "credit", []
    c = db.get(Campaign, s.campaign_id)
    if c is None:
        return None, []
    if job.required_capability == "asr":
        return _abschrift(db, c, job)
    if job.required_capability in queue.LLM_FAEHIGKEITEN:
        return _sprachmodell(db, c, job)
    if queue.worker_online(db, job.required_capability):
        return None, []
    return ("paused" if queue.pausierte_worker(db, job.required_capability) else "no_worker"), []


def ist_sl(db: Session, s: GameSession, user) -> bool:
    from app.access import membership

    if user is None:
        return False
    m: Member | None = membership(db, s.campaign_id, user)
    return m is not None and m.role == "gm"


def fuer(db: Session, s: GameSession, user) -> tuple[str | None, list[str]]:
    """Für die anfragende Person: SL bekommt Grund und Schalter, alle anderen nur „waiting“ (oder null)."""
    wert, aktionen = grund(db, s)
    if wert is None:
        return None, []
    if not ist_sl(db, s, user):
        return "waiting", []
    return wert, aktionen
