"""Wer diesen Server betreibt – Angaben für Datenschutzhinweis und Impressum.

Taleward (das Projekt) und jeder Server sind getrennte Rollen: Rechtliche Angaben gehören zum Server und kommen vom
Betreiber. Name und Kontakt stehen schon in den Angaben für die App (`einstellungen.angaben`), hier liegen Art,
Anschrift und bei Vereinen und Firmen die freiwilligen Zusatzangaben. Alles in den Server-Einstellungen (meta).

Die Texte unten sind VORLAGEN, keine Rechtsberatung. Der Betreiber liest sie, passt sie an und veröffentlicht sie
selbst – oder gibt einen Link auf eigene Texte an. Ob er ein Impressum braucht, beurteilt er selbst; Taleward gibt
dazu keine Auskunft.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.einstellungen import meta_lesen, meta_schreiben

ARTEN = ("privat", "verein", "firma")
MAX_ANSCHRIFT = 500
MAX_ZUSATZ = 300


@dataclass
class Betreiber:
    art: str
    name: str
    anschrift: str
    kontakt: str
    vertretung: str
    register: str

    @property
    def mit_zusatz(self) -> bool:
        """Vertretung und Register gibt es nur bei Vereinen und Firmen."""
        return self.art in ("verein", "firma")


def lesen(db: Session) -> Betreiber:
    from app.einstellungen import angaben

    a = angaben(db)
    art = meta_lesen(db, "betreiber.art") or ""
    return Betreiber(art=art if art in ARTEN else "", name=a.server_operator or "",
                     anschrift=meta_lesen(db, "betreiber.anschrift") or "", kontakt=a.server_contact or "",
                     vertretung=meta_lesen(db, "betreiber.vertretung") or "",
                     register=meta_lesen(db, "betreiber.register") or "")


def speichern(db: Session, art: str, anschrift: str, vertretung: str = "", register: str = "") -> None:
    """Art, Anschrift und Zusatzangaben speichern (Name und Kontakt über `einstellungen.speichern`)."""
    if art not in ARTEN:
        raise ValueError(art)
    zusatz = art in ("verein", "firma")
    meta_schreiben(db, "betreiber.art", art)
    meta_schreiben(db, "betreiber.anschrift", _zeilen(anschrift)[:MAX_ANSCHRIFT])
    meta_schreiben(db, "betreiber.vertretung", vertretung.strip()[:MAX_ZUSATZ] if zusatz else "")
    meta_schreiben(db, "betreiber.register", register.strip()[:MAX_ZUSATZ] if zusatz else "")


def _zeilen(text: str) -> str:
    return "\n".join(z.strip() for z in text.replace("\r\n", "\n").split("\n") if z.strip())


def wir(art: str) -> tuple[str, str]:
    """Wie die Vorlagen von den Runden des Betreibers sprechen („die Runden unseres Vereins“, „unsere Runden“ …) und
    vom Ort der Verarbeitung."""
    return {"privat": ("unsere Runden", "auf einem Server, den wir selbst betreiben"),
            "firma": ("die Runden unserer Organisation", "auf einem Server unserer Organisation")}.get(
        art, ("die Runden unseres Vereins", "auf einem Server unseres Vereins"))


# ---------------------------------------------------------------- Impressum
def impressum_vorlage(db: Session, sprache: str = "de") -> str:
    """Vorlage für das Impressum unter /impressum – aus den Angaben befüllt, Lücken in [eckigen Klammern]."""
    from app.einstellungen import angaben

    b, a = lesen(db), angaben(db)
    en = sprache == "en"
    anschrift = b.anschrift or ("[Postal address]" if en else "[Anschrift]")
    kontakt = b.kontakt or ("[E-mail address]" if en else "[E-Mail-Adresse]")
    t = [("# Legal notice" if en else "# Impressum"),
         (f"Information about the operator of the Taleward server “{a.server_name}”." if en
          else f"Angaben zum Betreiber des Taleward-Servers „{a.server_name}“."),
         ("## Operator" if en else "## Betreiber"), f"{b.name}\n{anschrift}",
         ("## Contact" if en else "## Kontakt"), kontakt]
    if b.mit_zusatz:
        t += [("## Represented by" if en else "## Vertreten durch"),
              b.vertretung or ("[Names of the persons authorised to represent]" if en
                               else "[Namen der vertretungsberechtigten Personen]")]
        if b.register:
            t += [("## Register entry" if en else "## Registereintrag"), b.register]
    t += [("## About Taleward" if en else "## Zu Taleward"),
          (f"This server is operated by {b.name}. The Taleward project only provides the software (free software "
           "under the AGPL-3.0) and does not operate this server." if en else
           f"Diesen Server betreibt {b.name}. Das Projekt Taleward stellt nur die Software bereit (freie Software "
           "unter der AGPL-3.0) und betreibt diesen Server nicht.")]
    return "\n\n".join(t)
