"""Audio-Hilfen für den Worker: Abschnitte dekodieren und lückenlos zusammenfügen, Hörproben schneiden.

Jeder Abschnitt wird einzeln zu 16 kHz mono PCM dekodiert und angehängt (kein „concat -c copy“):
robust bei gemischten Formaten, und die Zeitstempel stimmen exakt. Der Startversatz jedes Abschnitts
wird zurückgegeben – ist ein Abschnitt defekt, nennt die Meldung seine Nummer.
"""
import logging
import shutil
import subprocess
import sys
import wave
from pathlib import Path

SAMPLE_RATE = 16000
log = logging.getLogger("worker")


class AudioFehler(Exception):
    """Fehler mit verständlicher Meldung; `retryable` = lohnt ein neuer Versuch?"""

    def __init__(self, code: str, message: str, retryable: bool = False):
        super().__init__(message)
        self.code, self.retryable = code, retryable


def _ffmpeg() -> str:
    pfad = shutil.which("ffmpeg")
    if not pfad:
        raise AudioFehler("ffmpeg_missing", "ffmpeg ist auf dem Worker nicht installiert.", retryable=True)
    return pfad


def ffmpeg(args: list[str], zeit_s: float, programm: str | None = None) -> subprocess.CompletedProcess:
    """ffmpeg sicher aufrufen: ohne Eingabekanal (sonst wartet es unter der Worker-App, deren Steuerung über stdin
    läuft, auf Tastendrücke und hängt), ohne Konsolenfenster und mit Zeitgrenze statt stummem Hängen."""
    extra = {"creationflags": 0x08000000} if sys.platform == "win32" else {}  # CREATE_NO_WINDOW
    try:
        return subprocess.run([programm or _ffmpeg(), "-nostdin", *args], capture_output=True,
                              stdin=subprocess.DEVNULL, timeout=zeit_s, **extra)
    except subprocess.TimeoutExpired as e:
        raise AudioFehler("audio_timeout", f"ffmpeg hat nach {int(zeit_s)} s nicht geantwortet.", True) from e


def _zeitgrenze(datei: Path) -> float:
    """Großzügig: 10 Minuten plus 1 Minute je 20 MB (eine 4-Stunden-Aufnahme dekodiert in wenigen Minuten)."""
    try:
        mb = datei.stat().st_size / 2**20
    except OSError:
        mb = 0
    return 600 + 3 * mb


# Hochgeladene Dateien nur als Audio öffnen: keine Netz-Protokolle, keine Wiedergabelisten (HLS, concat), die andere
# Dateien oder Adressen nachladen könnten.
FORMATE = "wav,mp3,ogg,flac,mov,mp4,m4a,3gp,3g2,mj2,matroska,webm,aac,amr,caf,aiff,w64,asf"
EINGABE = ["-protocol_whitelist", "file,pipe", "-format_whitelist", FORMATE]


def dekodieren(quelle: Path) -> bytes:
    """Eine Datei zu 16 kHz mono, 16 bit PCM (roh)."""
    # 0.4.94 (M2): immer die erste Tonspur – nie mehr, als `dauer_dekodiert` gemessen hat (die längste Spur)
    res = ffmpeg(["-hide_banner", "-loglevel", "error", *EINGABE, "-i", str(quelle), "-map", "0:a:0",
                  "-ac", "1", "-ar", str(SAMPLE_RATE), "-f", "s16le", "-acodec", "pcm_s16le", "-"],
                 _zeitgrenze(quelle))
    if res.returncode != 0 or not res.stdout:
        raise AudioFehler("audio_unreadable", res.stderr.decode(errors="replace").strip()[-300:] or "keine Audiodaten")
    return res.stdout


def zusammenfuegen(abschnitte: list[Path], ziel: Path) -> list[float]:
    """Abschnitte in Reihenfolge zu einer WAV-Datei. Gibt die Startzeit (s) jedes Abschnitts zurück."""
    starts, pos = [], 0
    log.info("Audio wird zusammengefügt (%d Datei(en)) …", len(abschnitte))
    with wave.open(str(ziel), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(SAMPLE_RATE)
        for nr, datei in enumerate(abschnitte, start=1):
            try:
                pcm = dekodieren(datei)
            except AudioFehler as e:
                raise AudioFehler("audio_unreadable", f"Abschnitt {nr} ({datei.name}) ist nicht lesbar: {e}") from e
            starts.append(pos / SAMPLE_RATE)
            w.writeframes(pcm)
            pos += len(pcm) // 2
    return starts


def dauer(wav: Path) -> float:
    with wave.open(str(wav), "rb") as w:
        return w.getnframes() / w.getframerate()


# Mehr Bytes je Sekunde hat keine übliche Aufnahme (48 kHz, Stereo, 16 bit ≈ 192 kB/s). Meldet der Kopf einer Datei
# eine Länge, die dazu nicht passt, wird sie durch Dekodieren gemessen (0.4.89, M2: präparierter Kopf).
PLAUSIBEL_BYTES_JE_S = 200_000


def dauer_datei(datei: Path, gruendlich: bool = False) -> float | None:
    """Länge einer hochgeladenen Audiodatei in Sekunden. Zuerst laut Kopf (ffprobe, schnell); passt die Länge nicht
    zur Dateigröße, durch Dekodieren gemessen. None = nicht messbar.

    gruendlich (0.4.91, M2): immer dekodieren – der Kopf lässt sich beliebig setzen, auch passend zur Dateigröße (z. B.
    niedrige Bitrate). Gilt, sobald die Länge Geld kostet. Scheitert das Dekodieren, None (nie die Kopf-Länge)."""
    if gruendlich:
        return dauer_dekodiert(datei)
    programm = shutil.which("ffprobe")
    if not programm:
        return None
    extra = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        res = subprocess.run([programm, "-v", "error", *EINGABE, "-show_entries", "format=duration",
                              "-of", "default=noprint_wrappers=1:nokey=1", str(datei)],
                             capture_output=True, stdin=subprocess.DEVNULL, timeout=120, **extra)
        wert = float(res.stdout.decode(errors="replace").strip())
    except (subprocess.TimeoutExpired, ValueError, OSError):
        wert = 0.0
    try:
        groesse = datei.stat().st_size
    except OSError:
        groesse = 0
    if wert <= 0 or groesse / wert > PLAUSIBEL_BYTES_JE_S:
        return dauer_dekodiert(datei)  # 0.4.91 (M2): unplausibler Kopf und nicht dekodierbar → nicht messbar
    return wert


MAX_TONSPUREN = 8  # mehr Tonspuren in einer Datei hat keine Aufnahme – solche Dateien gelten als nicht messbar


def _tonspuren(datei: Path) -> int | None:
    programm = shutil.which("ffprobe")
    if not programm:
        return None
    extra = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    try:
        res = subprocess.run([programm, "-v", "error", *EINGABE, "-select_streams", "a", "-show_entries",
                              "stream=index", "-of", "csv=p=0", str(datei)],
                             capture_output=True, stdin=subprocess.DEVNULL, timeout=120, **extra)
    except (subprocess.TimeoutExpired, OSError):
        return None
    return len([z for z in res.stdout.decode(errors="replace").splitlines() if z.strip()])


def dauer_dekodiert(datei: Path) -> float | None:
    """Länge durch Dekodieren (ohne Ausgabe), unabhängig von den Angaben im Kopf. 0.4.94 (M2): jede Tonspur wird
    gemessen und die längste zählt – welche Spur später verarbeitet wird, ist damit gleich (`dekodieren` nimmt die
    erste). Ohne ffprobe nur die erste Spur."""
    anzahl = _tonspuren(datei)
    if anzahl is not None and (anzahl == 0 or anzahl > MAX_TONSPUREN):
        return None
    laengen = []
    for nr in range(anzahl or 1):
        try:
            res = ffmpeg(["-hide_banner", "-loglevel", "error", *EINGABE, "-i", str(datei), "-map", f"0:a:{nr}",
                          "-f", "null", "-progress", "pipe:1", "-"], 900)
        except (AudioFehler, OSError):
            return None
        zeiten = [int(z.split("=", 1)[1]) for z in res.stdout.decode(errors="replace").splitlines()
                  if z.startswith("out_time_us=") and z.split("=", 1)[1].strip().isdigit()]
        if zeiten and max(zeiten) > 0:
            laengen.append(max(zeiten) / 1_000_000)
    return max(laengen) if laengen else None


def hoerprobe(wav: Path, start: float, laenge: float, ziel: Path) -> bytes:
    """Kurze Hörprobe als Ogg/Opus (wenige Sekunden, klein)."""
    res = ffmpeg(["-hide_banner", "-loglevel", "error", "-y", "-ss", f"{max(0.0, start):.2f}",
                  "-t", f"{laenge:.2f}", *EINGABE, "-i", str(wav), "-c:a", "libopus", "-b:a", "24k", str(ziel)], 120)
    if res.returncode != 0:
        raise AudioFehler("sample_failed", res.stderr.decode(errors="replace").strip()[-300:])
    return ziel.read_bytes()
