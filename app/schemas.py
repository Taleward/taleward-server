"""Pydantic-Modelle, Feldnamen 1:1 wie in contract/session-chronik-api.yaml (camelCase).

Hinweis: Einige Felder werden je nach Betrachter WEGGELASSEN (nicht null gesetzt), z. B.
characterBackstory und gmNotes für Spieler. Die betroffenen Endpunkte antworten deshalb mit
response_model_exclude_unset=True, und die Ausgabe-Funktionen in services.py setzen jedes andere
Feld ausdrücklich.
"""
from datetime import datetime, timezone
from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator
from pydantic.alias_generators import to_camel

Role = Literal["gm", "player"]
Visibility = Literal["public", "gm_only"]
EntryType = Literal["npc", "location", "quest", "item", "faction", "other", "pc"]  # pc ab 0.4.7, nur vom Server
UUID_MUSTER = r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
CharacterStatus = Literal["active", "retired", "deceased"]
ContentLanguage = Literal["de", "en"]
GameSystem = Literal["dsa", "dnd", "pathfinder", "cthulhu", "shadowrun", "splittermond", "other"]
CoverPreset = Literal["meadow", "forest", "desert", "city", "cyber", "mountains", "coast", "swamp",
                      "dungeon", "space", "castle",
                      # ab 0.4.3
                      "dark-fantasy", "moonwood", "ancient-ruins", "tavern", "battlefield", "frozen-north",
                      "arcane-ruins", "fairy-wilds", "underworld", "storm-coast", "steampunk", "post-apocalypse",
                      "western", "noir", "space-opera", "orient", "necropolis", "manor", "riverside-mystery"]
COVER_PRESETS: tuple[str, ...] = CoverPreset.__args__  # type: ignore[attr-defined]
ProcessingState = Literal[
    "created", "uploading", "queued", "transcribing", "awaiting_speakers",
    "summarizing", "awaiting_review", "published", "failed",
]


class ApiModel(BaseModel):
    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True, extra="ignore")


def _as_utc(v: datetime | None) -> datetime | None:
    if v is None:
        return None
    if v.tzinfo is None:
        return v.replace(tzinfo=timezone.utc)
    return v.astimezone(timezone.utc)


# ---------- Server / Auth ----------
class ServerInfoOut(ApiModel):
    name: str
    operator: str
    contact: str | None
    api_version: str
    registration: Literal["invite_only", "code", "open", "closed"]  # code, open ab 0.4.23
    gm_approval: Literal["required", "none"] = "none"  # 0.4.23: eigene Kampagnen nur mit SL-Freigabe?
    auth_methods: list[Literal["password", "oidc", "passkey"]]
    privacy_policy_url: str | None
    min_age: int
    min_app_version: str | None = None
    latest_app_version: str | None = None
    app_download_url: str | None = None
    release_notes: str | None = None
    app_download_sha256: str | None = None
    app_download_size_bytes: int | None = None
    external_transcription: str | None
    external_transcription_mode: Literal["fallback", "primary"] | None = None
    cloud_summary: str | None = None
    cloud_summary_info: "CloudProviderInfoOut | None" = None  # 0.4.11
    external_transcription_info: "CloudProviderInfoOut | None" = None  # 0.4.11
    auth_providers: list["AuthProviderOut"] = []
    password_reset: bool = False
    audio_retention: "AudioRetentionOut | None" = None
    public_url: str | None = None  # 0.4.16: Adresse für andere Geräte (Einladungslink, wenn die App über localhost läuft)
    server_id: str | None = None  # 0.4.16: feste Kennung dieses Servers (wie `aud` im Token)
    legal_notice_url: str | None = None  # 0.4.22: Impressum (Vorlage /impressum oder eigener Link), null = keins
    terms_url: str | None = None  # 0.4.22: Nutzungsbedingungen, ab 0.4.22 immer gesetzt
    terms_version: int | None = None  # 0.4.22
    # 0.4.25: {mode, currency, topUpUrl} bei eingeschaltetem Guthaben, sonst null. topUpUrl fehlt ohne eingetragene
    # öffentliche Adresse (G15) – darum hier ein dict statt eines Modells.
    billing: dict | None = None


class InvitePreviewOut(ApiModel):
    """0.4.10: Vorschau einer Einladung ohne Anmeldung – nur, was /einladung/{code} ohnehin zeigt."""
    campaign_title: str
    seat_character_name: str | None = None
    expires_at: datetime | None = None


class CloudProviderInfoOut(ApiModel):
    """0.4.11: Wer Text (Zusammenfassung) bzw. Aufnahmen (Transkription) verarbeitet – ohne sprachabhängige Wörter."""
    id: str
    name: str
    region: Literal["eu", "non_eu"]
    country: str | None = None


class AudioRetentionOut(ApiModel):
    mode: Literal["until_release", "immediate"]
    max_days: int | None


class AuthProviderOut(ApiModel):
    id: Literal["google", "discord", "apple", "microsoft"]
    name: str


class LoginRequest(ApiModel):
    username: str
    password: str
    device_id: Any = None  # 0.4.27: freiwillig; ungültige Werte (auch kein Text) ignoriert der Server (app/geraete.py)


class UserOut(ApiModel):
    """Nur für die Person selbst (Login, /me, Registrierung, Tausch) – nie für andere."""
    id: str
    username: str
    display_name: str
    email: str | None = None
    email_pending: str | None = None
    has_password: bool = True
    providers: list[Literal["google", "discord", "apple", "microsoft"]] = []
    terms_accepted_version: int | None = None  # 0.4.22: null = noch nie zugestimmt
    terms_accepted_at: datetime | None = None
    gm_permission: Literal["granted", "none", "pending", "rejected"] = "none"  # 0.4.23
    gm_decided_at: datetime | None = None  # 0.4.23: letzte Entscheidung der Verwaltung


class RegisterRequest(ApiModel):
    """Zwei Formen (0.4.0): mit username + password oder mit registrationToken aus /auth/oidc/exchange."""
    invite_code: str | None = Field(default=None, max_length=64)  # 0.4.23: nur noch bei invite_only Pflicht
    registration_code: str | None = Field(default=None, max_length=200)  # 0.4.23: bei registration code
    username: str | None = Field(default=None, max_length=200)
    display_name: str = Field(max_length=300)
    password: str | None = Field(default=None, max_length=500)
    registration_token: str | None = Field(default=None, max_length=200)
    accept_privacy: bool = False
    age_confirmed: bool = False
    accept_terms: bool = False  # 0.4.22: Pflicht
    terms_version: int | None = Field(default=None, ge=1)  # 0.4.22: Pflicht


class TermsAcceptIn(ApiModel):
    accept: bool
    version: int = Field(ge=1)


class DeleteCampaignRequest(ApiModel):
    confirm_title: str = Field(max_length=300)


class DeleteMeRequest(ApiModel):
    password: str | None = Field(default=None, max_length=500)
    confirm_username: str | None = Field(default=None, max_length=200)


class PasswordResetRequest(ApiModel):
    login: str = Field(max_length=300)


class EmailRequest(ApiModel):
    email: str = Field(max_length=300)


class PasswordChangeRequest(ApiModel):
    current_password: str | None = Field(default=None, max_length=500)
    new_password: str = Field(max_length=500)


class OidcExchangeRequest(ApiModel):
    ticket: str = Field(max_length=200)
    verifier: str = Field(max_length=200)
    device_id: Any = None  # 0.4.27, wie bei LoginRequest


class OidcExchangeResponse(ApiModel):
    status: Literal["ok", "register", "email_in_use"]
    access_token: str | None = None
    expires_at: datetime | None = None
    user: UserOut | None = None
    registration_token: str | None = None
    suggested_display_name: str | None = None
    email: str | None = None
    provider: str | None = None


class LinkTokenOut(ApiModel):
    link_token: str


class LoginResponse(ApiModel):
    access_token: str
    expires_at: datetime
    user: UserOut


class OrganizationOut(ApiModel):
    id: str
    name: str
    my_role: Literal["admin", "member"]


class OrgRef(ApiModel):
    id: str
    name: str


# ---------- Stimmprofil ----------
class VoiceProfileOut(ApiModel):
    status: Literal["none", "processing", "ready", "failed"]
    created_at: datetime | None
    sample_seconds: float | None
    learn_from_sessions: bool
    learned_session_count: int
    message: str | None


class VoiceProfilePatch(ApiModel):
    learn_from_sessions: bool | None = None


# ---------- Kampagnen ----------
class MemberOut(ApiModel):
    id: str
    user_id: str
    display_name: str
    character_name: str | None
    role: Role
    recording_consent_at: datetime | None
    character_summary: str | None
    character_backstory: str | None = None  # wird für Unbefugte weggelassen
    portrait_updated_at: datetime | None
    deleted_at: datetime | None = None
    left_at: datetime | None = None
    character_id: str | None = None  # 0.4.7
    character_version: int | None = None
    character_status: CharacterStatus | None = None
    character_nickname: str | None = None
    move_consent_at: datetime | None = None  # 0.4.8
    open_seat: bool = False
    character_pronouns: str | None = None  # 0.4.28, für alle Mitglieder
    counters: list["CounterValue"] | None = None  # 0.4.28: nur Person selbst und SL, sonst fehlt das Feld


class UnreadOut(ApiModel):
    recaps: int
    comments: int
    bible: int


class CampaignSummaryOut(ApiModel):
    id: str
    title: str
    organization: OrgRef | None
    my_role: Role
    my_character_name: str | None
    member_count: int
    archived_at: datetime | None = None
    published_session_count: int
    pending_review_count: int
    open_character_proposals: int = 0  # 0.4.7, nur für die SL befüllt
    last_published_at: datetime | None
    cover_preset: CoverPreset | None
    unread: UnreadOut
    cover_image_updated_at: datetime | None
    next_session_at: datetime | None
    date_poll_needs_my_vote: bool


class Link(ApiModel):
    """0.4.13: Verweis nach draußen (Prüfung der Adresse in app/links.py)."""
    id: str = Field(pattern=UUID_MUSTER)
    label: str = Field(min_length=1, max_length=60)
    url: str = Field(min_length=1, max_length=2000)
    shared: bool = False


class CampaignOut(CampaignSummaryOut):
    description: str
    language: ContentLanguage
    system: GameSystem | None
    system_name: str | None
    world_info: str | None
    allow_external_transcription: bool
    allow_cloud_summary: bool = False
    allow_character_analysis: bool = False  # 0.4.18
    character_sheet_limits: "SheetLimitsOut | None" = None  # 0.4.19, für alle Mitglieder
    hotwords: list[str] | None = None  # 0.4.6: nur für die SL, für Spieler weggelassen
    links: list[Link] = []  # 0.4.13: Spieler nur shared
    gm_notices: list["GmNoticeOut"] | None = None  # 0.4.7: nur für die SL, für Spieler weggelassen
    settings_version: int | None = None  # 0.4.25, für alle Mitglieder
    billing: "CampaignBillingOut | None" = None  # 0.4.25, nur für die SL bei eingeschaltetem Guthaben
    open_player_proposals: int | None = None  # 0.4.28, nur für die SL, für Spielende weggelassen
    live_session_id: str | None = None  # 0.4.28, nur SL und Anwesende der Runde, sonst null
    members: list[MemberOut]


class SheetLimitsOut(ApiModel):
    max_sheets: int
    max_pages: int


class GmNoticeOut(ApiModel):
    id: str
    code: Literal["hidden_entries_for_newcomer", "seat_claimed", "character_orphaned", "member_joined",
                  "character_analysis_pending"]
    analysis_id: str | None = None  # 0.4.18, bei character_analysis_pending
    member_id: str | None
    entry_ids: list[str]
    created_at: datetime


class CharacterAnalysisOut(ApiModel):
    """0.4.18: Auswertung eines Charakterbogens oder Hintergrunds. proposalCount nur für die SL."""
    id: str
    campaign_id: str
    member_id: str
    source: Literal["sheet", "backstory"]
    document_id: str | None
    title: str
    state: Literal["awaiting_gm", "rejected", "queued", "processing", "done", "failed"]
    uses_cloud: bool
    page_count: int | None
    pages_limited: bool
    message: str | None
    proposal_count: int | None
    cost_estimate_cents: int | None = None  # 0.4.25
    credit_wait: "CreditWaitOut | None" = None  # 0.4.25
    created_at: datetime
    decided_at: datetime | None


class AnalysisDecisionIn(ApiModel):
    approve: bool


class CharacterIn(ApiModel):
    """Serverkopie eines Charakters aus der Sammlung der App (0.4.7)."""
    id: str = Field(pattern=UUID_MUSTER)
    version: int = Field(ge=1)
    name: str = Field(min_length=1, max_length=128)
    nickname: str | None = Field(default=None, max_length=64)
    summary: str | None = Field(default=None, max_length=2000)
    backstory: str | None = Field(default=None, max_length=20000)
    system: str | None = Field(default=None, max_length=64)
    pronouns: str | None = Field(default=None, max_length=32)  # 0.4.28 → Member.characterPronouns
    status: CharacterStatus
    status_changed_at: datetime | None = None


class WorldEntryIn(ApiModel):
    id: str = Field(pattern=UUID_MUSTER)
    version: int = Field(ge=1)
    type: Literal["npc", "location", "faction", "item", "quest", "other"]
    name: str = Field(min_length=1, max_length=200)
    summary: str = Field(min_length=1, max_length=4000)
    secret: bool
    relation: str | None = Field(default=None, max_length=120)  # 0.4.19: „Schwester von Litha“, nur Anzeige


class WorldIn(ApiModel):
    entries: list[WorldEntryIn]  # 1–100, geprüft im Endpunkt (400 world_too_many)


class WorldEntryStatusOut(ApiModel):
    id: str
    proposal_id: str | None
    entry_id: str | None
    state: Literal["pending", "accepted", "rejected", "unchanged"]
    server_version: int | None


class WorldOut(ApiModel):
    entries: list[WorldEntryStatusOut]


class CampaignCreate(ApiModel):
    title: str = Field(min_length=1, max_length=200)
    description: str = Field(default="", max_length=10000)
    language: ContentLanguage = "de"
    organization_id: str | None = None
    system: GameSystem | None = None
    system_name: str | None = Field(default=None, max_length=100)
    settings: "CampaignSettingsIn | None" = None  # 0.4.25


class CampaignPatch(ApiModel):
    title: str | None = Field(default=None, max_length=200)
    description: str | None = Field(default=None, max_length=10000)
    world_info: str | None = Field(default=None, max_length=50000)
    language: ContentLanguage | None = None
    system: GameSystem | None = None
    system_name: str | None = Field(default=None, max_length=100)
    cover_preset: CoverPreset | None = None
    allow_external_transcription: bool | None = None
    allow_cloud_summary: bool | None = None
    allow_character_analysis: bool | None = None  # 0.4.18
    archived: bool | None = None
    hotwords: list[Annotated[str, Field(max_length=40)]] | None = Field(default=None, max_length=200)
    links: list[Link] | None = Field(default=None, max_length=20)  # 0.4.13: ganze Liste ersetzen


class JoinRequest(ApiModel):
    code: str = Field(min_length=1, max_length=32)
    character_name: str | None = Field(default=None, max_length=200)
    character: CharacterIn | None = None  # 0.4.7


class InviteOut(ApiModel):
    code: str
    expires_at: datetime


class SeatInviteOut(InviteOut):
    member_id: str  # 0.4.8: Einladung für genau diesen offenen Platz


# ---------- Umzug (0.4.8) ----------
class MoveConsentIn(ApiModel):
    granted: bool


class CampaignExportOut(ApiModel):
    id: str
    state: Literal["queued", "processing", "ready", "failed"]
    progress: float | None
    size_bytes: int | None
    expires_at: datetime | None
    download_url: str | None
    consented_member_ids: list[str]
    message: str | None
    created_at: datetime


class ImportStartIn(ApiModel):
    file_name: str = Field(min_length=1, max_length=300)
    size_bytes: int = Field(ge=1)


class ImportStartOut(ApiModel):
    import_id: str
    chunk_size_bytes: int
    chunk_count: int


class ImportStatusOut(ApiModel):
    id: str
    state: Literal["uploading", "processing", "done", "failed"]
    progress: float | None
    missing_chunks: list[int] | None = None  # nur bei uploading, sonst weggelassen
    campaign_id: str | None
    open_seats: int | None
    message: str | None


class MemberPatch(ApiModel):
    character_name: str | None = Field(default=None, max_length=200)
    character_summary: str | None = Field(default=None, max_length=1000)
    character_backstory: str | None = Field(default=None, max_length=8000)
    role: Role | None = None


class RecordingConsentIn(ApiModel):
    granted: bool


class SeenIn(ApiModel):
    area: Literal["chronicle", "bible"]


class UsageOut(ApiModel):
    month: str
    sessions: int
    audio_seconds: int
    documents: int
    cost_estimate_cents: int | None
    billed_to: Literal["gm", "organization"]


# ---------- Sessions ----------
class AttendeeIn(ApiModel):
    member_id: str | None = None
    guest_name: str | None = Field(default=None, max_length=200)
    consent: bool
    consent_source: Literal["app", "on_site"]
    consent_at: datetime | None = None

    _utc = field_validator("consent_at")(_as_utc)


class AttendeeOut(ApiModel):
    member_id: str | None = None  # genau eines von beiden wird ausgegeben
    guest_name: str | None = None
    consent: bool
    consent_source: Literal["app", "on_site"]
    consent_at: datetime | None


class SessionCreate(ApiModel):
    title: str | None = Field(default=None, max_length=300)
    played_at: datetime
    attendees: list[AttendeeIn]

    _utc = field_validator("played_at")(_as_utc)


class SessionPatch(ApiModel):
    title: str | None = Field(default=None, max_length=300)
    attendees: list[AttendeeIn] | None = None


class SessionSummaryOut(ApiModel):
    id: str
    number: int
    title: str | None
    played_at: datetime
    state: ProcessingState
    unread_comments: int


class SessionOut(SessionSummaryOut):
    campaign_id: str
    source: Literal["table", "discord"] | None
    transcription_engine: Literal["local", "external"] | None
    attendees: list[AttendeeOut]
    duration_seconds: int | None
    audio_deleted_at: datetime | None
    published_at: datetime | None


class ProcessingStatusOut(ApiModel):
    state: ProcessingState
    progress: float | None
    queue_position: int | None
    message: str | None
    updated_at: datetime
    estimated_seconds: int | None = None
    credit_wait: "CreditWaitOut | None" = None  # 0.4.25


class GmNoteOut(ApiModel):
    text: str
    updated_at: datetime


class GmNoteIn(ApiModel):
    text: str = Field(max_length=100000)


class SpeakerOut(ApiModel):
    id: str
    label: str
    speaking_seconds: float
    sample_text: str
    suggested_member_id: str | None
    suggested_guest_name: str | None = None  # 0.4.24: schließt suggested_member_id aus
    confidence: float
    source: Literal["intro_round", "voice_match", "discord_track", "none"]
    assigned_guest_name: str | None = None  # 0.4.7
    assigned_member_id: str | None = None  # 0.4.10: erst nach der Bestätigung, vorher weggelassen
    samples: list["SpeakerSample"] = []  # 0.4.21: bis zu drei Textproben; leer bei älteren Workern
    hint: "SpeakerHint | None" = None  # 0.4.21: Anrede oder Selbstnennung widerspricht der Zuordnung


class SpeakerSample(ApiModel):
    start: float
    end: float | None = None
    text: str


class SpeakerHint(ApiModel):
    code: Literal["addressed_as", "self_named"]
    name: str
    member_id: str
    count: int


class SpeakerAssignIn(ApiModel):
    speaker_id: str
    member_id: str | None = None  # null = Gast (mit guestName) / ignorieren
    guest_name: str | None = Field(default=None, max_length=200)  # 0.4.7


class RecapOut(ApiModel):
    session_id: str
    number: int
    title: str
    text: str
    open_threads: list[str]
    published_at: datetime | None
    review: dict | None = None  # 0.4.6: Prüfteil, nur für die SL (für Spieler weggelassen)
    revision: dict | None = None  # 0.4.15: Korrektur-Entwurf, nur für die SL (für Spieler weggelassen)
    parts: list[dict] = []  # 0.4.17: Teile im Kapitel; leer = ohne Teile
    gm_warnings: list["GmWarning"] | None = None  # 0.4.21: nur für die SL (für Spieler weggelassen, auch keine Anzahl)


class GmWarning(ApiModel):
    code: Literal["secret_entry_name", "secret_wording"]
    entry_ids: list[str]
    names: list[str] = []
    paragraphs: list[int] = []


class RevisionIn(ApiModel):
    note: str = Field(max_length=100000)  # 0.4.15; Länge prüft der Server selbst (400 invalid_input mit Text)
    base_text: str = Field(max_length=200000)


class DecisionIn(ApiModel):
    accept: bool


class RecapPartIn(ApiModel):
    title: str = Field(max_length=2000)  # Länge prüft app/kapitelteile.py (400 invalid_parts)
    first_paragraph: int


class RecapIn(ApiModel):
    title: str | None = Field(default=None, max_length=300)
    text: str | None = Field(default=None, max_length=100000)
    open_threads: list[str] | None = Field(default=None, max_length=50)
    parts: list[RecapPartIn] | None = Field(default=None, max_length=50)  # 0.4.17


class EvidenceOut(ApiModel):
    start: float | None = None
    page: int | None = None
    quote: str


class ProposalOut(ApiModel):
    id: str
    session_id: str | None
    document_id: str | None
    source: Literal["session", "document", "character"]  # 0.4.7
    origin_character_id: str | None = None
    origin_entry_id: str | None = None
    submitted_by_member_id: str | None = None
    analysis_id: str | None = None  # 0.4.18
    relation: str | None = None  # 0.4.19
    entry_type: EntryType
    action: Literal["create", "update", "reveal"]
    target_entry_id: str | None
    title: str
    detail: str
    gm_notes: str | None
    suggested_visibility: Visibility
    hidden_from_member_ids: list[str]
    public_suggested: bool
    visibility_reason: str | None
    confidence: float
    flags: list[Literal["joke_suspected", "low_confidence", "contradicts_bible", "evidence_not_found"]]
    evidence: list[EvidenceOut]
    decision: Literal["open", "accepted", "rejected"]
    match: "ProposalMatch | None" = None  # 0.4.21: nur bei action create, nur für die SL (Vorschläge sieht nur sie)
    merge_into_entry_id: str | None = None  # 0.4.21
    holder_member_id: str | None = None  # 0.4.26: nur bei Beute aus Runden
    holder_cleared: bool = False  # 0.4.26


class ProposalMatch(ApiModel):
    entry_id: str
    name: str
    matched_name: str
    entry_type: EntryType
    kind: Literal["sound", "spelling", "part_name"]


class ProposalPatch(ApiModel):
    decision: Literal["open", "accepted", "rejected"] | None = None
    title: str | None = Field(default=None, max_length=300)
    detail: str | None = Field(default=None, max_length=20000)
    gm_notes: str | None = Field(default=None, max_length=20000)
    visibility: Visibility | None = None
    hidden_from_member_ids: list[str] | None = None
    merge_into_entry_id: str | None = Field(default=None, max_length=64)  # 0.4.21
    holder_member_id: str | None = Field(default=None, max_length=64)  # 0.4.26
    holder_cleared: bool | None = None  # 0.4.26


class TranscriptLineOut(ApiModel):
    start: float
    end: float
    speaker_id: str
    member_id: str | None
    text: str


# ---------- Bibel ----------
class EntryInput(ApiModel):
    type: EntryType | None = None
    name: str | None = Field(default=None, max_length=300)
    summary: str | None = Field(default=None, max_length=20000)
    status: Literal["active", "done"] | None = None
    holder_member_id: str | None = None
    visibility: Visibility | None = None
    gm_notes: str | None = Field(default=None, max_length=20000)
    hidden_from_member_ids: list[str] | None = None
    links: list[Link] | None = Field(default=None, max_length=3)  # 0.4.13
    # 0.4.21: „Auch genannt“, sichtbar wie der Name; je Eintrag 1–40 Zeichen (sonst 400)
    aliases: list[Annotated[str, Field(min_length=1, max_length=40)]] | None = Field(default=None, max_length=10)
    pronouns: str | None = Field(default=None, max_length=32)  # 0.4.28, sichtbar wie der Name


class MentionOut(ApiModel):
    session_number: int
    note: str


class EntryOut(ApiModel):
    id: str
    campaign_id: str
    type: EntryType
    name: str
    summary: str
    status: Literal["active", "done"] | None
    holder_member_id: str | None
    visibility: Visibility
    aliases: list[str] = []  # 0.4.21
    pronouns: str | None = None  # 0.4.28
    gm_notes: str | None = None  # wird für Spieler weggelassen
    hidden_from_member_ids: list[str] | None = None  # wird für Spieler weggelassen
    first_session_number: int | None
    last_session_number: int | None
    mentions: list[MentionOut]
    updated_at: datetime
    origin_character_id: str | None = None  # 0.4.7: nur für SL und Urheberin, sonst weggelassen
    origin_entry_id: str | None = None
    origin_version: int | None = None
    former_holder_member_id: str | None = None  # 0.4.9: nur für die SL, sonst weggelassen
    links: list[Link] = []  # 0.4.13: Spieler nur shared


class EntryAssignIn(ApiModel):
    """0.4.9: Figur einem anderen Spieler geben."""
    member_id: str = Field(max_length=36)


# ---------- Kommentare ----------
class CommentOut(ApiModel):
    id: str
    session_id: str
    author_member_id: str
    recipient_member_id: str | None
    recipient_member_ids: list[str] = []  # 0.4.26: alle Empfänger, leer = öffentlich
    text: str
    created_at: datetime
    edited_at: datetime | None


class CommentCreate(ApiModel):
    text: str = ""  # Länge prüft der Endpunkt selbst (413 comment_too_long statt 400)
    recipient_member_id: str | None = None
    recipient_member_ids: list[str | None] | None = Field(default=None, max_length=100)  # 0.4.26; Prüfung im Endpunkt


class CommentPatch(ApiModel):
    text: str = ""


# ---------- Terminabstimmung ----------
VoteAnswer = Literal["yes", "maybe", "no"]


class DateVoteOut(ApiModel):
    member_id: str
    answer: VoteAnswer


class DateOptionOut(ApiModel):
    id: str
    starts_at: datetime
    proposed_by_member_id: str
    votes: list[DateVoteOut]


class DatePollOut(ApiModel):
    id: str
    campaign_id: str
    status: Literal["open", "closed", "cancelled"]
    note: str | None
    options: list[DateOptionOut]
    chosen_option_id: str | None
    created_at: datetime


class DatePollCreate(ApiModel):
    note: str | None = Field(default=None, max_length=1000)


class DateOptionCreate(ApiModel):
    starts_at: datetime

    @field_validator("starts_at")
    @classmethod
    def _utc(cls, v: datetime) -> datetime:
        return _as_utc(v)


class DateVoteIn(ApiModel):
    answer: VoteAnswer


class DatePollClose(ApiModel):
    option_id: str


# ---------- SL-Unterlagen ----------
DocumentKind = Literal["handout", "gm", "mixed", "character_sheet"]


class CampaignDocumentOut(ApiModel):
    id: str
    campaign_id: str
    title: str
    file_name: str
    kind: DocumentKind
    size_bytes: int
    page_count: int | None
    state: Literal["queued", "processing", "awaiting_review", "done", "failed"]
    progress: float | None
    message: str | None
    proposal_count: int
    open_proposal_count: int
    world_info_suggestion: str | None
    uploaded_by_member_id: str | None = None  # 0.4.7
    analysis_id: str | None = None  # 0.4.18, nur bei character_sheet
    credit_wait: "CreditWaitOut | None" = None  # 0.4.25
    created_at: datetime


class DocumentApplyIn(ApiModel):
    apply_world_info: bool = False


# ---------------------------------------------------------------- Kapitelplan (0.4.12)
class DocumentPageOut(ApiModel):
    page: int
    text: str


class DocumentTextOut(ApiModel):
    pages: list[DocumentPageOut]


class PlanScene(ApiModel):
    id: str = Field(pattern=UUID_MUSTER)
    title: str = Field(min_length=1, max_length=120)
    notes: str | None = Field(default=None, max_length=4000)
    entry_ids: list[Annotated[str, Field(max_length=36)]] = Field(default=[], max_length=30)
    state: Literal["open", "played", "skipped"] = "open"
    links: list[Link] = Field(default=[], max_length=3)  # 0.4.13


class ChapterPlanIn(ApiModel):
    """POST: title ist Pflicht. PATCH (ChapterPlanPatchIn): nur mitgeschickte Felder ändern sich."""
    title: str = Field(min_length=1, max_length=120)
    session_number: int | None = Field(default=None, ge=1)
    state: Literal["draft", "ready", "played"] = "draft"
    notes: str | None = Field(default=None, max_length=20000)
    table_notes: str | None = Field(default=None, max_length=20000)  # 0.4.13
    scenes: list[PlanScene] = Field(default=[], max_length=50)
    names: list[Annotated[str, Field(max_length=40)]] = Field(default=[], max_length=100)
    document_ids: list[Annotated[str, Field(max_length=36)]] = Field(default=[], max_length=20)


class ChapterPlanPatchIn(ApiModel):
    title: str | None = Field(default=None, min_length=1, max_length=120)
    session_number: int | None = Field(default=None, ge=1)
    state: Literal["draft", "ready", "played"] | None = None
    notes: str | None = Field(default=None, max_length=20000)
    table_notes: str | None = Field(default=None, max_length=20000)  # 0.4.13
    scenes: list[PlanScene] | None = Field(default=None, max_length=50)
    names: list[Annotated[str, Field(max_length=40)]] | None = Field(default=None, max_length=100)
    document_ids: list[Annotated[str, Field(max_length=36)]] | None = Field(default=None, max_length=20)
    if_updated_at: datetime | None = None


class ChapterPlanOut(ApiModel):
    id: str
    campaign_id: str
    title: str
    session_number: int | None
    state: Literal["draft", "ready", "played"]
    notes: str | None
    table_notes: str | None = None  # 0.4.13
    scenes: list[PlanScene]
    names: list[str]
    document_ids: list[str]
    created_at: datetime
    updated_at: datetime


# ---------- Qualitätsprüfung (0.4.6) ----------
class CorrectionIn(ApiModel):
    heard: str = Field(min_length=1, max_length=100)
    correct: str = Field(max_length=100)
    add_to_hotwords: bool = True


class CorrectionsIn(ApiModel):
    corrections: list[CorrectionIn] = Field(min_length=1, max_length=100)
    retranscribe: bool = False



# ---------- 0.4.25: Guthaben in der App, Kampagnen-Einstellungen ----------
WarteGrund = Literal["approval", "empty", "too_low", "no_payer", "pot"]
AuftragsArt = Literal["transcribe", "summarize", "revise", "document"]


class CreditWaitOut(ApiModel):
    reason: WarteGrund
    estimate_cents: int | None = None
    credit_id: str | None = None
    job_id: str | None = None


class CreditOut(ApiModel):
    id: str
    kind: Literal["account", "organization"]
    organization_id: str | None
    organization_name: str | None
    balance_cents: int
    status: Literal["good", "low", "empty", "negative"]
    approval_threshold_cents: int
    sheet_max_pages: int
    sheets_per_player: int
    waiting_count: int
    can_manage: bool


class CreditUsageOut(ApiModel):
    job_kind: AuftragsArt
    campaign_id: str | None
    campaign_title: str | None
    session_number: int | None
    document_title: str | None


class CreditBookingOut(ApiModel):
    id: str
    kind: Literal["payment", "credit", "voucher", "usage", "chargeback", "refund", "correction"]
    amount_cents: int
    created_at: datetime
    reason: str | None
    usage: CreditUsageOut | None


class CreditWaitingJobOut(ApiModel):
    job_id: str
    job_kind: AuftragsArt
    campaign_id: str
    campaign_title: str
    session_id: str | None
    session_number: int | None
    document_id: str | None
    document_title: str | None
    analysis_id: str | None
    estimate_cents: int
    reason: WarteGrund
    created_at: datetime


class CreditSettingsIn(ApiModel):
    approval_threshold_cents: int | None = None
    sheet_max_pages: int | None = None
    sheets_per_player: int | None = None


class VoucherIn(ApiModel):
    code: str = Field(min_length=1, max_length=40)


class VoucherOut(ApiModel):
    amount_cents: int
    credit: CreditOut


class CampaignBillingOut(ApiModel):
    paid_by: Literal["gm", "organization", "nobody"]
    payer_member_id: str | None
    is_me: bool
    offered_to_member_id: str | None


class PayerOfferIn(ApiModel):
    member_id: str | None = Field(max_length=64)


# ---------- Spielende tragen bei (0.4.28) ----------
class CounterKind(ApiModel):
    id: str = Field(pattern=r"^[a-z0-9_-]{1,24}$")
    label: str = Field(min_length=1, max_length=24)
    hint: str | None = Field(default=None, max_length=60)


class CounterValue(ApiModel):
    kind_id: str = Field(max_length=24)
    value: int = Field(ge=-9999, le=99999)
    max: int | None = Field(default=None, ge=0, le=99999)


class CountersIn(ApiModel):
    values: list[CounterValue] = Field(max_length=12)


PlayerEntryType = Literal["npc", "location", "quest", "item", "faction", "other"]


class PlayerProposalIn(ApiModel):
    entry_type: PlayerEntryType
    title: str = Field(min_length=1, max_length=120)
    detail: str = Field(default="", max_length=2000)
    target_entry_id: str | None = Field(default=None, max_length=64)


class PlayerProposalOut(ApiModel):
    id: str
    campaign_id: str
    author_member_id: str
    entry_type: PlayerEntryType
    title: str
    detail: str
    target_entry_id: str | None
    state: Literal["open", "accepted", "rejected", "withdrawn", "closed"]
    created_at: datetime
    decided_at: datetime | None
    result_entry_id: str | None


class PlayerProposalDecision(ApiModel):
    decision: Literal["accept", "reject"]
    entry_type: PlayerEntryType | None = None
    title: str | None = Field(default=None, min_length=1, max_length=120)
    detail: str | None = Field(default=None, max_length=2000)
    gm_notes: str | None = Field(default=None, max_length=20000)
    visibility: Visibility | None = None
    hidden_from_member_ids: list[str] | None = Field(default=None, max_length=500)
    merge_into_entry_id: str | None = Field(default=None, max_length=64)
    holder_member_id: str | None = Field(default=None, max_length=64)


class BookmarkIn(ApiModel):
    at: datetime | None = None
    note: str | None = Field(default=None, max_length=280)


class BookmarkOut(ApiModel):
    id: str
    session_id: str
    at: datetime
    note: str | None
    offset_ms: int | None
    recap_paragraph: int | None
    created_at: datetime


class CampaignSettingsIn(ApiModel):
    allow_cloud_summary: bool | None = None
    allow_external_transcription: bool | None = None
    allow_character_analysis: bool | None = None
    player_proposals: bool | None = None  # 0.4.28
    counter_kinds: list[CounterKind] | None = Field(default=None, max_length=8)  # 0.4.28, ganze Liste


class CampaignSettingsPatch(CampaignSettingsIn):
    version: int = Field(ge=1)


class SettingLockOut(ApiModel):
    field: Literal["allowCloudSummary", "allowExternalTranscription", "allowCharacterAnalysis"]
    value: bool
    reason: Literal["unavailable", "operator"]


class CampaignSettingsOut(ApiModel):
    version: int
    allow_cloud_summary: bool
    allow_external_transcription: bool
    allow_character_analysis: bool
    player_proposals: bool = True  # 0.4.28
    counter_kinds: list[CounterKind] = []  # 0.4.28
    character_sheet_limits: SheetLimitsOut | None
    locks: list[SettingLockOut]
    updated_at: datetime
    updated_by_member_id: str | None


class CampaignSettingsLogOut(ApiModel):
    at: datetime
    field: str
    old_value: bool | int | None
    new_value: bool | int | None
    version: int
    by_member_id: str | None
    by_operator: bool


for _m in (MemberOut, CampaignOut, CharacterAnalysisOut, CampaignCreate, ProcessingStatusOut, CampaignDocumentOut):
    _m.model_rebuild()
