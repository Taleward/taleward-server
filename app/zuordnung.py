"""Stimmen automatisch zuordnen – läuft auf dem Server, direkt nach der Transkription.

Aufbau: Jede Quelle liefert Hinweise „Stimme S ist vermutlich Mitglied M, Sicherheit c“. Danach verteilt
`verteilen` die Mitglieder so auf die Stimmen, dass jedes Mitglied höchstens einmal vorgeschlagen wird
(die SL kann später trotzdem zwei Stimmen derselben Person zuordnen).

Quellen:
- `intro_round`: Hinweise aus der Abschrift – die Vorstellungsrunde am Anfang („Ich bin Lilio und spiele Jemma Reed“,
  „ich leite heute“, „ich als Erzähler“) und, schwächer, Figuren, die sich im Spiel mehrfach selbst mit Namen nennen
  („Mein Name ist Lysander Federkiel“). Im Spiel zählt nur eine klare Mehrheit, ein einzelner Satz nie.
- `voice_match`: Abgleich mit Stimmprofilen (app/stimmprofile.py).

Kandidaten sind die Anwesenden: Mitglieder mit Konto und Gäste (deren Name oft der Figurenname ist). Vorgeschlagen
werden nur Mitglieder; erkennt die Abschrift einen Gast, bekommt diese Stimme keinen Vorschlag – und kein Mitglied
wird ihr fälschlich zugeschlagen. Die SL darf mehr als eine Stimme haben (verstellte Stimmen für Figuren): Sind alle
anderen Anwesenden schon vergeben, bekommt eine übrige Stimme mit nennenswertem Redeanteil die SL als unsicheren
Vorschlag. Nur Vorschläge ab MIN_SICHERHEIT. Die SL bestätigt immer.
Je Runde schreibt `vorschlagen` eine Zeile ins Protokoll (nur Anzahlen, keine Namen; ohne Vorschlag als Warnung).
"""
from __future__ import annotations

import logging
import re
import unicodedata
from dataclasses import dataclass
from difflib import SequenceMatcher

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import GameSession, Member, Speaker, TranscriptSegment

FENSTER_SEKUNDEN = 20 * 60  # Vorstellungen stehen am Anfang
MIN_AEHNLICHKEIT = 0.78  # „Gemma“ ≈ „Jemma“ (Whisper-Schreibweise) = 0.8
MIN_SICHERHEIT = 0.5
IM_SPIEL_MINDESTENS = 2  # so oft muss sich eine Figur im Spiel selbst nennen …
IM_SPIEL_SICHERHEIT = 0.6  # … und so sicher ist der Vorschlag dann
SL_UEBRIG_ANTEIL = 0.03  # übrige Stimme ab diesem Anteil an der Redezeit → unsicherer Vorschlag „Spielleitung“
SL_UEBRIG_SICHERHEIT = 0.55
GAST = "gast:"  # Kennung eines Gasts in den Hinweisen (nie als Vorschlag gespeichert)

log = logging.getLogger("zuordnung")


@dataclass
class Hinweis:
    speaker_id: str
    member_id: str
    sicherheit: float
    quelle: str  # intro_round | voice_match
    beleg: str = ""


# ---------------------------------------------------------------- Namen
def _norm(text: str) -> str:
    text = unicodedata.normalize("NFKC", text).casefold()
    return re.sub(r"[^\w\s-]", " ", text).strip()


@dataclass
class Person:
    member: Member | None  # None: Gast
    namen: set[str]  # Name, Vorname, Benutzername
    figuren: set[str]  # Charaktername und dessen Teile
    gast: str | None = None

    @property
    def kennung(self) -> str:
        return self.member.id if self.member is not None else GAST + (self.gast or "")

    @property
    def ist_sl(self) -> bool:
        return self.member is not None and self.member.role == "gm"


def _varianten(text: str | None) -> set[str]:
    if not text:
        return set()
    voll = _norm(text)
    teile = {t for t in re.split(r"[\s-]+", voll) if len(t) >= 3}
    return {voll, *teile} - {""}


def personen(mitglieder: list[Member], gaeste: list[str] = ()) -> list[Person]:
    # Gelöschte Konten (ohne user) haben keinen Namen mehr, an dem man sie erkennen könnte
    pers = [Person(m, (_varianten(m.user.display_name) | _varianten(m.user.username)) if m.user else set(),
                   _varianten(m.character_name)) for m in mitglieder]
    # Gäste: Der eingetragene Name ist mal die Person, mal die Figur – er zählt für beides
    return pers + [Person(None, _varianten(g), _varianten(g), gast=g) for g in dict.fromkeys(gaeste) if g]


def _aehnlichkeit(wort: str, varianten: set[str]) -> float:
    best = 0.0
    for v in varianten:
        if wort == v:
            return 1.0
        if abs(len(wort) - len(v)) <= 3:
            best = max(best, SequenceMatcher(None, wort, v).ratio())
    return best if best >= MIN_AEHNLICHKEIT else 0.0


def _bester(phrase: str, pers: list[Person], feld: str, n: int = 3) -> tuple[Person | None, float]:
    """Wer passt zu den ersten n Wörtern nach „ich bin …“ / „ich spiele …“? Auch zwei Wörter („Jemma Reed“)."""
    woerter = [w for w in _norm(phrase).split() if w not in _FUELLWOERTER][:n]
    kandidaten = woerter + [" ".join(woerter[:2])] if len(woerter) >= 2 else woerter
    best, wert = None, 0.0
    for p in pers:
        for w in kandidaten:
            a = _aehnlichkeit(w, getattr(p, feld))
            if a > wert:
                best, wert = p, a
    return best, wert


_FUELLWOERTER = {"der", "die", "das", "den", "dem", "ein", "eine", "einen", "heute", "hier", "also", "ja", "äh", "ähm",
                 "the", "a", "an", "today", "uh", "um", "so", "well", "mal", "wieder", "nochmal", "weiterhin"}

# Wer ich bin
_PERSON = re.compile(r"\b(?:ich bin|ich heiße|ich heisse|mein name ist|hier ist|i am|i'm|my name is|this is)\s+(?P<rest>.{1,40})",
                     re.IGNORECASE)
# Wen ich spiele
_FIGUR = re.compile(r"\b(?:ich spiele|ich spiel|spiele ich|mein charakter heißt|mein charakter ist|meine figur heißt|"
                    r"meine figur ist|mein held heißt|meine heldin heißt|i play|i'm playing|i am playing|"
                    r"my character is|my character's name is)\s+(?P<rest>.{1,40})", re.IGNORECASE)
# Spielleitung
_SL = re.compile(r"\b(?:ich leite|ich bin (?:heute )?(?:euer |eure |der |die |dein |deine )?(?:spielleit\w*|sl|meister\w*|"
                 r"gm|dm|erzähler\w*)|ich mache (?:heute )?(?:die |den )?(?:spielleitung|sl|meister)|"
                 r"ich als (?:euer |eure |der |die )?(?:spielleit\w*|sl|meister\w*|erzähler\w*)|"
                 r"(?:ich bin |bin ich )?(?:euer|eure) (?:spielleit\w*|meister\w*|erzähler\w*)(?: heute)? (?:bin|heute)|"
                 r"i'm (?:your |the )?(?:gm|dm|game ?master)|i am (?:your |the )?(?:gm|dm|game ?master)|i'll be running|"
                 r"as (?:your |the )?(?:gm|dm|game ?master|narrator))",
                 re.IGNORECASE)


# ---------------------------------------------------------------- Vorstellungsrunde
def _absaetze(segmente: list[TranscriptSegment]) -> list[tuple[str, float, str]]:
    """Aufeinanderfolgende Zeilen derselben Stimme verbinden – eine Vorstellung verteilt sich oft auf zwei Zeilen."""
    out: list[list] = []
    for seg in segmente:
        if not seg.speaker_id:
            continue
        if out and out[-1][0] == seg.speaker_id and seg.start - out[-1][3] < 2.5:
            out[-1][2] += " " + seg.text
            out[-1][3] = seg.end
        else:
            out.append([seg.speaker_id, seg.start, seg.text, seg.end])
    return [(a[0], a[1], a[2]) for a in out]


def vorstellungsrunde(segmente: list[TranscriptSegment], mitglieder: list[Member],
                      gaeste: list[str] = ()) -> list[Hinweis]:
    pers = personen(mitglieder, gaeste)
    sl = [p for p in pers if p.ist_sl]
    hinweise: list[Hinweis] = []
    im_spiel: dict[str, dict[str, list[str]]] = {}  # Stimme → Person → Belege
    for speaker_id, start, text in _absaetze(segmente):
        zitat = text.strip()[:160]
        if start > FENSTER_SEKUNDEN:
            # Im Spiel nennen sich Figuren selbst („Mein Name ist Lysander Federkiel“). Nur die ersten zwei Wörter
            # zählen („Ich bin sofort hinterher, Arlekin“ ist keine Vorstellung), und nur Figuren.
            for m in (*_PERSON.finditer(text), *_FIGUR.finditer(text)):
                p, _ = _bester(m.group("rest"), pers, "figuren", n=2)
                if p:
                    im_spiel.setdefault(speaker_id, {}).setdefault(p.kennung, []).append(zitat)
            continue
        for m in _PERSON.finditer(text):
            p, a = _bester(m.group("rest"), pers, "namen")
            if p:
                hinweise.append(Hinweis(speaker_id, p.kennung, 0.9 * a, "intro_round", zitat))
            else:  # „ich bin heute Litha Flamel“ – nach „ich bin“ steht manchmal die Figur
                p, a = _bester(m.group("rest"), pers, "figuren")
                if p:
                    hinweise.append(Hinweis(speaker_id, p.kennung, 0.8 * a, "intro_round", zitat))
        for m in _FIGUR.finditer(text):
            p, a = _bester(m.group("rest"), pers, "figuren")
            if p:
                hinweise.append(Hinweis(speaker_id, p.kennung, 0.85 * a, "intro_round", zitat))
        if len(sl) == 1 and _SL.search(text):
            hinweise.append(Hinweis(speaker_id, sl[0].kennung, 0.8, "intro_round", zitat))
    for speaker_id, je_person in im_spiel.items():
        rang = sorted(je_person.items(), key=lambda kv: -len(kv[1]))
        (kennung, belege), zweit = rang[0], (len(rang[1][1]) if len(rang) > 1 else 0)
        if len(belege) >= IM_SPIEL_MINDESTENS and len(belege) >= 2 * zweit:
            hinweise.append(Hinweis(speaker_id, kennung, IM_SPIEL_SICHERHEIT, "intro_round", belege[0]))
    return hinweise


# ---------------------------------------------------------------- Anrede und Selbstnennung (0.4.21, Speaker.hint)
ANREDE_MINDESTENS = 2  # so oft muss die Abschrift widersprechen …
ANREDE_FAKTOR = 2  # … und mindestens doppelt so oft wie sie die Zuordnung stützt
ANREDE_ABSTAND = 6.0  # Sekunden: so schnell muss die angesprochene Stimme antworten


def _figurennamen(mitglieder: list[Member]) -> list[tuple[Member, str, re.Pattern]]:
    """Je Spielerfigur ein Muster für die Anrede: Name am Satzanfang mit Komma/Frage/Ausruf oder am Satzende."""
    aus = []
    for m in mitglieder:
        if m.role == "gm" or not m.character_name:
            continue
        vorname = m.character_name.strip().split()[0]
        if len(vorname) < 3:
            continue
        n = re.escape(vorname)
        muster = re.compile(rf"(?:^|[.!?…]\s+|,\s+){n}\s*[,!?…]|,\s+{n}\s*[.!?…]?\s*$|^{n}\s*$", re.IGNORECASE)
        aus.append((m, vorname, muster))
    return aus


def anrede_hinweise(segmente: list[TranscriptSegment], mitglieder: list[Member],
                    zuordnung: dict[str, str | None]) -> dict[str, dict]:
    """Je Stimme höchstens ein Hinweis {code, name, memberId, count}: Die Abschrift spricht die Stimme mehrfach mit
    dem Namen einer anderen Figur an (addressed_as) oder sie nennt sich selbst so (self_named) – und das klar öfter,
    als sie die zugeordnete Figur stützt. zuordnung: Stimme → Mitglied (Vorschlag oder Bestätigung); Stimmen ohne
    Zuordnung bekommen keinen Hinweis (kein Widerspruch)."""
    figuren = _figurennamen(mitglieder)
    if not figuren:
        return {}
    pers = personen([m for m, _, _ in figuren])
    zaehler: dict[str, dict[str, dict[str, int]]] = {}  # Stimme → Mitglied → code → Anzahl
    reihe = [seg for seg in segmente if seg.speaker_id]
    for i, seg in enumerate(reihe):
        text = seg.text or ""
        for m in (*_PERSON.finditer(text), *_FIGUR.finditer(text)):
            p, _ = _bester(m.group("rest"), pers, "figuren", n=2)
            if p is not None and p.member is not None:
                z = zaehler.setdefault(seg.speaker_id, {}).setdefault(p.member.id, {})
                z["self_named"] = z.get("self_named", 0) + 1
        if i + 1 >= len(reihe):
            continue
        naechste = reihe[i + 1]
        if naechste.speaker_id == seg.speaker_id or naechste.start - seg.end > ANREDE_ABSTAND:
            continue
        for m, _, muster in figuren:
            if muster.search(text.strip()):
                z = zaehler.setdefault(naechste.speaker_id, {}).setdefault(m.id, {})
                z["addressed_as"] = z.get("addressed_as", 0) + 1
    namen = {m.id: vorname for m, vorname, _ in figuren}
    aus: dict[str, dict] = {}
    for speaker_id, je_mitglied in zaehler.items():
        zugeordnet = zuordnung.get(speaker_id)
        if not zugeordnet:
            continue
        stuetzt = sum(je_mitglied.get(zugeordnet, {}).values())
        beste = None
        for member_id, codes in je_mitglied.items():
            if member_id == zugeordnet:
                continue
            code, anzahl = max(codes.items(), key=lambda kv: kv[1])
            gesamt = sum(codes.values())
            if beste is None or gesamt > beste[0]:
                beste = (gesamt, code, member_id, anzahl)
        if beste and beste[0] >= ANREDE_MINDESTENS and beste[0] >= ANREDE_FAKTOR * stuetzt:
            aus[speaker_id] = {"code": beste[1], "name": namen[beste[2]], "memberId": beste[2], "count": beste[3]}
    return aus


# ---------------------------------------------------------------- Verteilen
def verteilen(hinweise: list[Hinweis]) -> dict[str, Hinweis]:
    """Hinweise je (Stimme, Mitglied) zusammenfassen (1 − Π(1 − c)), dann gierig nach Sicherheit verteilen:
    jede Stimme und jedes Mitglied höchstens einmal. Ergebnis: speaker_id → bester Hinweis."""
    summe: dict[tuple[str, str], Hinweis] = {}
    for h in hinweise:
        k = (h.speaker_id, h.member_id)
        if k in summe:
            alt = summe[k]
            alt.sicherheit = 1 - (1 - alt.sicherheit) * (1 - h.sicherheit)
            if h.quelle == "voice_match":  # Stimmprofil ist die stärkere Quelle
                alt.quelle = "voice_match"
        else:
            summe[k] = Hinweis(h.speaker_id, h.member_id, h.sicherheit, h.quelle, h.beleg)
    # Widersprüche abwerten: sagt dieselbe Stimme für zwei Personen „ich bin …“, ist sie unsicherer
    je_stimme: dict[str, float] = {}
    for h in summe.values():
        je_stimme[h.speaker_id] = je_stimme.get(h.speaker_id, 0) + h.sicherheit
    for h in summe.values():
        h.sicherheit *= h.sicherheit / je_stimme[h.speaker_id]  # nur ein Kandidat: unverändert
    ergebnis: dict[str, Hinweis] = {}
    vergeben: set[str] = set()
    for h in sorted(summe.values(), key=lambda x: -x.sicherheit):
        if h.sicherheit < MIN_SICHERHEIT or h.speaker_id in ergebnis or h.member_id in vergeben:
            continue
        ergebnis[h.speaker_id] = h
        vergeben.add(h.member_id)
    return ergebnis


def sl_weitere_stimmen(auswahl: dict[str, Hinweis], redezeit: dict[str, float], kandidaten: list[str],
                       sl_id: str | None) -> dict[str, Hinweis]:
    """Die SL spricht Figuren oft mit verstellter Stimme – die Trennung macht daraus eine zweite Stimme. Sind alle
    anderen Anwesenden schon vergeben, bekommen übrige Stimmen mit nennenswertem Redeanteil die SL (unsicher)."""
    if not sl_id or sl_id not in {h.member_id for h in auswahl.values()}:
        return {}
    vergeben = {h.member_id for h in auswahl.values()}
    if any(k not in vergeben for k in kandidaten if k != sl_id):
        return {}
    gesamt = sum(redezeit.values()) or 1.0
    return {sid: Hinweis(sid, sl_id, SL_UEBRIG_SICHERHEIT, "intro_round")
            for sid, sek in redezeit.items() if sid not in auswahl and sek / gesamt >= SL_UEBRIG_ANTEIL}


def vorschlagen(db: Session, s: GameSession, weitere_hinweise: list[Hinweis] | None = None) -> int:
    """Vorschläge für die Stimmen einer Tischaufnahme setzen. Gibt die Anzahl vorgeschlagener Stimmen zurück."""
    anwesend = {a.member_id for a in s.attendees if a.member_id}
    gaeste = [a.guest_name for a in s.attendees if not a.member_id and a.guest_name]
    mitglieder = list(db.scalars(select(Member).where(Member.id.in_(anwesend)))) if anwesend else []
    segmente = list(db.scalars(select(TranscriptSegment).where(TranscriptSegment.session_id == s.id)
                               .order_by(TranscriptSegment.position)))
    from app.stimmprofile import hinweise as stimm_hinweise

    aus_abschrift = vorstellungsrunde(segmente, mitglieder, gaeste)
    stimmprofil = stimm_hinweise(db, s)
    auswahl = verteilen(aus_abschrift + stimmprofil + list(weitere_hinweise or []))
    stimmen = [sp for sp in db.scalars(select(Speaker).where(Speaker.session_id == s.id))
               if sp.source != "discord_track"]
    sl = next((m.id for m in mitglieder if m.role == "gm"), None)
    kandidaten = [m.id for m in mitglieder] + [GAST + g for g in dict.fromkeys(gaeste)]
    auswahl |= sl_weitere_stimmen(auswahl, {sp.id: sp.speaking_seconds or 0.0 for sp in stimmen}, kandidaten, sl)
    vorgeschlagen = 0
    for sp in stimmen:
        h = auswahl.get(sp.id)
        if h is None or h.member_id.startswith(GAST):  # Gäste kann die Schnittstelle (noch) nicht vorschlagen
            sp.suggested_member_id, sp.confidence, sp.source = None, 0.0, "none"
        else:
            sp.suggested_member_id, sp.confidence, sp.source = h.member_id, round(h.sicherheit, 2), h.quelle
            vorgeschlagen += 1
    # Ohne einen einzigen Vorschlag als Warnung – die steht im Server-Protokoll auch ohne eigene Einstellung
    log.log(logging.INFO if vorgeschlagen else logging.WARNING, "Stimmvorschläge Session %s: %d Stimmen, %d Anwesende mit Konto, %d Gäste, %d Segmente "
             "(%d in den ersten %d min), Hinweise Abschrift %d, Stimmprofil %d; vorgeschlagen %d, als Gast erkannt %d",
             s.id, len(stimmen), len(mitglieder), len(gaeste), len(segmente),
             sum(1 for x in segmente if x.start <= FENSTER_SEKUNDEN), FENSTER_SEKUNDEN // 60, len(aus_abschrift),
             len(stimmprofil), vorgeschlagen, sum(1 for h in auswahl.values() if h.member_id.startswith(GAST)))
    return vorgeschlagen
