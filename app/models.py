"""Datenbanktabellen. IDs sind UUID-Strings, Zeiten immer UTC."""
import json
import uuid
from datetime import datetime

from sqlalchemy import Boolean, Float, ForeignKey, Index, Integer, String, Text, UniqueConstraint, text
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base, UTCDateTime, utcnow


def new_id() -> str:
    return str(uuid.uuid4())


class ServerMeta(Base):
    """Einstellungen, die der Server selbst verwaltet (z. B. seine dauerhafte Server-ID für Tokens)."""

    __tablename__ = "server_meta"
    key: Mapped[str] = mapped_column(String(64), primary_key=True)
    value: Mapped[str] = mapped_column(Text)


# ---------------------------------------------------------------- Konten
class User(Base):
    """Konto. Wie man sich anmeldet (Passwort, später OIDC/Passkey), steht getrennt in AuthMethod."""

    __tablename__ = "users"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    # immer kleingeschrieben gespeichert → Anmeldung unabhängig von Groß-/Kleinschreibung
    username: Mapped[str] = mapped_column(String(64), unique=True, index=True)
    display_name: Mapped[str] = mapped_column(String(128))
    # Wird beim Passwort-Zurücksetzen erhöht → alte Anmeldungen werden ungültig
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Einrichtungskonto admin/admin: nur für die Ersteinrichtung in der Verwaltung, löscht sich danach selbst
    setup_account: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # Selbstregistrierung: Zustimmung zum Datenschutzhinweis und Altersbestätigung mit Zeitpunkt
    privacy_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    age_confirmed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Freiwillige E-Mail nur für Kontozwecke (0.4.0): bestätigt bzw. eingetragen, aber noch nicht bestätigt
    email: Mapped[str | None] = mapped_column(String(254), nullable=True, index=True)
    email_pending: Mapped[str | None] = mapped_column(String(254), nullable=True)
    email_verified_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Nach „Passwort ändern“ in der App bleibt genau das Token gültig, mit dem geändert wurde („<ver>:<jti>“)
    token_ausnahme: Mapped[str | None] = mapped_column(String(80), nullable=True)
    # 0.4.22: zuletzt zugestimmte Fassung der Nutzungsbedingungen mit Zeitpunkt; None = noch nie (Konto von vorher)
    terms_accepted_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    terms_accepted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.23: SL-Freigabe (eigene Kampagnen anlegen): granted | none | pending | rejected. Zählt nur, wenn der Server
    # sie verlangt (app/slfreigabe.py). Anfrage und Entscheidung mit Zeitpunkt und entscheidendem Verwaltungskonto.
    gm_permission: Mapped[str] = mapped_column(String(16), default="none", server_default="none")
    gm_requested_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    gm_decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    gm_decided_by: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gm_sprache: Mapped[str | None] = mapped_column(String(8), nullable=True)  # Sprache der Antwort-Mail
    gm_mail_fehler: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 0.4.135: Hinweis-Mails zum Guthaben abbestellt (Guthaben-Seite)
    guthaben_mails_aus: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 0.4.135: Sprache der App bei der letzten Anmeldung (de|en) – danach richten sich Mails an das Konto
    sprache: Mapped[str | None] = mapped_column(String(8), nullable=True)

    auth_methods: Mapped[list["AuthMethod"]] = relationship(cascade="all, delete-orphan")


class AuthMethod(Base):
    __tablename__ = "auth_methods"
    __table_args__ = (UniqueConstraint("user_id", "kind"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    kind: Mapped[str] = mapped_column(String(16))  # password | oidc:google | oidc:discord | … | passkey
    secret: Mapped[str] = mapped_column(Text)  # bei password: Argon2-Hash; bei oidc:*: Kennung beim Dienst (sub)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


# ---------------------------------------------------------------- Organisation
class Organization(Base):
    """Oberste Ebene: Verein, Laden, Gruppe. Ein selbst betriebener Server hat meist genau eine."""

    __tablename__ = "organizations"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(200))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class OrgMember(Base):
    __tablename__ = "org_members"
    organization_id: Mapped[str] = mapped_column(ForeignKey("organizations.id", ondelete="CASCADE"), primary_key=True)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True, index=True)
    role: Mapped[str] = mapped_column(String(16), default="member")  # admin | member
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Kampagnen
class Campaign(Base):
    __tablename__ = "campaigns"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    organization_id: Mapped[str | None] = mapped_column(
        ForeignKey("organizations.id", ondelete="SET NULL"), nullable=True, index=True
    )
    title: Mapped[str] = mapped_column(String(200))
    description: Mapped[str] = mapped_column(Text, default="")
    language: Mapped[str] = mapped_column(String(8), default="de")
    system: Mapped[str | None] = mapped_column(String(32), nullable=True)
    system_name: Mapped[str | None] = mapped_column(String(100), nullable=True)
    world_info: Mapped[str | None] = mapped_column(Text, nullable=True)
    cover_preset: Mapped[str | None] = mapped_column(String(32), nullable=True)
    cover_image_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    next_session_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    archived_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # abgeschlossen (0.4.5)
    # darf der externe Anbieter (falls der Betreiber einen freigibt) für diese Kampagne transkribieren?
    allow_external_transcription: Mapped[bool] = mapped_column(Boolean, default=False)
    # 0.3.10: Recap/Vorschläge (und Unterlagen) über die Cloud-API nur, wenn die SL es erlaubt
    allow_cloud_summary: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 0.4.18: Mitglieder dürfen Charakterbogen und Hintergrund auswerten lassen (jede Auswertung mit Zustimmung der SL)
    allow_character_analysis: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 0.4.6: Namenshilfe der SL als JSON {"extra": [], "entfernt": [], "ignoriert": []} (app/namenshilfe.py)
    namenshilfe: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0.4.8: aus einer Umzugsdatei angelegt – Platzhalter-Mitglied der importierenden SL bis „Das bin ich“
    imported_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    imported_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0.4.13: Links der Kampagne als JSON [{id, label, url, shared}] (app/links.py)
    links: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0.4.85 (Abrechnung, app/abrechnung.py): zahlende SL. None = die SL, die die Kampagne angelegt hat (die zuerst
    # beigetretene SL). Abgeben nur ausdrücklich: Angebot an eine andere SL, die es annimmt.
    zahler_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    zahler_angebot_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0.4.25: Fassung der Kampagnen-Einstellungen (zählt bei jeder wirksamen Änderung hoch, app/kampagnen_einstellungen)
    settings_version: Mapped[int] = mapped_column(Integer, default=1, server_default="1")
    settings_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    settings_updated_by: Mapped[str | None] = mapped_column(String(36), nullable=True)  # Mitglied, None = Betreiber
    # 0.4.28: Spielende dürfen Einträge vorschlagen (Standard an) und Zählerarten am Charakter als JSON
    # [{id, label, hint}] (leer = aus); beides nur über app/kampagnen_einstellungen.aendern
    player_proposals: Mapped[bool] = mapped_column(Boolean, default=True, server_default="1")
    counter_kinds: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    members: Mapped[list["Member"]] = relationship(back_populates="campaign", cascade="all, delete-orphan")
    organization: Mapped[Organization | None] = relationship()


class Member(Base):
    __tablename__ = "members"
    __table_args__ = (UniqueConstraint("campaign_id", "user_id"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    # None = Konto gelöscht: Das Mitglied bleibt als „Gelöschtes Konto“ stehen, damit Kapitel, Anwesende und
    # Kommentare stimmig bleiben; Bild, Hintergrund und Zustimmung sind dann entfernt.
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True, nullable=True)
    deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # Verlassen oder entfernt (0.4.5): bleibt stehen wie ein gelöschtes Konto, user_id bleibt für die Rückkehr
    left_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    role: Mapped[str] = mapped_column(String(16))  # gm | player
    character_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    character_summary: Mapped[str | None] = mapped_column(Text, nullable=True)
    # nur die Person selbst und die SL; fließt nie in die KI-Verarbeitung
    character_backstory: Mapped[str | None] = mapped_column(Text, nullable=True)
    portrait_updated_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    recording_consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    chronicle_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    bible_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    joined_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # 0.4.7: Serverkopie des Charakters aus der Sammlung der App – nur die Person selbst ändert sie
    character_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    character_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    character_status: Mapped[str | None] = mapped_column(String(16), nullable=True)  # active | retired | deceased
    character_nickname: Mapped[str | None] = mapped_column(String(64), nullable=True)
    character_system: Mapped[str | None] = mapped_column(String(64), nullable=True)
    # 0.4.8: „Meine Charakterdaten dürfen bei einem Umzug mit“ und offener Platz aus einem Umzug (user_id None)
    move_consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    open_seat: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    # 0.4.28: wie über die Figur gesprochen wird (öffentlich wie die Kurzbeschreibung) und Zählerstände als JSON
    # [{kindId, value, max}] – die Stände nur für die Person selbst und die SL (app/spielende.py)
    character_pronouns: Mapped[str | None] = mapped_column(String(32), nullable=True)
    counters: Mapped[str | None] = mapped_column(Text, nullable=True)

    campaign: Mapped[Campaign] = relationship(back_populates="members")
    user: Mapped[User | None] = relationship()

    @property
    def aktiv(self) -> bool:
        return self.user_id is not None and self.left_at is None

    @property
    def anzeigename(self) -> str:
        """Anzeigename des Kontos; nach Kontolöschung „Gelöschtes Konto“ in der Sprache der Kampagne, für Plätze aus
        einem Umzug „Offener Platz“ bzw. „Ehemaliges Mitglied“ (0.4.8)."""
        if self.user is not None:
            return self.user.display_name
        en = self.campaign.language == "en"
        if self.open_seat:
            return "Open seat" if en else "Offener Platz"
        if self.deleted_at is None:
            return "Former member" if en else "Ehemaliges Mitglied"
        return "Deleted account" if en else "Gelöschtes Konto"


class ConsentLog(Base):
    """Nachweis jeder Zustimmung und jedes Widerrufs (bleibt auch nach Kontolöschung bestehen)."""

    __tablename__ = "consent_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)  # bewusst ohne Fremdschlüssel
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    guest_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    # granted | revoked (stehende Zustimmung in der App) | on_site (vor Ort für eine Session)
    action: Mapped[str] = mapped_column(String(16))
    recorded_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Invite(Base):
    __tablename__ = "invites"
    code: Mapped[str] = mapped_column(String(32), primary_key=True)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    created_by: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime)
    # 0.4.8: Einladung für genau einen offenen Platz (gilt einmal)
    member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)


# ---------------------------------------------------------------- Sessions
class GameSession(Base):
    """Eine gespielte Session = ein Kapitel."""

    __tablename__ = "sessions"
    __table_args__ = (UniqueConstraint("campaign_id", "number"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    number: Mapped[int] = mapped_column(Integer)
    title: Mapped[str | None] = mapped_column(String(300), nullable=True)
    played_at: Mapped[datetime] = mapped_column(UTCDateTime)
    state: Mapped[str] = mapped_column(String(32), default="created", index=True)
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)
    transcription_engine: Mapped[str | None] = mapped_column(String(16), nullable=True)  # local | external
    duration_seconds: Mapped[int | None] = mapped_column(Integer, nullable=True)
    audio_deleted_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    published_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    status_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0.4.6: erneute Transkription mit korrigierter Namenshilfe (höchstens zweimal; läuft gerade → True)
    nachtranskriptionen: Mapped[int] = mapped_column(Integer, default=0, server_default="0")
    nachtranskription: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    state_updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    attendees: Mapped[list["Attendee"]] = relationship(
        back_populates="session", cascade="all, delete-orphan", order_by="Attendee.position"
    )


class Attendee(Base):
    """Anwesende Person: Mitglied (member_id) oder Gast ohne Konto (guest_name)."""

    __tablename__ = "attendees"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str | None] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), nullable=True)
    guest_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    consent: Mapped[bool] = mapped_column(Boolean, default=False)
    consent_source: Mapped[str] = mapped_column(String(16), default="on_site")  # app | on_site
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    session: Mapped[GameSession] = relationship(back_populates="attendees")


class SessionSeen(Base):
    """Gelesen-Marker pro Mitglied und Kapitel (für ungelesene Kommentare)."""

    __tablename__ = "session_seen"
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class GmNote(Base):
    __tablename__ = "gm_notes"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    text: Mapped[str] = mapped_column(Text, default="")
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Bibel
class Entry(Base):
    """Eintrag der Kampagnenbibel."""

    __tablename__ = "entries"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    type: Mapped[str] = mapped_column(String(16))
    name: Mapped[str] = mapped_column(String(300))
    summary: Mapped[str] = mapped_column(Text, default="")
    # geheimer Teil, auch bei öffentlichen Einträgen – Spieler sehen ihn nie
    gm_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    holder_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id", ondelete="SET NULL"), nullable=True
    )
    visibility: Mapped[str] = mapped_column(String(16), default="gm_only")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # Zeitpunkt, ab dem Spieler den Eintrag „neu“ sehen (öffentlich geworden oder öffentlich geändert)
    public_changed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.7: pc-Eintrag – zu welchem Charakter er gehört (bleibt nach einem Charakterwechsel stehen)
    pc_character_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    # 0.4.7: mitgebrachter Eintrag – nur die SL und die Urheberin sehen die Herkunft
    origin_character_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    origin_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    origin_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0.4.9: NSC aus der Figur eines ausgetretenen Mitglieds – nur die SL sieht, von wem
    former_holder_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0.4.13: Links als JSON [{id, label, url, shared}]; Spieler bekommen nur shared (app/links.py)
    links: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0.4.89: aus einer Auswertung von Charakterbogen bzw. Hintergrund übernommen – bis die SL Sichtbarkeit oder
    # „Wer weiß was“ selbst festlegt, wissen es nur SL und Urheberin (auch Nachzügler nicht; die KI nicht als bekannt)
    origin_analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0.4.21: „Auch genannt“ – weitere Namen als JSON-Liste; sichtbar wie der Name
    aliases_json: Mapped[str | None] = mapped_column("aliases", Text, nullable=True)
    # 0.4.28: Pronomen (sinnvoll bei NSC), sichtbar wie der Name
    pronouns: Mapped[str | None] = mapped_column(String(32), nullable=True)

    mentions: Mapped[list["EntryMention"]] = relationship(cascade="all, delete-orphan")
    hidden_from: Mapped[list["EntryHidden"]] = relationship(cascade="all, delete-orphan")

    @property
    def hidden_member_ids(self) -> set[str]:
        return {h.member_id for h in self.hidden_from}

    @property
    def allgemein_bekannt(self) -> bool:
        """Öffentlich und von niemandem verborgen – nur solche Einträge gelten für die KI als Spielerwissen."""
        return self.visibility == "public" and not self.hidden_from and not self.origin_analysis_id

    @property
    def aliases(self) -> list[str]:
        try:
            liste = json.loads(self.aliases_json or "[]")
        except ValueError:
            return []
        return [str(x) for x in liste if isinstance(x, str) and x.strip()]

    @property
    def alle_namen(self) -> list[str]:
        """Name und Aliasse, Name zuerst."""
        return [self.name, *self.aliases]


class EntryHidden(Base):
    """„Wer weiß was“: öffentlicher Eintrag, der vor einzelnen Mitgliedern verborgen ist."""

    __tablename__ = "entry_hidden"
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)


class EntryMention(Base):
    """In welcher Session ein Eintrag vorkam (wird beim Veröffentlichen befüllt)."""

    __tablename__ = "entry_mentions"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    entry_id: Mapped[str] = mapped_column(ForeignKey("entries.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    note: Mapped[str] = mapped_column(Text, default="")

    session: Mapped[GameSession] = relationship()


# ---------------------------------------------------------------- Stimmprofil
class VoiceProfile(Base):
    """Stimmprofil – enthält später nur den Stimmabdruck, nie Audio."""

    __tablename__ = "voice_profiles"
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), primary_key=True)
    status: Mapped[str] = mapped_column(String(16), default="none")
    created_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    sample_seconds: Mapped[float | None] = mapped_column(Float, nullable=True)
    learn_from_sessions: Mapped[bool] = mapped_column(Boolean, default=False)
    learned_session_count: Mapped[int] = mapped_column(Integer, default=0)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Biometrisch, verlässt den Server nie. Nur Zahlen, nie Audio.
    consent_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    base_embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, aus der eigenen Aufnahme
    learned_sum: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON, Summe gelernter Abdrücke
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)


# ---------------------------------------------------------------- Upload
class Upload(Base):
    """Ein Upload-Vorgang für eine Session (Teile liegen auf der Platte unter data/uploads/<id>/)."""

    __tablename__ = "uploads"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    source: Mapped[str] = mapped_column(String(16))  # table | discord
    state: Mapped[str] = mapped_column(String(16), default="open")  # open | completed | aborted
    chunk_size: Mapped[int] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    completed_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.85: vom Server selbst gemessene Länge der Aufnahme (nur bei eingeschalteter Abrechnung)
    dauer_sekunden: Mapped[float | None] = mapped_column(Float, nullable=True)
    # 0.4.28: Spannen der Aufnahme (nur table, nur von der SL) als JSON [[start, ende], …] in Sekunden seit 1970 –
    # nur gespeichert, wenn sie aufsteigend und ohne Überlappung sind (app/spielende.py)
    spans: Mapped[str | None] = mapped_column(Text, nullable=True)

    files: Mapped[list["UploadFile"]] = relationship(
        cascade="all, delete-orphan", order_by="UploadFile.position"
    )


class UploadFile(Base):
    __tablename__ = "upload_files"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    upload_id: Mapped[str] = mapped_column(ForeignKey("uploads.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    file_name: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(Integer)
    mime_type: Mapped[str] = mapped_column(String(100))
    track_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    chunk_count: Mapped[int] = mapped_column(Integer)


# ---------------------------------------------------------------- Warteschlange
class Worker(Base):
    """Ein Worker (z. B. PC mit Grafikkarte). Meldet sich mit eigenem Token, nicht mit einem Nutzerkonto."""

    __tablename__ = "workers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    name: Mapped[str] = mapped_column(String(100))
    token_hash: Mapped[str] = mapped_column(String(64))
    capabilities: Mapped[str] = mapped_column(String(200), default="asr,llm")  # kommagetrennt
    info: Mapped[str | None] = mapped_column(Text, nullable=True)  # zuletzt gemeldete Angaben (JSON)
    last_seen_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    revoked_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    paused: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # nimmt keine Aufträge an
    local: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")  # von der Verwaltung gestartet
    # In der Worker-App pausiert (seit wann) – der Worker meldet sich weiter, nimmt aber nichts an
    app_paused_since: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Job(Base):
    __tablename__ = "jobs"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    type: Mapped[str] = mapped_column(String(16))  # transcribe | summarize | document | voice_enroll | revise
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    upload_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    owner_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # voice_enroll
    state: Mapped[str] = mapped_column(String(16), default="queued", index=True)  # queued | leased | done | failed
    required_capability: Mapped[str] = mapped_column(String(16))  # asr | llm | llm_revise | embed
    engine: Mapped[str] = mapped_column(String(16), default="local")  # local | external
    lease_worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    lease_expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    attempts: Mapped[int] = mapped_column(Integer, default=0)
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    started_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    error_code: Mapped[str | None] = mapped_column(String(64), nullable=True)
    error_message: Mapped[str | None] = mapped_column(Text, nullable=True)
    messwerte: Mapped[str | None] = mapped_column(Text, nullable=True)  # 0.4.114: JSON, app/messwerte.py
    # 0.4.85 (Abrechnung): Die SL hat den Auftrag über ihrem Betrag für „Verarbeiten“ freigegeben
    kosten_freigegeben_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.18: Auswertung eines Charakterbogens bzw. Hintergrunds (Art „document“, ohne document_id)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)


# ---------------------------------------------------------------- Ergebnis der Transkription
class Speaker(Base):
    """Erkannte Stimme einer Session. Hörprobe und Stimmabdruck bleiben nur bis zur bestätigten Zuordnung."""

    __tablename__ = "speakers"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(String(50))
    raw_label: Mapped[str] = mapped_column(String(50))  # Kennung des Workers, z. B. SPEAKER_01
    speaking_seconds: Mapped[float] = mapped_column(Float, default=0)
    sample_text: Mapped[str] = mapped_column(Text, default="")
    sample_path: Mapped[str | None] = mapped_column(String(300), nullable=True)
    embedding: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-Liste, biometrisch – kurzlebig
    embedding_model: Mapped[str | None] = mapped_column(String(100), nullable=True)  # Modell@Fassung
    suggested_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    suggested_guest_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 0.4.24: Gast-Vorschlag
    confidence: Mapped[float] = mapped_column(Float, default=0)
    source: Mapped[str] = mapped_column(String(16), default="none")
    assigned_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    assigned_guest_name: Mapped[str | None] = mapped_column(String(200), nullable=True)  # 0.4.7: Gast benannt
    # 0.4.21: bis zu drei Textproben über die Runde verteilt, JSON [{start, end, text}]; Hörprobe je Probe liegt
    # unter storage.sample_path(session, speaker, index). Leer/None = nur sample_text (ältere Worker).
    samples_json: Mapped[str | None] = mapped_column("samples", Text, nullable=True)


class TranscriptSegment(Base):
    __tablename__ = "transcript_segments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    position: Mapped[int] = mapped_column(Integer)
    start: Mapped[float] = mapped_column(Float)
    end: Mapped[float] = mapped_column(Float)
    speaker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    text: Mapped[str] = mapped_column(Text)
    # 0.4.6: unsicher erkannte Wörter [{"word", "start", "score", "anfang"}] – Grundlage für uncertain-terms
    unsicher: Mapped[str | None] = mapped_column(Text, nullable=True)


class UsageLog(Base):
    """Verbrauch pro Verarbeitung – Grundlage für GET /campaigns/{id}/usage und spätere Abrechnung."""

    __tablename__ = "usage_log"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(String(36), index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 0.4.18
    kind: Mapped[str] = mapped_column(String(16))  # transcription | summary | document
    engine: Mapped[str] = mapped_column(String(16), default="local")
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    worker_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    audio_seconds: Mapped[float] = mapped_column(Float, default=0)
    compute_seconds: Mapped[float] = mapped_column(Float, default=0)
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_cents: Mapped[int] = mapped_column(Integer, default=0)
    billed_to: Mapped[str] = mapped_column(String(16), default="gm")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Ergebnis der Zusammenfassung
class Recap(Base):
    """Recap einer Session. Für Spieler erst sichtbar, wenn die Session veröffentlicht ist."""

    __tablename__ = "recaps"
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), primary_key=True)
    title: Mapped[str] = mapped_column(String(300))
    text: Mapped[str] = mapped_column(Text)
    open_threads: Mapped[str] = mapped_column(Text, default="[]")  # JSON-Liste
    model: Mapped[str | None] = mapped_column(String(100), nullable=True)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)  # von der SL bearbeitet
    review: Mapped[str | None] = mapped_column(Text, nullable=True)  # 0.4.6: Prüfteil als JSON, nur für die SL
    # 0.4.15: offener Korrektur-Entwurf als JSON (Hinweis, Auftrag, Änderungen), nur für die SL, geht nie in den Umzug
    revision: Mapped[str | None] = mapped_column(Text, nullable=True)
    # 0.4.17: Teile im Kapitel als JSON [{title, firstParagraph}]; None = ohne Teile (app/kapitelteile.py)
    parts: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Proposal(Base):
    """Bibel-Vorschlag aus einer Session (später auch aus SL-Unterlagen). Spieler sehen Vorschläge nie."""

    __tablename__ = "proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    session_id: Mapped[str | None] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    position: Mapped[int] = mapped_column(Integer, default=0)
    entry_type: Mapped[str] = mapped_column(String(16))
    action: Mapped[str] = mapped_column(String(16))  # create | update | reveal
    target_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    title: Mapped[str] = mapped_column(String(300))
    detail: Mapped[str] = mapped_column(Text, default="")
    gm_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    suggested_visibility: Mapped[str] = mapped_column(String(16), default="gm_only")
    hidden_member_ids_json: Mapped[str] = mapped_column("hidden_member_ids", Text, default="[]")
    public_suggested: Mapped[bool] = mapped_column(Boolean, default=False)
    visibility_reason: Mapped[str | None] = mapped_column(Text, nullable=True)
    confidence: Mapped[float] = mapped_column(Float, default=0.5)
    flags: Mapped[str] = mapped_column(Text, default="[]")  # JSON-Liste
    evidence: Mapped[str] = mapped_column(Text, default="[]")  # JSON-Liste {start|page, quote}
    decision: Mapped[str] = mapped_column(String(16), default="open")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    # 0.4.7: mitgebrachte Welt (source character) – wirkt beim Entscheiden sofort
    source: Mapped[str | None] = mapped_column(String(16), nullable=True)  # None = aus session_id/document_id
    origin_character_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    origin_version: Mapped[int | None] = mapped_column(Integer, nullable=True)
    submitted_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.18: bei source character aus einer Auswertung (Charakterbogen oder Hintergrund); None = mitgebrachte Welt
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    relation: Mapped[str | None] = mapped_column(String(120), nullable=True)  # 0.4.19, mitgebrachte Welt (nur Anzeige)
    # 0.4.21: Dublettenprüfung ohne Sprachmodell (JSON {entryId, name, matchedName, entryType, kind}) und die
    # Entscheidung der SL, bei diesem Eintrag zu ergänzen statt neu anzulegen (nur action create)
    match_json: Mapped[str | None] = mapped_column("match", Text, nullable=True)
    merge_into_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    # 0.4.26: bei Beute aus Runden – bei welcher Figur der Gegenstand danach ist (aktives Mitglied mit Rolle player)
    # bzw. dass ihn keine Figur mehr hat (nur update bzw. mit merge_into_entry_id); nie beides
    holder_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    holder_cleared: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")


class GmNotice(Base):
    """Hinweis an die SL (0.4.7), z. B. Neuzugang bei teilweise verborgenen Einträgen. Spieler sehen ihn nie."""

    __tablename__ = "gm_notices"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    code: Mapped[str] = mapped_column(String(48))
    member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    entry_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON-Liste
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True)  # 0.4.18: character_analysis_pending


class CharacterAnalysis(Base):
    """Auswertung eines Charakterbogens oder Hintergrunds (Schnittstelle 0.4.18, app/charakterauswertung.py).
    Wartet zuerst auf die Zustimmung der SL. Der auszuwertende Text liegt nur bis zum Ende der Auswertung hier
    (`abschnitte`, beim Hintergrund der Stand zum Zeitpunkt der Anfrage). Geht beim Umzug nicht mit."""

    __tablename__ = "character_analyses"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(String(36), index=True)
    source: Mapped[str] = mapped_column(String(16))  # sheet | backstory
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    title: Mapped[str] = mapped_column(String(300))
    # awaiting_gm | rejected | queued | processing | done | failed
    state: Mapped[str] = mapped_column(String(16), default="awaiting_gm", index=True)
    uses_cloud: Mapped[bool] = mapped_column(Boolean, default=False)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    pages_limited: Mapped[bool] = mapped_column(Boolean, default=False)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-Schlüssel wie status_message
    abschnitte: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON [{seite, text}] bis zum Ende
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


# ---------------------------------------------------------------- Umzug (0.4.8)
class CampaignExport(Base):
    """Kampagne als Datei taleward-kampagne/1 (app/umzug.py). Die Datei liegt unter data/umzug/exporte/<id>.zip."""

    __tablename__ = "campaign_exports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    requested_by_user_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="queued")  # queued | processing | ready | failed
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    size_bytes: Mapped[int | None] = mapped_column(Integer, nullable=True)
    file_name: Mapped[str | None] = mapped_column(String(300), nullable=True)
    consented_member_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON-Liste, Stand beim Start
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    expires_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class CampaignImport(Base):
    """Import einer Datei taleward-kampagne/1. Teile liegen unter data/umzug/importe/<id>/."""

    __tablename__ = "campaign_imports"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    file_name: Mapped[str] = mapped_column(String(300))
    size_bytes: Mapped[int] = mapped_column(Integer)
    chunk_size: Mapped[int] = mapped_column(Integer)
    chunk_count: Mapped[int] = mapped_column(Integer)
    state: Mapped[str] = mapped_column(String(16), default="uploading")  # uploading | processing | done | failed
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    open_seats: Mapped[int | None] = mapped_column(Integer, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    finished_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


# ---------------------------------------------------------------- SL-Unterlagen
class CampaignDocument(Base):
    """Hochgeladene Unterlage der SL (PDF, Word, Text). Nur die SL sieht sie. Der ausgelesene Text liegt neben der
    Datei (data/unterlagen/<id>/text.json), die Vorschläge in `proposals` (document_id)."""

    __tablename__ = "documents"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(300))
    file_name: Mapped[str] = mapped_column(String(300))
    kind: Mapped[str] = mapped_column(String(16))  # handout | gm | mixed | character_sheet
    size_bytes: Mapped[int] = mapped_column(Integer)
    page_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(24), default="queued")  # queued|processing|awaiting_review|done|failed
    progress: Mapped[float | None] = mapped_column(Float, nullable=True)
    message: Mapped[str | None] = mapped_column(Text, nullable=True)  # JSON-Schlüssel wie status_message
    world_info_suggestion: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_by_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class ChapterPlan(Base):
    """Kapitelplan der SL (Schnittstelle 0.4.12): Szenen mit Notiz und verknüpften Einträgen, Namen als Schreibhilfe,
    verknüpfte Unterlagen. Nur die SL sieht ihn; er fließt nie in Kapitel, Vorschläge, Gegenprüfung oder Probelauf –
    einzige Ausnahme sind `names` als Schreibhilfe der Transkription des Kapitels mit derselben Nummer.
    Szenen, Namen und Unterlagen liegen als JSON im Plan (immer als ganze Liste ersetzt)."""

    __tablename__ = "chapter_plans"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    title: Mapped[str] = mapped_column(String(120))
    session_number: Mapped[int | None] = mapped_column(Integer, nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="draft")  # draft | ready | played
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    table_notes: Mapped[str | None] = mapped_column(Text, nullable=True)  # 0.4.13: Notizzettel am SL-Schirm
    scenes: Mapped[str] = mapped_column(Text, default="[]")  # JSON [{id, title, notes, entryIds, state, links}]
    names: Mapped[str] = mapped_column(Text, default="[]")  # JSON [str]
    document_ids: Mapped[str] = mapped_column(Text, default="[]")  # JSON [str]
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Miteinander (Kommentare, Termine)
class Comment(Base):
    """Kommentar zu einem Kapitel: öffentlich (recipient None) oder privat. Fließt nie in Recaps oder Vorschläge ein.
    0.4.26: privat an mehrere – alle Empfänger stehen in CommentRecipient (auch der erste), recipient_member_id ist der
    erste Empfänger für ältere Apps und zeigt, dass der Kommentar privat ist."""

    __tablename__ = "comments"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    author_member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    recipient_member_id: Mapped[str | None] = mapped_column(
        ForeignKey("members.id", ondelete="CASCADE"), nullable=True, index=True)
    text: Mapped[str] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    edited_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class CommentRecipient(Base):
    """0.4.26: Empfänger eines privaten Kommentars (einer oder mehrere)."""

    __tablename__ = "comment_recipients"
    comment_id: Mapped[str] = mapped_column(ForeignKey("comments.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True, index=True)
    position: Mapped[int] = mapped_column(Integer, default=0, server_default="0")


class DatePoll(Base):
    __tablename__ = "date_polls"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    status: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | closed | cancelled
    note: Mapped[str | None] = mapped_column(Text, nullable=True)
    chosen_option_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    ended_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)

    options: Mapped[list["DateOption"]] = relationship(cascade="all, delete-orphan", order_by="DateOption.starts_at")


class DateOption(Base):
    __tablename__ = "date_options"
    __table_args__ = (UniqueConstraint("poll_id", "starts_at"),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    poll_id: Mapped[str] = mapped_column(ForeignKey("date_polls.id", ondelete="CASCADE"), index=True)
    starts_at: Mapped[datetime] = mapped_column(UTCDateTime)
    proposed_by_member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"))
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)

    votes: Mapped[list["DateVote"]] = relationship(cascade="all, delete-orphan", order_by="DateVote.created_at")


class DateVote(Base):
    __tablename__ = "date_votes"
    option_id: Mapped[str] = mapped_column(ForeignKey("date_options.id", ondelete="CASCADE"), primary_key=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), primary_key=True)
    answer: Mapped[str] = mapped_column(String(8))  # yes | maybe | no
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Bekannte Geräte (0.4.141)
class BekanntesGeraet(Base):
    """Browser, in dem sich ein Konto schon einmal in der Verwaltung oder auf der Guthaben-Seite angemeldet hat. Nur die
    SHA-256 des Cookie-Werts; eine Anmeldung aus einem bekannten Browser löst keine Hinweis-Mail mehr aus. Räumt sich nach
    GERAET_TAGE Tagen ohne Anmeldung auf (app/geraete.py)."""

    __tablename__ = "bekannte_geraete"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 hex des Cookie-Werts
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    ort: Mapped[str] = mapped_column(String(16))  # verwaltung | guthaben (wo zuerst gesehen)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)


# ---------------------------------------------------------------- Spielende tragen bei (0.4.28)
class PlayerProposal(Base):
    """Vorschlag einer Spielerin für die Bibel. Geht nie an ein Sprachmodell und nie ins Kapitel; erst die Entscheidung
    der SL macht einen Eintrag. Nie im Umzug. text_sichtbar: beim Übernehmen stand der Text im öffentlichen Teil (nicht nur
    in SL-Notizen) – Grundlage dafür, was die Urheberin sieht (S56, app/spielende.py)."""

    __tablename__ = "player_proposals"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    author_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    entry_type: Mapped[str] = mapped_column(String(16))
    title: Mapped[str] = mapped_column(String(120))
    detail: Mapped[str] = mapped_column(Text, default="")
    target_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    state: Mapped[str] = mapped_column(String(16), default="open", index=True)  # open | accepted | rejected | withdrawn
    result_entry_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    text_sichtbar: Mapped[bool] = mapped_column(Boolean, default=False, server_default="0")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow, index=True)
    decided_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True, index=True)


class Bookmark(Base):
    """Gemerkte Stelle einer Runde – nur für die Person selbst (auch nicht für die SL). Nie an ein Sprachmodell."""

    __tablename__ = "bookmarks"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    session_id: Mapped[str] = mapped_column(ForeignKey("sessions.id", ondelete="CASCADE"), index=True)
    member_id: Mapped[str] = mapped_column(ForeignKey("members.id", ondelete="CASCADE"), index=True)
    at: Mapped[datetime] = mapped_column(UTCDateTime)
    note: Mapped[str | None] = mapped_column(String(280), nullable=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Passkeys und vertraute Geräte (0.4.143)
class Passkey(Base):
    """Passkey (WebAuthn) eines Verwaltungs- oder SL-Kontos als zweiter Faktor neben dem Code (app/passkeys.py).
    Gespeichert sind nur die Credential-ID, der öffentliche Schlüssel (COSE) und der Signaturzähler – nichts
    Biometrisches, nichts Geheimes. Gültig nur für die RP-ID (Hostname der eingetragenen öffentlichen Adresse)."""

    __tablename__ = "passkeys"
    id: Mapped[str] = mapped_column(String(1024), primary_key=True)  # Credential-ID, base64url
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    public_key: Mapped[str] = mapped_column(Text)  # COSE, base64url
    sign_count: Mapped[int] = mapped_column(Integer, default=0)
    transports: Mapped[str] = mapped_column(Text, default="[]")  # JSON-Liste
    rp_id: Mapped[str] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(40))
    auffaellig: Mapped[bool] = mapped_column(Boolean, default=False)  # Signaturzähler ging zurück (geklont?)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    last_used_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class VertrautesGeraet(Base):
    """Browser, dem ein Konto nach dem zweiten Faktor 30 Tage vertraut (S44, app/vertraut.py): dort entfällt beim
    Anmelden nur der Code, das Passwort bleibt. Nur die SHA-256 des Merkzeichens; gebunden an Konto, token_version
    und Faktor-Fassung (Passkey entfernt, Faktor aus oder neu, neue Wiederherstellungscodes)."""

    __tablename__ = "vertraute_geraete"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 hex von Merkzeichen und Bereich
    user_id: Mapped[str] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), index=True)
    bereich: Mapped[str] = mapped_column(String(16))  # verwaltung | guthaben
    token_version: Mapped[int] = mapped_column(Integer, default=0)
    faktor_fassung: Mapped[int] = mapped_column(Integer, default=0)
    bezeichnung: Mapped[str] = mapped_column(String(60), default="")
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    last_used_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Einmal-Tokens (0.4.0)
class EinmalToken(Base):
    """Kurzlebige Einmal-Werte: E-Mail bestätigen, Anmeldung mit Dienst (Zustand, Ticket, Registrierung, Verbinden).
    Gespeichert wird nur die SHA-256 des Werts; nach Gebrauch oder Ablauf gelöscht."""

    __tablename__ = "einmal_tokens"
    id: Mapped[str] = mapped_column(String(64), primary_key=True)  # SHA-256 hex des Werts
    art: Mapped[str] = mapped_column(String(24), index=True)
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="CASCADE"), nullable=True, index=True)
    daten: Mapped[str] = mapped_column(Text, default="{}")
    expires_at: Mapped[datetime] = mapped_column(UTCDateTime, index=True)
    created_at: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


# ---------------------------------------------------------------- Abrechnung mit Guthaben (0.4.85, app/abrechnung.py)
class Guthaben(Base):
    """Wer zahlt: ein Konto (SL) oder eine Organisation (Topf). Einen Kontostand gibt es nicht – der Saldo ist die Summe
    der Buchungen. Nach dem Löschen des Kontos bleibt die Zeile ohne Bezug (user_id NULL), die Buchungen bleiben."""

    __tablename__ = "guthaben"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    art: Mapped[str] = mapped_column(String(16))  # konto | organisation
    user_id: Mapped[str | None] = mapped_column(ForeignKey("users.id", ondelete="SET NULL"), nullable=True,
                                                unique=True)
    organization_id: Mapped[str | None] = mapped_column(ForeignKey("organizations.id", ondelete="SET NULL"),
                                                        nullable=True, unique=True)
    # Einstellungen der SL bzw. der Verwalter (Änderungen stehen im Protokoll)
    freigabe_ab_cent: Mapped[int] = mapped_column(Integer, default=200)
    bogen_seiten: Mapped[int] = mapped_column(Integer, default=20)
    boegen_je_spieler: Mapped[int] = mapped_column(Integer, default=3)
    # 0.4.133: nach einer Rückbuchung gesperrt, bis der Betreiber entscheidet (keine neuen Verarbeitungen, kein Aufladen)
    gesperrt_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.135: Hinweis-Mails – schon gemeldete Anlässe (kommagetrennt, fallen weg, sobald der Anlass vorbei ist) und
    # Zeitpunkt der letzten Mail (höchstens eine je 24 Stunden)
    hinweise: Mapped[str | None] = mapped_column(String(64), nullable=True)
    hinweis_mail_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Buchung(Base):
    """Buchungsbuch: nur Einfügen. Ändern und Löschen verhindern Trigger in der Datenbank (Migration b3d9e1f7a2c4).
    Beträge in ganzen Cent mit Vorzeichen (+ gutschreiben, − verbrauchen), Währung fest EUR."""

    __tablename__ = "buchungen"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    guthaben_id: Mapped[str] = mapped_column(ForeignKey("guthaben.id"), index=True)
    art: Mapped[str] = mapped_column(String(16))  # zahlung|gutschrift|gutschein|verbrauch|rueckbuchung|erstattung|korrektur
    betrag_cent: Mapped[int] = mapped_column(Integer)
    waehrung: Mapped[str] = mapped_column(String(3), default="EUR")
    job_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gutschein_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    gegen_buchung_id: Mapped[str | None] = mapped_column(String(36), nullable=True, unique=True)
    ereignis_id: Mapped[str | None] = mapped_column(String(200), nullable=True, unique=True)  # Zahlungsanbieter
    grund: Mapped[str | None] = mapped_column(Text, nullable=True)
    von: Mapped[str] = mapped_column(String(36), default="system")  # Konto des Verwalters oder „system“
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class KostenAuftrag(Base):
    """Je kostenpflichtigem Auftrag eine Zeile, angelegt beim Start (queue.claim) und beim Ende abgerechnet. Höchstens
    ein laufender je Guthaben – das sichert ein eindeutiger Teilindex in der Datenbank selbst."""

    __tablename__ = "kosten_auftraege"
    __table_args__ = (Index("ix_kosten_auftraege_ein_laufender", "guthaben_id", unique=True,
                            sqlite_where=text("zustand = 'laeuft'")),)
    job_id: Mapped[str] = mapped_column(String(36), primary_key=True)
    guthaben_id: Mapped[str] = mapped_column(ForeignKey("guthaben.id"), index=True)
    weg: Mapped[str] = mapped_column(String(16))  # worker | zentrale | extern
    art: Mapped[str] = mapped_column(String(16))  # Auftragsart (transcribe, summarize, revise, document)
    menge: Mapped[int] = mapped_column(Integer, default=0)  # Sekunden Aufnahme bzw. Seiten bzw. 1
    # 0.4.87: Bezug auch ohne den Auftrag – Runde, Unterlage oder Kampagne können vor dem Abrechnen gelöscht werden
    campaign_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    session_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    document_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    analysis_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)  # 0.4.18
    schaetzung_cent: Mapped[int] = mapped_column(Integer, default=0)
    zustand: Mapped[str] = mapped_column(String(16), default="laeuft")  # laeuft | abgerechnet
    betrag_cent: Mapped[int | None] = mapped_column(Integer, nullable=True)
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    abgerechnet_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)


class Gutschein(Base):
    """Gutscheincode über einen festen Betrag. Gespeichert wird nur die SHA-256 des Codes."""

    __tablename__ = "gutscheine"
    # 0.4.87: je Guthaben höchstens ein eingelöster Gutschein derselben Aktion – in der Datenbank selbst
    __table_args__ = (Index("ix_gutscheine_aktion_eingeloest", "aktion", "eingeloest_von", unique=True,
                            sqlite_where=text("eingeloest_von IS NOT NULL")),)
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    code_hash: Mapped[str] = mapped_column(String(64), unique=True)
    aktion: Mapped[str] = mapped_column(String(100), index=True)
    betrag_cent: Mapped[int] = mapped_column(Integer)
    gueltig_bis: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    eingeloest_von: Mapped[str | None] = mapped_column(String(36), nullable=True)  # Guthaben
    eingeloest_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    angelegt_von: Mapped[str] = mapped_column(String(36))
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class AbrechnungProtokoll(Base):
    """Wer hat wann was an der Abrechnung geändert (Einstellungen, Gutschrift, Gutscheine, Zahler). Nur Einfügen."""

    __tablename__ = "abrechnung_protokoll"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    von: Mapped[str] = mapped_column(String(36))
    guthaben_id: Mapped[str | None] = mapped_column(String(36), nullable=True, index=True)
    was: Mapped[str] = mapped_column(String(40))
    daten: Mapped[str] = mapped_column(Text, default="{}")
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class EinstellungsProtokoll(Base):
    """0.4.25: Änderungen an den Kampagnen-Einstellungen (je Feld alt/neu). Gelöscht mit der Kampagne."""

    __tablename__ = "kampagnen_einstellungen_protokoll"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    campaign_id: Mapped[str] = mapped_column(ForeignKey("campaigns.id", ondelete="CASCADE"), index=True)
    feld: Mapped[str] = mapped_column(String(40))
    alt: Mapped[str | None] = mapped_column(String(20), nullable=True)  # JSON-Wert (true/false/Zahl/null)
    neu: Mapped[str | None] = mapped_column(String(20), nullable=True)
    fassung: Mapped[int] = mapped_column(Integer)
    von_member_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    vom_betreiber: Mapped[bool] = mapped_column(Boolean, default=False)
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)


class Bezahlvorgang(Base):
    """0.4.133: Aufladen über einen Zahlungsanbieter (app/zahlung.py). Angelegt vor der Weiterleitung; gutgeschrieben
    wird nur, was der Anbieter bestätigt und genau zum Vorgang passt."""

    __tablename__ = "bezahlvorgaenge"
    id: Mapped[str] = mapped_column(String(36), primary_key=True, default=new_id)
    guthaben_id: Mapped[str] = mapped_column(ForeignKey("guthaben.id"), index=True)
    von: Mapped[str] = mapped_column(String(36), index=True)  # Konto, das aufgeladen hat
    anbieter: Mapped[str] = mapped_column(String(16))  # stripe | paypal
    netto_cent: Mapped[int] = mapped_column(Integer)  # wird gutgeschrieben
    brutto_cent: Mapped[int] = mapped_column(Integer)  # wird bezahlt (mit Gebühr)
    zustand: Mapped[str] = mapped_column(String(16), default="offen")  # offen|bezahlt|abgebrochen|fehlgeschlagen|abweichung
    anbieter_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)  # Sitzung bzw. Auftrag
    zahlung_ref: Mapped[str | None] = mapped_column(String(100), nullable=True, index=True)  # Zahlung bzw. Abbuchung
    erstattet_cent: Mapped[int] = mapped_column(Integer, default=0)
    buchung_id: Mapped[str | None] = mapped_column(String(36), nullable=True)
    angelegt: Mapped[datetime] = mapped_column(UTCDateTime, default=utcnow)
    abgeschlossen: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    # 0.4.138: Häkchen „sofort nutzbar“ (Widerruf) – Zeitpunkt und Fassung der Bedingungen und der Widerrufsbelehrung
    zustimmung_at: Mapped[datetime | None] = mapped_column(UTCDateTime, nullable=True)
    zustimmung_fassung: Mapped[str | None] = mapped_column(String(80), nullable=True)
