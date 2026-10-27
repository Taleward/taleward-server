"""Abrechnung mit Guthaben (0.4.85): Wer verarbeiten lässt, zahlt aus einem Guthaben, das vorher aufgeladen wurde.

Schalter `abrechnung.modus` in der Verwaltung: `aus` (Standard – alles wie bisher, alle Wege dazu antworten 404),
`guthaben` (jede zahlende SL hat ein eigenes Guthaben) oder `topf` (die Organisation der Kampagne zahlt).

- **Buchungsbuch:** Jede Bewegung ist eine eigene Zeile in `buchungen`, die die Datenbank weder ändern noch löschen
  lässt. Der Saldo ist die Summe; einen gespeicherten Kontostand gibt es nicht. Beträge in ganzen Cent, Währung EUR.
- **Start:** Geprüft wird genau an einer Stelle, wenn ein Auftrag vergeben wird (`queue.claim` und die
  Cloud-Transkription in `extern`). Saldo ≤ 0 → der Auftrag wartet. Saldo minus Schätzung unter dem Deckel
  (Standard −1,50 €) → er wartet. Je Guthaben läuft höchstens ein kostenpflichtiger Auftrag (Teilindex in der
  Datenbank). Ein gestarteter Auftrag läuft immer zu Ende, auch ins Minus; automatische Wiederholungen gehören dazu.
- **Abrechnung:** Am Ende (fertig oder endgültig gescheitert) wird gebucht. Cloud: die tatsächlichen Kosten aus dem
  Verbrauchsprotokoll. Worker: Betreiberpreis × die Menge, die der Server beim Start selbst gemessen hat (Länge der
  Aufnahme bzw. Seiten) – nie die vom Worker gemeldete Rechenzeit. Auf beides kommt der Aufschlag des Betreibers.
- **Wer zahlt:** die zahlende SL der Kampagne (ohne Eintrag die SL, die sie angelegt hat; abgeben nur ausdrücklich)
  bzw. im Topf-Modus die Organisation. Spieler zahlen nie.
- Probeläufe der Verwaltung und Stimmprofile kosten nichts.

Preise und Grenzen stehen in der Verwaltung (server_meta), nie im Repository.
"""
from __future__ import annotations

import hashlib
import json
import math
import secrets
from dataclasses import asdict, dataclass, fields
from datetime import datetime

from sqlalchemy import func, select, update
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from sqlalchemy.orm.attributes import set_committed_value

from app.db import utcnow
from app.einstellungen import meta_lesen, meta_schreiben
from app.models import (AbrechnungProtokoll, Buchung, Campaign, CampaignDocument, GameSession, Guthaben, Gutschein,
                        Job, KostenAuftrag, Member, OrgMember, Upload, UsageLog)

MODI = ("aus", "guthaben", "topf")
KOSTENPFLICHTIG = ("transcribe", "summarize", "revise", "document")
NUTZUNG = {"transcribe": "transcription", "summarize": "summary", "revise": "revision", "document": "document"}
MAX_AUFNAHME_SEKUNDEN = 8 * 3600  # feste Obergrenze je Runde


def verarbeitung_hoechstens(db: Session) -> int | None:
    """0.4.99: So viele Sekunden Audio werden je Runde höchstens verarbeitet (Zusammenfügen bricht danach ab) –
    mit Abrechnung die feste Obergrenze plus eine Minute Luft, sonst None (dann gilt die allgemeine Grenze in audio)."""
    return MAX_AUFNAHME_SEKUNDEN + 60 if an(db) else None


# ---------------------------------------------------------------- Einstellungen des Betreibers
@dataclass
class Preise:
    """Alle Werte in Cent. Worker-Preise legt der Betreiber fest; die Cloud-Werte sind nur Schätzungen für den Start
    (0 = automatisch: Mittelwert der bisherigen Läufe, sonst aus den Preisen des Anbieters), abgerechnet werden dort
    die tatsächlichen Kosten."""

    aufschlag_prozent: int = 0
    worker_transkription_h: int = 0  # je Stunde Aufnahme
    worker_kapitel_h: int = 0  # je Stunde Aufnahme
    worker_korrektur: int = 0  # je Korrektur per Hinweis
    worker_unterlage_seite: int = 0
    cloud_transkription_h: int = 0
    cloud_kapitel_h: int = 0
    cloud_korrektur: int = 0
    cloud_unterlage_seite: int = 0
    deckel: int = 150  # so weit darf ein Auftrag ins Minus führen (laut Schätzung)
    warnung_ab: int = 150  # „Guthaben knapp“ darunter
    freigabe_standard: int = 200  # Aufträge darüber warten auf „Verarbeiten“ (je Guthaben einstellbar)
    gutschrift_max: int = 5000  # Obergrenze je Gutschrift von Hand bzw. je Gutschein


def modus(db: Session) -> str:
    wert = meta_lesen(db, "abrechnung.modus") or "aus"
    return wert if wert in MODI else "aus"


def an(db: Session) -> bool:
    return modus(db) != "aus"


def preise(db: Session) -> Preise:
    try:
        roh = json.loads(meta_lesen(db, "abrechnung.preise") or "{}")
    except ValueError:
        roh = {}
    p = Preise()
    for f in fields(Preise):
        try:
            setattr(p, f.name, max(0, int(roh.get(f.name, getattr(p, f.name)))))
        except (TypeError, ValueError):
            pass
    return p


def preise_schreiben(db: Session, p: Preise, von: str) -> None:
    alt = asdict(preise(db))
    meta_schreiben(db, "abrechnung.preise", json.dumps(asdict(p)))
    protokoll(db, von, "preise", alt=alt, neu=asdict(p))


def modus_schreiben(db: Session, wert: str, von: str) -> None:
    if wert not in MODI:
        raise ValueError(wert)
    protokoll(db, von, "modus", alt=modus(db), neu=wert)
    meta_schreiben(db, "abrechnung.modus", wert)


def protokoll(db: Session, von: str, was: str, guthaben_id: str | None = None, **daten) -> None:
    db.add(AbrechnungProtokoll(von=von, guthaben_id=guthaben_id, was=was,
                               daten=json.dumps(daten, ensure_ascii=False, default=str)))


# ---------------------------------------------------------------- Guthaben und Saldo
def _neu(db: Session, anlegen: bool, **werte) -> Guthaben:
    """Neues Guthaben. anlegen=False (Lesewege wie Hinweise und Anzeigen): nur ein Vorläufiges ohne Zeile in der
    Datenbank – Saldo 0, Einstellungen wie voreingestellt –, damit ein Lesezugriff keine Schreibsperre hält."""
    g = Guthaben(freigabe_ab_cent=preise(db).freigabe_standard, bogen_seiten=20, boegen_je_spieler=3, **werte)
    if anlegen:
        db.add(g)
        db.flush()
    return g


def guthaben_konto(db: Session, user_id: str, anlegen: bool = True) -> Guthaben:
    g = db.scalar(select(Guthaben).where(Guthaben.user_id == user_id))
    return g if g is not None else _neu(db, anlegen, art="konto", user_id=user_id)


def guthaben_organisation(db: Session, org_id: str, anlegen: bool = True) -> Guthaben:
    g = db.scalar(select(Guthaben).where(Guthaben.organization_id == org_id))
    return g if g is not None else _neu(db, anlegen, art="organisation", organization_id=org_id)


def saldo(db: Session, guthaben_id: str | None) -> int:
    if guthaben_id is None:
        return 0
    return int(db.scalar(select(func.coalesce(func.sum(Buchung.betrag_cent), 0))
                         .where(Buchung.guthaben_id == guthaben_id)) or 0)


def stand(db: Session, g: Guthaben) -> str:
    """minus | leer | knapp | gut – für Hinweise ohne Betrag."""
    s = saldo(db, g.id)
    if s < 0:
        return "minus"
    if s == 0:
        return "leer"
    return "knapp" if s < preise(db).warnung_ab else "gut"


def buchen(db: Session, g: Guthaben, art: str, betrag_cent: int, von: str = "system", _schreiben: bool = True,
           **bezug) -> Buchung:
    b = Buchung(guthaben_id=g.id, art=art, betrag_cent=int(betrag_cent), von=von, **bezug)
    db.add(b)
    if _schreiben:  # nicht während eines laufenden Schreibvorgangs (Ereignis beim Abschluss)
        db.flush()
    return b


def darf_sehen(db: Session, user_id: str, g: Guthaben) -> bool:
    """Saldo und Buchungen sieht nur das Konto selbst bzw. die Verwalter der Organisation (sonst 404)."""
    if g.art == "konto":
        return g.user_id == user_id
    return db.scalar(select(func.count()).select_from(OrgMember).where(
        OrgMember.organization_id == g.organization_id, OrgMember.user_id == user_id,
        OrgMember.role == "admin")) > 0


def sichtbare_guthaben(db: Session, user_id: str) -> list[Guthaben]:
    """Eigenes Guthaben und – im Topf-Modus – die Töpfe der Organisationen, in denen die Person Verwalter ist."""
    aus = [guthaben_konto(db, user_id)]
    if modus(db) == "topf":
        for om in db.scalars(select(OrgMember).where(OrgMember.user_id == user_id, OrgMember.role == "admin")):
            aus.append(guthaben_organisation(db, om.organization_id))
    return aus


# ---------------------------------------------------------------- Wer zahlt
def aktive_sl(db: Session, c: Campaign) -> list[Member]:
    return list(db.scalars(select(Member).where(
        Member.campaign_id == c.id, Member.role == "gm", Member.user_id.is_not(None), Member.left_at.is_(None),
        Member.deleted_at.is_(None)).order_by(Member.joined_at)))


def zahler_user_id(db: Session, c: Campaign) -> str | None:
    """Zahlende SL der Kampagne, solange sie dort SL ist. Ohne Eintrag: die zuerst beigetretene SL (die Kampagne angelegt
    hat). Ist diese nicht mehr SL, zahlt niemand, bis eine andere SL die Zahlung ausdrücklich übernimmt."""
    uid = c.zahler_user_id
    if uid is None:
        erste = db.scalar(select(Member).where(Member.campaign_id == c.id, Member.role == "gm")
                          .order_by(Member.joined_at).limit(1))
        uid = erste.user_id if erste is not None else None
    if uid is None or uid not in {m.user_id for m in aktive_sl(db, c)}:
        return None
    return uid


def zahler(db: Session, c: Campaign | None, anlegen: bool = False) -> Guthaben | None:
    """Guthaben, das für Aufträge dieser Kampagne zahlt. anlegen=False: ohne Schreibzugriff (siehe _neu)."""
    if c is None:
        return None
    if modus(db) == "topf" and c.organization_id:
        return guthaben_organisation(db, c.organization_id, anlegen)
    uid = zahler_user_id(db, c)
    return guthaben_konto(db, uid, anlegen) if uid else None


# Zustände einer Runde, in denen ihre Aufnahme auf Verarbeitung wartet (Hochladen läuft oder Abschrift steht an)
AUFNAHME_WARTET = ("uploading", "queued")


def wartende_aufnahme(db: Session, s: GameSession) -> GameSession | None:
    """0.4.24: Mit Einzelzahler und Guthaben ≤ 0 wartet höchstens eine Aufnahme je zahlendem Konto. Gibt die schon
    wartende Runde zurück (eine andere als s), sonst None. Ohne Zahler entscheidet der übliche Wartegrund. Liegen
    gebliebene Uploads setzt die Wartung zurück, Aufnahmen nach der Aufbewahrungsfrist zählen nicht mehr."""
    if modus(db) != "guthaben":
        return None
    c = db.get(Campaign, s.campaign_id)
    g = zahler(db, c)
    if g is None or saldo(db, g.id) > 0:
        return None
    uid = zahler_user_id(db, c)
    kampagnen = [m.campaign_id for m in db.scalars(select(Member).where(
        Member.user_id == uid, Member.role == "gm", Member.left_at.is_(None), Member.deleted_at.is_(None)))]
    for andere in db.scalars(select(GameSession).where(
            GameSession.campaign_id.in_(kampagnen), GameSession.id != s.id,
            GameSession.state.in_(AUFNAHME_WARTET), GameSession.audio_deleted_at.is_(None))  # nach Frist gelöscht: zählt nicht
            .order_by(GameSession.created_at)):
        k = db.get(Campaign, andere.campaign_id)
        gz = zahler(db, k)
        if gz is not None and gz.id == g.id:
            return andere
    return None


def kampagne_von_auftrag(db: Session, job: Job) -> Campaign | None:
    from app.queue import kampagne_von

    return kampagne_von(db, job)


def zahlung_anbieten(db: Session, c: Campaign, von: str, an_user_id: str) -> None:
    if zahler_user_id(db, c) != von or an_user_id == von or an_user_id not in {m.user_id for m in aktive_sl(db, c)}:
        raise ValueError("zahler")
    c.zahler_angebot_user_id = an_user_id
    protokoll(db, von, "zahler_angebot", kampagne=c.id, an=an_user_id)


def zahlung_uebernehmen(db: Session, c: Campaign, user_id: str) -> None:
    """Annehmen eines Angebots – oder übernehmen, wenn die bisherige zahlende SL nicht mehr SL ist."""
    if user_id not in {m.user_id for m in aktive_sl(db, c)}:
        raise ValueError("keine_sl")
    if c.zahler_angebot_user_id != user_id and zahler_user_id(db, c) is not None:
        raise ValueError("kein_angebot")
    protokoll(db, user_id, "zahler_uebernommen", kampagne=c.id, vorher=c.zahler_user_id)
    c.zahler_user_id, c.zahler_angebot_user_id = user_id, None


# ---------------------------------------------------------------- Menge und Schätzung
def aufnahme_messen(up: Upload) -> float | None:
    """Länge einer fertig hochgeladenen Aufnahme selbst messen (ffprobe je Datei). Tisch: Dateien hintereinander
    (Summe); Discord: Spuren gleichzeitig (längste). None = nicht messbar."""
    import shutil
    import tempfile
    from pathlib import Path

    from app import audio, storage

    laengen = []
    with tempfile.TemporaryDirectory(prefix="taleward-dauer-") as tmp:
        for f in up.files:
            ziel = Path(tmp) / f"teil-{f.position}{Path(f.file_name).suffix.lower()[:8]}"
            with open(ziel, "wb") as aus:
                for i in range(f.chunk_count):
                    with open(storage.chunk_path(up.id, f.id, i), "rb") as ein:
                        shutil.copyfileobj(ein, aus)
            d = audio.dauer_datei(ziel, gruendlich=True)  # 0.4.91: Kosten hängen dran – immer dekodieren
            ziel.unlink(missing_ok=True)
            if d is None:
                return None
            laengen.append(d)
    if not laengen:
        return None
    return max(laengen) if up.source == "discord" else sum(laengen)



def groesse_sekunden(up: Upload) -> int:
    """Grobe Länge nach Dateigröße (64 kbit/s – eher zu lang als zu kurz), wenn die Aufnahme nicht messbar ist."""
    return int(sum(f.size_bytes for f in up.files) / 8000)


def aufnahme_sekunden(db: Session, session_id: str | None) -> int:
    """Länge der Aufnahme nach eigener Messung des Servers. Nicht messbar: das Größere aus der gemeldeten Länge der Runde
    (setzt die SL) und der Schätzung nach Dateigröße (0.4.87)."""
    if not session_id:
        return 0
    up = db.scalar(select(Upload).where(Upload.session_id == session_id, Upload.state == "completed")
                   .order_by(Upload.completed_at.desc()).limit(1))
    if up is not None and up.dauer_sekunden:
        return int(math.ceil(up.dauer_sekunden))
    s = db.get(GameSession, session_id)
    gemeldet = int(s.duration_seconds) if s is not None and s.duration_seconds else 0
    return max(gemeldet, groesse_sekunden(up) if up is not None else 0)


def menge_nachtragen(db: Session, job: Job, sekunden: float | None) -> None:
    """Transkription fertig: Ist die tatsächlich verarbeitete Audiolänge größer als beim Start gemessen (z. B. ein
    präparierter Datei-Kopf), zählt die größere – nie weniger als gemessen (0.4.89)."""
    k = db.get(KostenAuftrag, job.id)
    if k is None or k.zustand != "laeuft" or k.art != "transcribe" or not sekunden or sekunden <= 0:
        return
    k.menge = max(k.menge or 0, int(math.ceil(float(sekunden))))  # 0.4.94: ohne Kappung – zählt, was verarbeitet ist


def menge(db: Session, job: Job) -> int:
    if job.type in ("transcribe", "summarize"):
        return aufnahme_sekunden(db, job.session_id)
    if job.type == "document" and job.analysis_id:  # 0.4.18: Charakterbogen bzw. Hintergrund, höchstens 20 Seiten
        from app.charakterauswertung import fuer_auftrag, seiten

        a = fuer_auftrag(db, job)
        return seiten(a) if a is not None else 1
    if job.type == "document":
        doc = db.get(CampaignDocument, job.document_id or "")
        return max(1, (doc.page_count or 1) if doc else 1)
    return 1


def _mittelwert_cloud(db: Session, art: str) -> float:
    """Durchschnittliche Cloud-Kosten der letzten Läufe (Cent je Stunde Aufnahme, je Seite bzw. je Korrektur)."""
    zeilen = list(db.scalars(select(UsageLog).where(UsageLog.engine == "external", UsageLog.kind == NUTZUNG[art],
                                                    UsageLog.cost_cents > 0)
                             .order_by(UsageLog.created_at.desc()).limit(50)))
    if not zeilen:
        return 0.0
    kosten = sum(z.cost_cents for z in zeilen)
    if art == "revise":
        return kosten / len(zeilen)
    if art == "document":
        docs = [db.get(CampaignDocument, z.document_id or "") for z in zeilen]
        return kosten / max(1, sum(max(1, d.page_count or 1) if d else 1 for d in docs))
    if art == "transcribe":
        sek = sum(z.audio_seconds or 0 for z in zeilen)
    else:
        runden = [db.get(GameSession, z.session_id or "") for z in zeilen]
        sek = sum((r.duration_seconds or 0) if r else 0 for r in runden)
    return kosten * 3600 / sek if sek > 0 else 0.0


# 0.4.93: Tokens für die Schätzung aus den Preisen des Anbieters, solange es keine eigenen Läufe gibt. Gemessen an der
# Lange Testrunde (158 min): mit Mistral 211k Tokens ein / 10k aus für Kapitel, Vorschläge und Gegenprüfung
# (≈ 80k / 3,8k je Stunde) – aufgerundet. Korrektur: Hinweis, Kapitel und bis 40.000 Zeichen Abschrift. Unterlage:
# eine Seite (≈ 3.000 Zeichen) plus Bibel und Vorschläge.
TOKENS_SCHAETZUNG = {"summarize": (85_000, 4_500), "revise": (20_000, 3_000), "document": (2_000, 500)}


def _token_preis(db: Session) -> tuple[float, float] | None:
    """Cent je 1 Mio. Tokens (ein, aus) des Cloud-Sprachmodells – eigene Preise aus der Verwaltung, sonst der teuerste
    der eingestellten Modelle laut Preistabelle. None = unbekannt (fremdes Modell ohne eigene Preise)."""
    from app.einstellungen import llm_konfig
    from app.sprachmodell import PREISE

    k = llm_konfig(db)
    if k.cent_ein is not None and k.cent_aus is not None:
        return float(k.cent_ein), float(k.cent_aus)
    modelle = {m for m in (k.api_modell, k.api_modell_vorschlaege, k.api_modell_notizen) if m}
    preise_ = [PREISE.get(m) for m in modelle]
    if not preise_ or any(p is None for p in preise_):
        return None
    return float(max(p[0] for p in preise_)), float(max(p[1] for p in preise_))


def preis_aus_anbieter(db: Session, art: str) -> float:
    """Schätzung aus den Preisen des Anbieters (Cent je Stunde Aufnahme, je Stück bzw. je Seite); 0 = unbekannt."""
    if art == "transcribe":
        from app.einstellungen import extern_konfig

        return max(0.0, float(extern_konfig(db).cent_pro_minute or 0)) * 60
    preis = _token_preis(db)
    if preis is None:
        return 0.0
    ein, aus = TOKENS_SCHAETZUNG[art]
    return (ein * preis[0] + aus * preis[1]) / 1_000_000


def preis_je_einheit(db: Session, art: str, weg: str) -> float:
    """Cent je Stunde Aufnahme (Transkription, Kapitel), je Seite (Unterlage) bzw. je Stück (Korrektur)."""
    p = preise(db)
    worker = weg == "worker"
    feld = {"transcribe": "transkription_h", "summarize": "kapitel_h", "revise": "korrektur",
            "document": "unterlage_seite"}[art]
    wert = getattr(p, ("worker_" if worker else "cloud_") + feld)
    if not worker and wert == 0:
        return _mittelwert_cloud(db, art) or preis_aus_anbieter(db, art)
    return float(wert)


def _mit_aufschlag(db: Session, cent: float) -> int:
    return int(math.ceil(cent * (100 + preise(db).aufschlag_prozent) / 100 - 1e-9)) if cent > 0 else 0


def betrag_fuer(db: Session, art: str, weg: str, m: int) -> int:
    einheit = preis_je_einheit(db, art, weg)
    basis = einheit * m / 3600 if art in ("transcribe", "summarize") else einheit * m
    return _mit_aufschlag(db, basis)


def schaetzung(db: Session, job: Job, weg: str | None = None) -> int:
    """Geschätzte Kosten in Cent (mit Aufschlag). Ohne Weg: der teurere der möglichen Wege (für die Anzeige)."""
    m = menge(db, job)
    wege = [weg] if weg else ["worker", "cloud"]
    return max(betrag_fuer(db, job.type, w, m) for w in wege)


# ---------------------------------------------------------------- Start (ein Prüfpunkt)
def warte_grund(db: Session, job: Job) -> str | None:
    """Warum ein wartender Auftrag (noch) nicht startet – ohne etwas zu verändern. None = nichts aus der Abrechnung."""
    if not an(db) or job.type not in KOSTENPFLICHTIG or job.state != "queued":
        return None
    if db.get(KostenAuftrag, job.id) is not None:
        return None  # Wiederholung eines gestarteten Auftrags
    g = zahler(db, kampagne_von_auftrag(db, job))
    if g is None:
        return "zahler_fehlt"
    geschaetzt = schaetzung(db, job)
    if job.kosten_freigegeben_at is None and geschaetzt > g.freigabe_ab_cent:
        return "freigabe"
    s = saldo(db, g.id)
    if s <= 0:
        return "leer"
    if s - geschaetzt < -preise(db).deckel:
        return "zu_knapp"
    return None


def starten(db: Session, job: Job, weg: str) -> str | None:
    """Vor der Vergabe eines Auftrags aufrufen (in derselben Transaktion wie die Vergabe). None = darf starten; dann
    ist der Kostenauftrag vorgemerkt und wird mit der Vergabe festgeschrieben. Sonst der Grund – und es ist nichts
    verändert (Rollback)."""
    if not an(db) or job.type not in KOSTENPFLICHTIG:
        return None
    if db.get(KostenAuftrag, job.id) is not None:
        return None  # automatische Wiederholung: gehört zum selben Auftrag, schon vorgemerkt
    c = kampagne_von_auftrag(db, job)
    g = zahler(db, c, anlegen=True)
    if g is None:
        db.rollback()
        return "zahler_fehlt"
    weg_preis = "worker" if weg == "worker" else "cloud"
    m = menge(db, job)
    geschaetzt = betrag_fuer(db, job.type, weg_preis, m)
    if job.kosten_freigegeben_at is None and max(geschaetzt, schaetzung(db, job)) > g.freigabe_ab_cent:
        db.rollback()
        return "freigabe"
    # Erst die Zeile anlegen (Schreibsperre der Datenbank, Teilindex: höchstens einer je Guthaben), dann den Saldo
    # innerhalb dieser Transaktion lesen – ein zweiter Start für dasselbe Guthaben scheitert am Index.
    db.add(KostenAuftrag(job_id=job.id, guthaben_id=g.id, weg=weg, art=job.type, menge=m,
                         schaetzung_cent=geschaetzt, zustand="laeuft", campaign_id=c.id if c else None,
                         session_id=job.session_id, document_id=job.document_id, analysis_id=job.analysis_id))
    try:
        db.flush()
    except IntegrityError:
        db.rollback()
        return "laeuft_schon"
    s = saldo(db, g.id)
    if s <= 0:
        db.rollback()
        return "leer"
    if s - geschaetzt < -preise(db).deckel:
        db.rollback()
        return "zu_knapp"
    return None


# ---------------------------------------------------------------- Abrechnen
def _cloud_kosten(db: Session, k: KostenAuftrag, job: Job | None) -> int:
    """Tatsächliche Cloud-Kosten aus dem Verbrauchsprotokoll. Das Protokoll bleibt beim Löschen von Runde oder
    Unterlage erhalten; der Bezug steht seit 0.4.87 am Kostenauftrag selbst."""
    document_id = k.document_id or (job.document_id if job is not None else None)
    session_id = k.session_id or (job.session_id if job is not None else None)
    analysis_id = k.analysis_id or (job.analysis_id if job is not None else None)
    if not document_id and not session_id and not analysis_id:
        return 0
    q = select(func.coalesce(func.sum(UsageLog.cost_cents), 0)).where(
        UsageLog.engine == "external", UsageLog.kind == NUTZUNG[k.art], UsageLog.created_at >= k.angelegt)
    if analysis_id:  # 0.4.18
        q = q.where(UsageLog.analysis_id == analysis_id)
    elif document_id:
        q = q.where(UsageLog.document_id == document_id)
    else:
        q = q.where(UsageLog.session_id == session_id)
    return int(db.scalar(q) or 0)


def abrechnen(db: Session, k: KostenAuftrag, _schreiben: bool = True) -> int:
    """Einen Kostenauftrag buchen (in der laufenden Transaktion). Gibt den Betrag zurück.

    Worker: endgültig gescheitert kostet nichts. Fertig – oder gelöscht, bevor er fertig war (Runde, Unterlage oder
    Kampagne weg) – kostet den vollen Betrag; die Arbeit ist dann getan bzw. begonnen."""
    job = db.get(Job, k.job_id)
    # 0.4.89: „superseded“ (neu transkribiert, abgelöst) ist kein Scheitern – die Arbeit war begonnen
    gescheitert = job is not None and job.state == "failed" and job.error_code != "superseded"
    if k.weg == "worker":
        betrag = 0 if gescheitert else betrag_fuer(db, k.art, "worker", k.menge)
    else:  # Cloud: tatsächliche Kosten (auch bei Fehlschlag, soweit angefallen); Testmodus kostet nichts
        betrag = _mit_aufschlag(db, _cloud_kosten(db, k, job))
        if betrag == 0 and (job is None or job.state == "failed"):
            # 0.4.89: gelöscht, abgelöst oder gescheitert, ehe ein Verbrauch protokolliert war – mindestens die
            # Schätzung vom Start (der Anbieter hat ggf. schon gerechnet)
            betrag = max(0, k.schaetzung_cent or 0)
    jetzt = utcnow()
    # 0.4.89: nur einmal buchen – auch wenn zwei Prozesse denselben Kostenauftrag zugleich abrechnen
    res = db.execute(update(KostenAuftrag).where(KostenAuftrag.job_id == k.job_id, KostenAuftrag.zustand == "laeuft")
                     .values(zustand="abgerechnet", betrag_cent=betrag, abgerechnet_at=jetzt)
                     .execution_options(synchronize_session=False))
    for feld, wert in (("zustand", "abgerechnet"), ("betrag_cent", betrag if res.rowcount == 1 else k.betrag_cent),
                       ("abgerechnet_at", jetzt if res.rowcount == 1 else k.abgerechnet_at)):
        set_committed_value(k, feld, wert)
    if res.rowcount != 1:
        return 0
    g = db.get(Guthaben, k.guthaben_id)
    if betrag > 0:
        buchen(db, g, "verbrauch", -betrag, job_id=k.job_id, _schreiben=_schreiben)
    return betrag


def vor_loeschen(db: Session, *, campaign_id: str | None = None, session_id: str | None = None,
                 document_id: str | None = None) -> int:
    """0.4.87: Vor dem Löschen einer Kampagne, Runde oder Unterlage deren laufende Kostenaufträge buchen – sonst
    verschwände der Auftrag und mit ihm der Bezug. Gilt unabhängig vom Schalter (gestartet ist gestartet)."""
    bedingung = []
    if campaign_id:
        runden = select(GameSession.id).where(GameSession.campaign_id == campaign_id)
        unterlagen = select(CampaignDocument.id).where(CampaignDocument.campaign_id == campaign_id)
        bedingung += [KostenAuftrag.campaign_id == campaign_id,
                      KostenAuftrag.job_id.in_(select(Job.id).where(Job.session_id.in_(runden))),
                      KostenAuftrag.job_id.in_(select(Job.id).where(Job.document_id.in_(unterlagen)))]
    if session_id:
        bedingung += [KostenAuftrag.session_id == session_id,
                      KostenAuftrag.job_id.in_(select(Job.id).where(Job.session_id == session_id))]
    if document_id:
        bedingung += [KostenAuftrag.document_id == document_id,
                      KostenAuftrag.job_id.in_(select(Job.id).where(Job.document_id == document_id))]
    if not bedingung:
        return 0
    from sqlalchemy import or_

    anzahl = 0
    for k in list(db.scalars(select(KostenAuftrag).where(KostenAuftrag.zustand == "laeuft", or_(*bedingung)))):
        abrechnen(db, k)
        anzahl += 1
    return anzahl


def offene_abrechnen(db: Session) -> int:
    """Sicherheitsnetz: alle laufenden Kostenaufträge buchen, deren Auftrag fertig, gescheitert oder gelöscht ist.
    Seit 0.4.87 wird schon beim Abschluss gebucht (siehe unten); das hier fängt Übriggebliebenes."""
    anzahl = 0
    for k in list(db.scalars(select(KostenAuftrag).where(KostenAuftrag.zustand == "laeuft"))):
        job = db.get(Job, k.job_id)
        if job is None or job.state in ("done", "failed"):
            abrechnen(db, k)
            anzahl += 1
    if anzahl:
        db.commit()
    return anzahl


# ---------------------------------------------------------------- Gutschrift von Hand und Gutscheine
def gutschreiben(db: Session, g: Guthaben, betrag_cent: int, grund: str, von: str) -> Buchung:
    if betrag_cent <= 0 or betrag_cent > preise(db).gutschrift_max:
        raise ValueError("betrag")
    from app.aliasse import zeichen_bereinigen

    grund = zeichen_bereinigen(grund)[:500].strip()  # G17: die SL sieht den Grund in der App
    if not grund:
        raise ValueError("grund")
    b = buchen(db, g, "gutschrift", betrag_cent, von=von, grund=grund)
    protokoll(db, von, "gutschrift", g.id, betrag_cent=betrag_cent, grund=grund)
    return b


def stornieren(db: Session, buchung_id: str, von: str) -> Buchung:
    """Gegenbuchung zu einer Gutschrift von Hand (höchstens einmal – eindeutiger Bezug in der Datenbank)."""
    b = db.get(Buchung, buchung_id)
    if b is None or b.art != "gutschrift":
        raise ValueError("buchung")
    g = db.get(Guthaben, b.guthaben_id)
    try:
        gegen = buchen(db, g, "korrektur", -b.betrag_cent, von=von, gegen_buchung_id=b.id,
                       grund=f"Storno der Gutschrift vom {b.angelegt:%d.%m.%Y}")
    except IntegrityError:
        db.rollback()
        raise ValueError("schon_storniert") from None
    protokoll(db, von, "storno", g.id, buchung=b.id, betrag_cent=b.betrag_cent)
    return gegen


_CODE_ZEICHEN = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 32 Zeichen = 5 Bit je Zeichen, 16 Zeichen = 80 Bit


def _code_hash(code: str) -> str:
    norm = "".join(c for c in (code or "").upper() if c.isalnum())
    return hashlib.sha256(b"taleward-gutschein:" + norm.encode()).hexdigest()


def gutscheine_erzeugen(db: Session, anzahl: int, betrag_cent: int, aktion: str, gueltig_bis: datetime | None,
                        von: str) -> list[str]:
    """Codes nur jetzt im Klartext; gespeichert wird nur ihr Hash."""
    aktion = " ".join((aktion or "").split())[:100]
    if not aktion or not 1 <= anzahl <= 100 or betrag_cent <= 0 or betrag_cent > preise(db).gutschrift_max:
        raise ValueError("eingabe")
    codes = []
    for _ in range(anzahl):
        roh = "".join(secrets.choice(_CODE_ZEICHEN) for _ in range(16))
        code = "-".join(roh[i:i + 4] for i in range(0, 16, 4))
        db.add(Gutschein(code_hash=_code_hash(code), aktion=aktion, betrag_cent=betrag_cent, gueltig_bis=gueltig_bis,
                         angelegt_von=von))
        codes.append(code)
    protokoll(db, von, "gutscheine", anzahl=anzahl, betrag_cent=betrag_cent, aktion=aktion,
              gueltig_bis=gueltig_bis.isoformat() if gueltig_bis else None)
    return codes


def gutschein_einloesen(db: Session, g: Guthaben, code: str, von: str) -> int:
    """Atomar und nur einmal; je Guthaben höchstens ein Gutschein derselben Aktion. Wirft ValueError (für die Meldung
    immer gleich – ob es den Code gibt, verrät die Antwort nicht)."""
    from sqlalchemy import update

    gs = db.scalar(select(Gutschein).where(Gutschein.code_hash == _code_hash(code)))
    jetzt = utcnow()
    if gs is None or gs.eingeloest_at is not None or (gs.gueltig_bis is not None and gs.gueltig_bis < jetzt):
        raise ValueError("ungueltig")
    schon = db.scalar(select(func.count()).select_from(Gutschein).where(
        Gutschein.aktion == gs.aktion, Gutschein.eingeloest_von == g.id))
    if schon:
        raise ValueError("aktion")
    try:
        res = db.execute(update(Gutschein).where(Gutschein.id == gs.id, Gutschein.eingeloest_at.is_(None))
                         .values(eingeloest_von=g.id, eingeloest_at=jetzt))
    except IntegrityError:  # 0.4.87: gleichzeitig ein anderer Code derselben Aktion – der Index lässt nur einen zu
        db.rollback()
        raise ValueError("aktion") from None
    if res.rowcount != 1:
        db.rollback()
        raise ValueError("ungueltig")
    buchen(db, g, "gutschein", gs.betrag_cent, von=von, gutschein_id=gs.id)
    return gs.betrag_cent


# ---------------------------------------------------------------- Hinweis für die App (ProcessingStatus.message)
def hinweis(db: Session, job: Job | None, lang: str) -> str | None:
    """Text, warum ein wartender Auftrag wegen der Abrechnung nicht startet – ohne Saldo (nur die SL sieht wartende
    Runden und Unterlagen). None = kein Grund aus der Abrechnung."""
    from app import errors

    if job is None:
        return None
    grund = warte_grund(db, job)
    if grund is None:
        return None
    topf = modus(db) == "topf" and bool(getattr(kampagne_von_auftrag(db, job), "organization_id", None))
    schluessel = {"zahler_fehlt": "status.credit_no_payer", "freigabe": "status.credit_approval",
                  "leer": "status.credit_pot_empty" if topf else "status.credit_empty",
                  "zu_knapp": "status.credit_pot_empty" if topf else "status.credit_low"}[grund]
    return errors.ApiError(0, schluessel, betrag=euro(schaetzung(db, job))).message(lang)


# ---------------------------------------------------------------- Für Seiten
def buchungen(db: Session, g: Guthaben, anzahl: int = 100, vor: Buchung | None = None) -> list[Buchung]:
    """Neueste zuerst. vor: nur Buchungen, die vor dieser stehen (Weiterblättern)."""
    from sqlalchemy import and_, or_

    q = select(Buchung).where(Buchung.guthaben_id == g.id)
    if vor is not None:
        q = q.where(or_(Buchung.angelegt < vor.angelegt, and_(Buchung.angelegt == vor.angelegt, Buchung.id < vor.id)))
    return list(db.scalars(q.order_by(Buchung.angelegt.desc(), Buchung.id.desc()).limit(anzahl)))


def ist_aktive_sl(db: Session, user_id: str, c: Campaign | None) -> bool:
    """G14: Titel von Runden, Unterlagen und Auswertungen in Guthaben-Ansichten nur für die heutige SL der Kampagne."""
    return c is not None and user_id in {m.user_id for m in aktive_sl(db, c)}


def ist_topf(g: Guthaben | None) -> bool:
    return g is not None and g.art == "organisation"


# 0.4.25: Wartegrund als Code für die App (CreditWait.reason)
GRUND_API = {"freigabe": "approval", "leer": "empty", "zu_knapp": "too_low", "zahler_fehlt": "no_payer"}
STAND_API = {"gut": "good", "knapp": "low", "leer": "empty", "minus": "negative"}


def warten_fuer(db: Session, job: Job | None, user_id: str) -> dict | None:
    """CreditWait für eine SL der Kampagne (Spieler bekommen None, das prüft der Aufrufer). Betrag, Guthaben und
    Auftrag nur für Personen, die das zahlende Guthaben sehen dürfen; Topf ohne Verwalterrecht → pot."""
    if job is None or not an(db):
        return None
    grund = warte_grund(db, job)
    if grund is None:
        return None
    c = kampagne_von_auftrag(db, job)
    g = zahler(db, c)
    sieht = g is not None and darf_sehen(db, user_id, g)
    if sieht and g.id is None:  # noch nie gebucht: Zeile anlegen, damit „Verarbeiten“ eine Kennung hat
        g = zahler(db, c, anlegen=True)
        db.commit()
    if ist_topf(g) and not sieht:
        return {"reason": "pot", "estimate_cents": None, "credit_id": None, "job_id": None}
    if not sieht:
        return {"reason": GRUND_API[grund], "estimate_cents": None, "credit_id": None, "job_id": None}
    return {"reason": GRUND_API[grund], "estimate_cents": schaetzung(db, job), "credit_id": g.id, "job_id": job.id}


def wartende_auftraege(db: Session, g: Guthaben) -> list[dict]:
    """Wartende kostenpflichtige Aufträge, die dieses Guthaben bezahlen würde, mit Schätzung und Grund."""
    aus = []
    for job in db.scalars(select(Job).where(Job.state == "queued", Job.type.in_(KOSTENPFLICHTIG))
                          .order_by(Job.created_at).limit(500)):
        c = kampagne_von_auftrag(db, job)
        z = zahler(db, c)
        if z is None or z.id is None or z.id != g.id or db.get(KostenAuftrag, job.id) is not None:
            continue
        s = db.get(GameSession, job.session_id) if job.session_id else None
        doc = db.get(CampaignDocument, job.document_id) if job.document_id else None
        from app.charakterauswertung import fuer_auftrag

        aus.append({"job": job, "kampagne": c, "runde": s, "unterlage": doc, "auswertung": fuer_auftrag(db, job),
                    "schaetzung": schaetzung(db, job), "grund": warte_grund(db, job)})
    return aus


def freigeben(db: Session, job: Job, user_id: str, g: Guthaben) -> None:
    if job.state != "queued" or job.kosten_freigegeben_at is not None:
        return
    job.kosten_freigegeben_at = utcnow()
    protokoll(db, user_id, "auftrag_freigegeben", g.id, job=job.id, schaetzung_cent=schaetzung(db, job))


def einstellungen_aendern(db: Session, g: Guthaben, von: str, freigabe_ab_cent: int, bogen_seiten: int,
                          boegen_je_spieler: int) -> None:
    neu = {"freigabe_ab_cent": max(0, min(100_000, freigabe_ab_cent)),
           "bogen_seiten": max(1, min(2000, bogen_seiten)),
           "boegen_je_spieler": max(0, min(50, boegen_je_spieler))}
    alt = {k: getattr(g, k) for k in neu}
    if alt == neu:
        return
    for k, v in neu.items():
        setattr(g, k, v)
    protokoll(db, von, "einstellungen", g.id, alt=alt, neu=neu)


def bogen_grenzen(db: Session, c: Campaign) -> tuple[int, int] | None:
    """(Seiten je Bogen, Bögen je Spieler) für Charakterbögen von Spielern – None bei ausgeschalteter Abrechnung."""
    if not an(db):
        return None
    g = zahler(db, c)
    return (g.bogen_seiten, g.boegen_je_spieler) if g is not None else (20, 3)


def euro_genau(cent: float) -> str:
    """Kleine Schätzwerte (unter einem Cent je Seite) lesbar: drei Nachkommastellen unter 1 €, sonst zwei."""
    if cent <= 0:
        return "–"
    euro_ = cent / 100
    return (f"{euro_:.3f}" if euro_ < 1 else f"{euro_:.2f}").replace(".", ",") + " €"


def euro(cent: int) -> str:
    vorzeichen = "−" if cent < 0 else ""
    cent = abs(int(cent))
    return f"{vorzeichen}{cent // 100},{cent % 100:02d} €"


# ---------------------------------------------------------------- Abrechnen beim Abschluss (0.4.87)
# Aufträge werden an vielen Stellen beendet (Ergebnis eines Workers, Zentrale, Cloud, endgültiges Scheitern). Statt
# jede Stelle einzeln anzupassen, bucht ein Datenbank-Ereignis in derselben Transaktion, in der ein Auftrag „done“ oder
# „failed“ wird: vor dem Schreiben merken, nach dem Schreiben buchen (dann sind auch Verbrauchsprotokolle derselben
# Transaktion in der Datenbank). Das Commit schreibt die Buchung mit.
from sqlalchemy import event as _event  # noqa: E402
from sqlalchemy import inspect as _inspect  # noqa: E402
from sqlalchemy.orm import Session as _Sitzung  # noqa: E402

_MERKER = "abrechnung.beendet"


@_event.listens_for(_Sitzung, "before_flush")
def _beendete_merken(session, _flush, _objekte) -> None:
    ids = {o.id for o in session.dirty if isinstance(o, Job) and o.state in ("done", "failed")
           and _inspect(o).attrs.state.history.has_changes()}
    if ids:
        session.info.setdefault(_MERKER, set()).update(ids)


@_event.listens_for(_Sitzung, "after_flush_postexec")
def _beendete_buchen(session, _flush) -> None:
    ids = session.info.pop(_MERKER, None)
    if not ids:
        return
    with session.no_autoflush:
        for job_id in ids:
            k = session.get(KostenAuftrag, job_id)
            if k is not None and k.zustand == "laeuft":
                abrechnen(session, k, _schreiben=False)


@_event.listens_for(_Sitzung, "after_rollback")
def _merker_leeren(session) -> None:
    session.info.pop(_MERKER, None)
