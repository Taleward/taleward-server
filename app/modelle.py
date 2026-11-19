"""Feste Modellfassungen für den Worker.

Die KI-Pakete sind in uv.lock festgelegt. Die Modelldateien kommen von Hugging Face. Ohne Festlegung fragen
whisperx und pyannote bei jedem Laden nach einer neueren Fassung und übernehmen sie still. Das darf nicht sein:
Stimmabdrücke passen nur zu genau der Fassung des Sprechermodells, mit der sie entstanden sind.

Welche Fassung gilt:
1. Die Fassung hier im Code (FASSUNGEN). Sie gilt für alle Installationen und ändert sich nur mit einem geprüften
   Server-Update.
2. Sonst die Fassung, die dieser Worker beim ersten Laden gemerkt hat (data/modelle.json).
3. Sonst einmalig die vorhandene bzw. aktuelle Fassung nehmen und merken.

Geladen wird danach aus dem lokalen Ordner, ohne Nachfrage bei Hugging Face.

Das Sprechermodell holt der Worker vom Server (app/modellablage.py), sobald es dort liegt: ohne eigenes
Hugging-Face-Konto. Nur wenn der Server es (noch) nicht hat, lädt der Worker selbst von Hugging Face.

Für das Sprechermodell gilt zusätzlich eine feste Liste (FEST): genau eine Fassung und die SHA-256 jeder ihrer Dateien.
Seine Dateien werden beim Laden ausgeführt, darum nimmt der Worker nichts anderes an, egal woher es kommt (Server,
Hugging Face, schon vorhandener Ordner), und prüft vor jedem Laden noch einmal. Eine neue Fassung des Sprechermodells
kommt nur mit einem geprüften Update dieser Datei.
Nicht betroffen: das deutsche und englische Ausrichtungsmodell (torchaudio) und die Spracherkennung (VAD) –
beide hängen fest an den Paketversionen.
"""
from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

log = logging.getLogger(__name__)

SPRECHERMODELL = "pyannote/speaker-diarization-community-1"
SPRACHBAUSTEIN = "nltk/punkt_tab"  # Satzmodelle für die Ausrichtung (0.4.140, app/modellablage.py)

# Hugging-Face-Kennung → Commit. Eingetragen wird nur, was auf echter Hardware geprüft ist.
FASSUNGEN: dict[str, str] = {}

# Sprechermodell: Fassung und je Datei (Größe, SHA-256). Nichts außerhalb dieser Liste wird geladen.
FEST: dict[str, tuple[str, dict[str, tuple[int, str]]]] = {
    SPRECHERMODELL: ("3533c8cf8e369892e6b79ff1bf80f7b0286a54ee", {
        ".gitattributes": (1571, "5ca5d721264b62c84e55b912e7cdb019439cad30d41df8aeea3a23f9b1f86ef3"),
        "README.md": (9978, "2db91f9265bd81f1653ff088b5bff22bf6aebebea03328513af65501643f8a31"),
        "config.yaml": (444, "5ce2bfa9a938dc132cec1172592d65173cbb8f444ea1e4133f10f9391de155be"),
        "diarization.gif": (861445, "0d925ad38995d89009260e493b0ae2e684c3e1397f495265ed841c45c4f73a35"),
        "embedding/README.md": (938, "fa9e5105ae95edb231d841476cdb91eef4be0621c372ed4f7d3421294b5f8ad7"),
        "embedding/pytorch_model.bin": (26646242, "6f10ff60898a1d185fa22e1d11e0bfa8a92efec811f11bca48cb8cafebefd929"),
        "plda/README.md": (220, "e1316dbbeb3261431478d48ceebbd4bba395c3587e7b80c254dbab00f1209d0a"),
        "plda/plda.npz": (133852, "9b77bcd840692710dd3496f62ecfeed8d8e5f002fd991b785079b244eab7d255"),
        "plda/xvec_transform.npz": (134376, "325f1ce8e48f7e55e9c8aa47e05d2766b7c48c4b25b8de8dd751e7a4cc5fbe8f"),
        "segmentation/pytorch_model.bin": (5906507, "7ad24338d844fb95985486eb1a464e32d229f6d7a03c9abe60f978bacf3f816e"),
    }),
}
# Dateien, die Taleward selbst in einen Modellordner legt (kein Teil des Modells, nie geladen)
EIGENE_DATEIEN = {".vollstaendig", "NOTICE.txt", "manifest.json"}
MAX_MODELLDATEI = 600 * 1024 ** 2  # Obergrenze je Datei für Modelle ohne feste Liste

# Was im lokalen Ordner liegen muss, damit er als vollständig gilt
PRUEFDATEI = {SPRECHERMODELL: "config.yaml"}
WHISPER_DATEIEN = ["config.json", "preprocessor_config.json", "model.bin", "tokenizer.json", "vocabulary.*"]


class ModellFehler(Exception):
    """Modell nicht ladbar – verständliche Meldung."""


@dataclass
class Bereit:
    repo: str
    fassung: str | None  # Commit; None bei einem eigenen Ordner
    pfad: Path
    quelle: str  # festgelegt | gemerkt | neu | ordner

    @property
    def kennung(self) -> str:
        return f"{self.repo}@{self.fassung[:12]}" if self.fassung else self.repo


def feste_fassung(repo: str) -> str | None:
    """Die einzige Fassung, die für dieses Modell gilt (FEST, sonst FASSUNGEN)."""
    return FEST[repo][0] if repo in FEST else FASSUNGEN.get(repo)


def _summe(datei: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with datei.open("rb") as f:
        for block in iter(lambda: f.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def ordner_pruefen(repo: str, ordner: Path) -> None:
    """Für Modelle mit fester Liste: Im Ordner liegt jede Datei der Liste mit genau ihrer Prüfsumme und sonst nichts.
    Sonst ModellFehler – bevor irgendetwas daraus geladen wird."""
    if repo not in FEST:
        return
    dateien = FEST[repo][1]
    gefunden = set()
    for p in sorted(ordner.rglob("*")):
        if p.is_dir():
            continue
        name = p.relative_to(ordner).as_posix()
        if name in EIGENE_DATEIEN:
            continue
        if name not in dateien:
            raise ModellFehler(f"Modell {repo}: unerwartete Datei „{name[:100]}“ im Modellordner. "
                               "Das Modell wird nicht geladen.")
        gefunden.add(name)
    fehlt = sorted(set(dateien) - gefunden)
    if fehlt:
        raise ModellFehler(f"Modell {repo}: Im Modellordner fehlt „{fehlt[0]}“.")
    for name, (groesse, summe) in dateien.items():
        datei = ordner / name
        if datei.stat().st_size != groesse or _summe(datei) != summe:
            raise ModellFehler(f"Modell {repo}: Die Datei „{name}“ ist nicht die geprüfte Fassung. "
                               "Das Modell wird nicht geladen.")


def merkdatei() -> Path:
    from app.config import get_settings

    return get_settings().data_dir / "modelle.json"


def gemerkt() -> dict[str, str]:
    try:
        daten = json.loads(merkdatei().read_text(encoding="utf-8"))
        return {k: v for k, v in daten.items() if isinstance(v, str)}
    except (OSError, ValueError):
        return {}


def _merken(repo: str, fassung: str) -> None:
    daten = gemerkt()
    if daten.get(repo) == fassung:
        return
    daten[repo] = fassung
    datei = merkdatei()
    datei.parent.mkdir(parents=True, exist_ok=True)
    tmp = datei.with_suffix(".tmp")
    tmp.write_text(json.dumps(daten, indent=2, sort_keys=True), encoding="utf-8", newline="\n")
    tmp.replace(datei)


def whisper_repo(name: str) -> str:
    """„large-v3“ → „Systran/faster-whisper-large-v3“ (gleiche Zuordnung wie faster-whisper)."""
    try:
        from faster_whisper.utils import _MODELS
    except ImportError:  # ohne KI-Pakete (Tests)
        _MODELS = {"large-v3": "Systran/faster-whisper-large-v3"}
    return _MODELS.get(name, name)


def _vollstaendig(pfad: Path, repo: str) -> bool:
    pruef = PRUEFDATEI.get(repo, "model.bin" if "whisper" in repo else None)
    return pfad.is_dir() and (pruef is None or (pfad / pruef).exists())


def _laden(repo: str, fassung: str | None, token: str | None, nur_lokal: bool) -> Path:
    from huggingface_hub import snapshot_download

    muster = WHISPER_DATEIEN if "whisper" in repo else (sorted(FEST[repo][1]) if repo in FEST else None)
    return Path(snapshot_download(repo, revision=fassung, token=token, local_files_only=nur_lokal,
                                  allow_patterns=muster))


def bereitstellen(repo_oder_ordner: str, token: str | None = None) -> Bereit:
    """Pfad zur festen Fassung eines Modells; lädt nur, wenn sie noch nicht auf diesem Worker liegt."""
    eigener = Path(repo_oder_ordner).expanduser()
    if eigener.is_dir():
        return Bereit(repo_oder_ordner, None, eigener, "ordner")
    repo = repo_oder_ordner
    soll = feste_fassung(repo)
    quelle = "festgelegt"
    if soll is None:
        soll, quelle = gemerkt().get(repo), "gemerkt"
    pfad = None
    try:  # zuerst lokal – ohne Netz; ohne Fassung: das, was schon im Zwischenspeicher liegt
        pfad = _laden(repo, soll, token, nur_lokal=True)
        if not _vollstaendig(pfad, repo):
            pfad = None
    except Exception:  # noqa: BLE001 – nicht vorhanden → laden
        pfad = None
    if pfad is None:
        log.info("Lade Modell %s%s (einmalig, kann dauern) …", repo, f" in Fassung {soll[:12]}" if soll else "")
        try:
            pfad = _laden(repo, soll, token, nur_lokal=False)
        except Exception as e:  # noqa: BLE001
            raise ModellFehler(_meldung(repo, e)) from e
    fassung = pfad.name  # Ordnername im Zwischenspeicher = Commit
    if soll is None:
        quelle = "neu"
    elif fassung != soll:
        raise ModellFehler(f"Modell {repo}: erwartet Fassung {soll[:12]}, gefunden {fassung[:12]}.")
    ordner_pruefen(repo, pfad)  # feste Liste: auch der eigene Abruf und der vorhandene Zwischenspeicher
    _merken(repo, fassung)
    return Bereit(repo, fassung, pfad, quelle)


# ---------------------------------------------------------------- Vom Server (Worker-Protokoll)
class ServerQuelle:
    """Lädt Modelle, die der Server verteilt (GET /worker/v1/models…), mit dem Worker-Token."""

    def __init__(self, server: str, token: str, klient=None):
        import httpx

        self.server = server.rstrip("/")
        self.kopf = {"Authorization": f"Bearer {token}"}
        self.klient = klient or httpx.Client(timeout=httpx.Timeout(30, read=300))

    def verzeichnis(self, repo: str) -> dict | None:
        r = self.klient.get(f"{self.server}/worker/v1/models", params={"repo": repo}, headers=self.kopf)
        if r.status_code == 404:
            return None
        if r.status_code != 200:
            raise ModellFehler(f"Server liefert das Modell {repo} nicht ({r.status_code}).")
        return r.json()

    def laden(self, repo: str, fassung: str, pfad: str, ziel: Path, hoechstens: int = MAX_MODELLDATEI) -> str:
        """Datei laden, höchstens `hoechstens` Bytes (mehr → Abbruch, die Datei wird entfernt)."""
        import hashlib

        ziel.parent.mkdir(parents=True, exist_ok=True)
        h, n = hashlib.sha256(), 0
        with self.klient.stream("GET", f"{self.server}/worker/v1/models/file", headers=self.kopf,
                                params={"repo": repo, "fassung": fassung, "pfad": pfad}) as r:
            if r.status_code != 200:
                raise ModellFehler(f"Modelldatei {pfad} nicht vom Server ladbar ({r.status_code}).")
            with ziel.open("wb") as f:
                for block in r.iter_bytes(1024 * 1024):
                    n += len(block)
                    if n > hoechstens:
                        break
                    h.update(block)
                    f.write(block)
        if n > hoechstens:
            ziel.unlink(missing_ok=True)
            raise ModellFehler(f"Modelldatei {pfad} ist größer als erwartet – abgebrochen.")
        return h.hexdigest()


_COMMIT = re.compile(r"^[0-9a-f]{40}$")
_PFADZEICHEN = re.compile(r"^[A-Za-z0-9._/-]+$")


def commit_ok(fassung) -> bool:
    """Eine Fassung ist ein 40-stelliger Git-Commit – nie ein Name wie „main“ oder ein Pfad."""
    return isinstance(fassung, str) and bool(_COMMIT.match(fassung))


def _sicher(pfad) -> bool:
    """Relativer Pfad innerhalb des Modellordners – unter Linux und Windows (keine Laufwerke, kein „..“)."""
    from pathlib import PurePosixPath, PureWindowsPath

    if not isinstance(pfad, str) or not _PFADZEICHEN.match(pfad):
        return False
    w = PureWindowsPath(pfad)
    if PurePosixPath(pfad).is_absolute() or w.is_absolute() or w.drive or w.anchor:
        return False
    return all(t not in ("", ".", "..") for t in pfad.split("/"))


def _innerhalb(wurzel: Path, ziel: Path) -> bool:
    try:
        ziel.resolve().relative_to(wurzel.resolve())
        return True
    except ValueError:
        return False


def vom_server(repo: str, quelle: ServerQuelle) -> Bereit | None:
    """Feste Fassung des Servers bereitstellen (aus dem lokalen Zwischenspeicher oder frisch geladen).
    None, wenn der Server das Modell nicht hat."""
    import shutil

    from app.config import get_settings

    try:
        v = quelle.verzeichnis(repo)
    except ModellFehler:
        raise
    except Exception as e:  # noqa: BLE001 – Netz
        # Server gerade nicht erreichbar (WLAN beim Anmelden noch nicht da, Server kurz weg): liegt die zuletzt
        # gemerkte Fassung vollständig hier, arbeiten wir damit weiter – sonst müsste der Worker 10 Minuten warten.
        vorhanden = _vorhanden(repo)
        if vorhanden is not None:
            log.warning("Server nicht erreichbar (%s) – nehme das gemerkte Modell %s@%s",
                        type(e).__name__, repo, vorhanden.fassung[:12])
            return vorhanden
        raise ModellFehler(f"Server nicht erreichbar, um das Modell {repo} zu laden ({type(e).__name__}).") from e
    if v is None:
        return None
    fassung = v.get("fassung")
    if not commit_ok(fassung):
        raise ModellFehler(f"Modell {repo}: Der Server nennt keine gültige Fassung.")
    fest = feste_fassung(repo)
    if fest and fest != fassung:
        raise ModellFehler(f"Modell {repo}: Der Server hat Fassung {fassung[:12]}, dieser Worker nimmt nur die "
                           f"geprüfte Fassung {fest[:12]}. Server und Worker auf dieselbe Version aktualisieren; "
                           "danach in der Verwaltung unter Transkription das Sprechermodell neu laden.")
    liste = FEST[repo][1] if repo in FEST else None
    angebot = v.get("dateien") if isinstance(v.get("dateien"), list) else []
    if liste is not None:  # feste Liste: nichts außerhalb, nichts fehlt – geprüft, bevor etwas geladen wird
        namen = [d.get("pfad") if isinstance(d, dict) else None for d in angebot]
        fremd = [n for n in namen if n not in liste]
        if fremd or set(namen) != set(liste):
            raise ModellFehler(f"Modell {repo}: Der Server bietet nicht genau die geprüften Dateien an"
                               + (f" (unerwartet: {str(fremd[0])[:100]})" if fremd else "") + ". Nichts wurde geladen.")
    ordner = get_settings().data_dir / "modelle" / repo.replace("/", "__") / fassung
    fertig = ordner / ".vollstaendig"
    if fertig.exists() and liste is not None:
        try:  # vorhandener Ordner: vor jedem Laden gegen die Liste, Abweichendes verwerfen und neu holen
            ordner_pruefen(repo, ordner)
        except ModellFehler as e:
            log.warning("%s Der Ordner wird verworfen und neu geladen.", e)
            shutil.rmtree(ordner, ignore_errors=True)
    if not fertig.exists():
        log.info("Lade Modell %s@%s vom Server (einmalig) …", repo, fassung[:12])
        tmp = ordner.with_name(ordner.name + ".laden")
        shutil.rmtree(tmp, ignore_errors=True)
        try:
            for d in angebot:
                if not _sicher(d.get("pfad")) or not _innerhalb(tmp, tmp / d["pfad"]):
                    raise ModellFehler(f"Unerwarteter Pfad im Modellverzeichnis: {str(d.get('pfad'))[:100]}")
                # feste Liste: Größe und Prüfsumme aus dem Code, nie aus der Antwort des Servers
                groesse, soll = liste[d["pfad"]] if liste is not None else (MAX_MODELLDATEI, d["sha256"])
                if quelle.laden(repo, fassung, d["pfad"], tmp / d["pfad"], groesse) != soll:
                    raise ModellFehler(f"Prüfsumme stimmt nicht: {d['pfad']} – bitte erneut versuchen.")
            ordner_pruefen(repo, tmp)
            (tmp / ".vollstaendig").write_text(fassung, encoding="utf-8", newline="\n")
            shutil.rmtree(ordner, ignore_errors=True)
            tmp.rename(ordner)
        except BaseException:
            shutil.rmtree(tmp, ignore_errors=True)
            raise
    _merken(repo, fassung)
    return Bereit(repo, fassung, ordner, "server")


def _vorhanden(repo: str) -> Bereit | None:
    """Die gemerkte Fassung eines Servermodells, wenn sie vollständig auf der Platte liegt."""
    from app.config import get_settings

    fassung = gemerkt().get(repo)
    if not commit_ok(fassung):
        return None
    ordner = get_settings().data_dir / "modelle" / repo.replace("/", "__") / fassung
    if not (ordner / ".vollstaendig").exists():
        return None
    if repo in FEST:
        if fassung != FEST[repo][0]:
            return None
        try:
            ordner_pruefen(repo, ordner)
        except ModellFehler as e:
            log.warning("%s", e)
            return None
    return Bereit(repo, fassung, ordner, "gemerkt")


def _meldung(repo: str, e: Exception) -> str:
    text = f"{type(e).__name__}: {e}"
    if any(w in text for w in ("401", "403", "Gated", "gated", "Unauthorized")):
        return (f"Kein Zugang zu {repo} bei Hugging Face. HF_TOKEN in der .env prüfen und die Modellbedingungen "
                "auf der Modellseite annehmen (docs/BETRIEB.md, „Transkription“).")
    if any(w in text for w in ("Connection", "Timeout", "resolve", "Offline", "offline")):
        return f"Modell {repo} liegt noch nicht auf diesem Computer, und Hugging Face ist nicht erreichbar."
    return f"Modell {repo} konnte nicht geladen werden ({text[:300]})."


def passt(a: str | None, b: str | None) -> bool:
    """Passen zwei Stimmabdrücke zusammen? Gleiches Modell; die Fassung zählt nur, wenn beide sie kennen
    (Abdrücke von vor der Festlegung haben keine – sie stammen praktisch immer aus derselben Fassung)."""
    if not a or not b:
        return True
    ra, _, fa = a.partition("@")
    rb, _, fb = b.partition("@")
    return ra == rb and (not fa or not fb or fa == fb)
