"""Nutzungsbedingungen je Server (0.4.22).

Jeder Server hat Nutzungsbedingungen: entweder die neutrale Vorlage unter `/nutzungsbedingungen` (aus den Angaben des
Betreibers befüllt; der Betreiber kann sie im Assistenten bzw. in der Verwaltung anpassen) oder ein eigener Link.
`ServerInfo.termsUrl` ist deshalb immer gesetzt. Die Fassung (`termsVersion`, ganze Zahl ab 1) zählt der Betreiber in
der Verwaltung hoch, wenn eine Änderung eine neue Zustimmung braucht; Konten mit älterer oder fehlender Zustimmung
dürfen nur noch lesen (403 `terms_required`, Ausnahmen in `app/access.py`). Zustimmung und Fassung stehen am Konto
(`User.terms_accepted_version/_at`), der Zeitpunkt wird protokolliert.
"""
from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.einstellungen import meta_lesen, meta_schreiben

META_TEXT = "nutzungsbedingungen.text"  # eigener Text (leer = Vorlage)
META_URL = "nutzungsbedingungen.url"  # eigener Link (leer = Text bzw. Vorlage auf diesem Server)
META_VERSION = "nutzungsbedingungen.version"  # Fassung, ab 1
META_GEAENDERT = "nutzungsbedingungen.geaendert"  # Zeitpunkt der letzten Fassung (ISO), nur zur Anzeige
PFAD = "/nutzungsbedingungen"
MAX_TEXT = 40000


@dataclass
class Stand:
    text: str  # gespeicherter Text oder Vorlage
    eigener_text: bool
    url: str | None  # eigener Link
    version: int

    @property
    def quelle(self) -> str:
        return "link" if self.url else ("eigen" if self.eigener_text else "vorlage")


def version(db: Session) -> int:
    try:
        return max(1, int(meta_lesen(db, META_VERSION) or 1))
    except ValueError:
        return 1


def stand(db: Session, sprache: str = "de") -> Stand:
    text = (meta_lesen(db, META_TEXT) or "").strip()
    url = (meta_lesen(db, META_URL) or "").strip()
    return Stand(text=text or vorlage(db, sprache), eigener_text=bool(text),
                 url=url if url.startswith("https://") else None, version=version(db))


def adresse(db: Session, basis: str) -> str:
    """termsUrl für /info: eigener Link oder die Seite dieses Servers."""
    st = stand(db)
    return st.url or basis.rstrip("/") + PFAD


def impressum_adresse(db: Session, basis: str) -> str | None:
    """legalNoticeUrl für /info: eigener Link, Vorlage unter /impressum oder null."""
    text, link = meta_lesen(db, "impressum.text"), (meta_lesen(db, "impressum.url") or "").strip()
    if link.startswith("https://"):
        return link
    return basis.rstrip("/") + "/impressum" if text else None


def hochzaehlen(db: Session) -> int:
    """Neue Fassung: alle Konten müssen erneut zustimmen (403 bis dahin)."""
    from app.db import utcnow

    neu = version(db) + 1
    meta_schreiben(db, META_VERSION, str(neu))
    meta_schreiben(db, META_GEAENDERT, utcnow().isoformat())
    return neu


def text_speichern(db: Session, text: str) -> None:
    meta_schreiben(db, META_TEXT, text.strip()[:MAX_TEXT])
    meta_schreiben(db, META_URL, "")


def link_speichern(db: Session, url: str) -> None:
    meta_schreiben(db, META_URL, url.strip()[:500])
    meta_schreiben(db, META_TEXT, "")


def vorlage_nutzen(db: Session) -> None:
    meta_schreiben(db, META_TEXT, "")
    meta_schreiben(db, META_URL, "")


def zugestimmt(user, db: Session) -> bool:
    """Hat das Konto der geltenden Fassung zugestimmt?"""
    return user.terms_accepted_version is not None and user.terms_accepted_version >= version(db)


def vorlage(db: Session, sprache: str = "de") -> str:
    """Neutrale Vorlage – nichts versprechen, nichts erfinden: Zweck, Konto, Aufnahmen und Einwilligung, Inhalte,
    Gewähr, Kündigung, Änderungen. Angaben des Betreibers kommen aus app/betreiber.py."""
    from app import betreiber
    from app.einstellungen import angaben

    b, a = betreiber.lesen(db), angaben(db)
    en = sprache == "en"
    wir, _ = betreiber.wir(b.art)
    if en:
        return "\n\n".join([
            "# Terms of use",
            f"These terms apply to the use of the Taleward server “{a.server_name}”, operated by {b.name} "
            "(see the legal notice). By creating an account you accept them.",
            "## What this server is for",
            "This server records game sessions of tabletop role-playing groups, transcribes them and turns them into "
            "chapters (summaries) and a campaign bible for the members of a campaign. It is meant for the groups the "
            "operator invites; there is no entitlement to an account or to availability.",
            "## Your account",
            "- You need an invitation to create an account. Keep your login to yourself and tell the operator if you "
            "think someone else is using it.",
            "- Use your own name or a nickname that others at the table recognise. Do not impersonate other people.",
            "- You can delete your account yourself at any time in the app (“Accounts and servers”). Chapters and the "
            "campaign bible stay with the campaign; your personal data is removed as described in the privacy notice.",
            "## Recordings and consent",
            "- A session is only recorded if every person present has agreed – each person for themselves. Guests "
            "agree on the device at the table.",
            "- You can withdraw your standing consent at any time in the app. Recordings that have not been uploaded "
            "yet are then rejected; published chapters remain.",
            "- Recordings are kept only as long as the server settings say (see the privacy notice) and are then "
            "deleted.",
            "## Content",
            "- Chapters are written by software from a transcript. They can be incomplete or wrong; the game master "
            "checks them before publishing, but no one guarantees that they are correct.",
            "- Do not upload or write anything that infringes the rights of others or is illegal. The operator may "
            "remove such content and close accounts that repeatedly break these rules.",
            "- Comments, character descriptions and documents you add remain yours. You allow the campaign members "
            "to read what you share with them and the operator to store it on this server.",
            "## No warranty",
            "This server is run as a hobby or club service, as is. The operator does not promise availability, "
            "freedom from errors or that data is never lost – keep copies of what matters to you. The operator is "
            "liable only for intent and gross negligence, as far as the law allows this limitation.",
            "## Changes",
            "If these terms change in a way that needs your renewed consent, the app shows the new version. Until "
            "you accept it you can still read, withdraw consents, download your data and delete your account.",
            "## Software",
            "The Taleward project only provides the software (free software under the AGPL-3.0) and is not a party "
            "to these terms.",
        ])
    return "\n\n".join([
        "# Nutzungsbedingungen",
        f"Diese Bedingungen gelten für die Nutzung des Taleward-Servers „{a.server_name}“, den {b.name} betreibt "
        "(siehe Impressum). Mit dem Anlegen eines Kontos nimmst du sie an.",
        "## Wofür dieser Server da ist",
        f"Dieser Server zeichnet Spielabende von Rollenspielgruppen auf, schreibt sie ab und macht daraus Kapitel "
        f"(Zusammenfassungen) und eine Kampagnenbibel für die Mitglieder einer Kampagne – für {wir} und die Gruppen, "
        "die der Betreiber einlädt. Einen Anspruch auf ein Konto oder auf Verfügbarkeit gibt es nicht.",
        "## Dein Konto",
        "- Ein Konto gibt es nur mit Einladung. Halte deine Zugangsdaten geheim und sag dem Betreiber Bescheid, wenn "
        "du glaubst, dass jemand anderes sie benutzt.",
        "- Nutze deinen Namen oder einen Spitznamen, den die anderen am Tisch kennen. Gib dich nicht als andere "
        "Person aus.",
        "- Du kannst dein Konto jederzeit selbst in der App löschen („Konten und Server“). Kapitel und Kampagnenbibel "
        "bleiben bei der Kampagne; deine persönlichen Daten werden entfernt, wie es der Datenschutzhinweis beschreibt.",
        "## Aufnahmen und Einwilligung",
        "- Ein Spielabend wird nur aufgezeichnet, wenn jede anwesende Person zugestimmt hat – jede für sich selbst. "
        "Gäste stimmen am Gerät am Tisch zu.",
        "- Deine dauerhafte Zustimmung kannst du jederzeit in der App widerrufen. Noch nicht hochgeladene Aufnahmen "
        "werden dann abgelehnt; veröffentlichte Kapitel bleiben.",
        "- Aufnahmen bleiben nur so lange, wie es die Einstellung des Servers vorsieht (siehe Datenschutzhinweis), "
        "und werden danach gelöscht.",
        "## Inhalte",
        "- Kapitel schreibt eine Software aus einer Abschrift. Sie können unvollständig oder falsch sein; die "
        "Spielleitung prüft sie vor dem Veröffentlichen, aber niemand garantiert, dass sie stimmen.",
        "- Lade nichts hoch und schreib nichts, was Rechte anderer verletzt oder verboten ist. Der Betreiber darf "
        "solche Inhalte entfernen und Konten schließen, die wiederholt gegen diese Regeln verstoßen.",
        "- Kommentare, Charakterbeschreibungen und Unterlagen, die du einbringst, bleiben deine. Du erlaubst den "
        "Mitgliedern der Kampagne, zu lesen, was du mit ihnen teilst, und dem Betreiber, es auf diesem Server zu "
        "speichern.",
        "## Keine Gewähr",
        "Dieser Server läuft als Hobby- oder Vereinsangebot, so wie er ist. Der Betreiber verspricht keine "
        "Verfügbarkeit, keine Fehlerfreiheit und nicht, dass nie Daten verloren gehen – behalte Kopien von dem, was "
        "dir wichtig ist. Der Betreiber haftet nur für Vorsatz und grobe Fahrlässigkeit, soweit das Gesetz diese "
        "Einschränkung zulässt.",
        "## Änderungen",
        "Ändern sich diese Bedingungen so, dass eine neue Zustimmung nötig ist, zeigt die App die neue Fassung. Bis du "
        "zustimmst, kannst du weiter lesen, Zustimmungen widerrufen, deine Daten herunterladen und dein Konto löschen.",
        "## Software",
        "Das Projekt Taleward stellt nur die Software bereit (freie Software unter der AGPL-3.0) und ist nicht "
        "Vertragspartner dieser Bedingungen.",
    ])
