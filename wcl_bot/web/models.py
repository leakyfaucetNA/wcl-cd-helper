"""Pydantic request/response models for the web API."""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field


class HealerSlotPayload(BaseModel):
    wow_class: str = Field(..., description="e.g. 'Druid', 'Priest'")
    spec: str = Field(..., description="e.g. 'Restoration', 'Holy'")
    count: int = Field(1, ge=1, le=8, description="How many of this slot")


class EncounterRef(BaseModel):
    id: int
    name: str


class ZoneRef(BaseModel):
    id: int
    name: str
    expansion: str | None
    frozen: bool
    encounters: list[EncounterRef]


class ZonesResponse(BaseModel):
    zones: list[ZoneRef]


class DiscoverRequest(BaseModel):
    encounter_id: int
    difficulty: int = Field(..., ge=1, le=5, description="3=N, 4=H, 5=M")
    healers: list[HealerSlotPayload] = Field(..., min_length=1)
    metric: Literal["execution", "speed"] = "execution"
    region: str | None = None
    skip_top: int = 0
    pages: int = Field(20, ge=1, le=100)
    include_extra_healers: bool = False
    bypass_cache: bool = False       # one-shot fresh fetch, ignores query cache


class MatchSummary(BaseModel):
    index: int                       # 1-based for UI display
    report_code: str
    fight_id: int
    url: str
    guild: str | None
    region: str | None
    duration_ms: int
    healer_count: int                # for UI grouping (4 / 5 / 6 healers)
    guild_rank: int | None           # position in WCL's leaderboard pool
    total_hps: float | None          # sum of healers' HPS (amount)
    avg_rank_percent: float | None   # mean Parse % across healers
    avg_active_pct: float | None     # mean active-time % across healers
    min_active_pct: float | None     # lowest healer's active-time % (dead-healer flag)


class DiscoverResponse(BaseModel):
    server_filter: str               # what we sent to WCL — surfaced for transparency
    matches: list[MatchSummary]


class NoteRequest(BaseModel):
    report_code: str
    fight_id: int
    style: Literal["nsrt", "mrt"] = "nsrt"
    # Optional WCL-name → display-name overrides applied at format time.
    # Used by the UI to remap discovered players to the user's own guild names.
    name_overrides: dict[str, str] = Field(default_factory=dict)
    # Optional WCL-name → target-class overrides (e.g. {"Yolene": "Paladin"}).
    # For each event by that healer, the formatter substitutes the spell
    # with an equivalent from the target class (matched by `purpose`). Used
    # to "rewrite a Resto Shaman's cooldowns as a Holy Paladin's" so the
    # generated note slots into your real comp. Lines with no equivalent
    # in the target class are dropped.
    class_overrides: dict[str, str] = Field(default_factory=dict)
    # Spell IDs to omit from the generated note + returned timeline. Frontend
    # populates this from the persistent settings (excluded spells).
    excluded_spell_ids: list[int] = Field(default_factory=list)


class LogDetailRequest(BaseModel):
    report_code: str
    fight_id: int


class HealerDetail(BaseModel):
    name: str
    wow_class: str
    spec: str
    parse_percent: float | None       # rankPercent from report.rankings (this fight)
    hps: float | None                 # amount from report.rankings
    active_time_pct: float | None     # activeTime / fight duration * 100


class LogDetailResponse(BaseModel):
    report_code: str
    fight_id: int
    healers: list[HealerDetail]


class TimelineEntry(BaseModel):
    time_ms: int
    healer_name: str
    healer_class: str
    healer_spec: str
    spell_label: str
    spell_id: int                    # actual cast id


class NotePlayer(BaseModel):
    """One player whose cooldowns appear in the note's timeline."""
    name: str
    wow_class: str


class NoteResponse(BaseModel):
    report_code: str
    fight_id: int
    url: str
    duration_ms: int
    healers: list[str]               # display strings like "Mâvêr (Druid/Restoration)"
    note_text: str                   # the {time:...} formatted note
    timeline: list[TimelineEntry]    # structured equivalent for UI rendering
    # Non-healer players whose tracked raid CDs (Rallying Cry, AMZ, etc.)
    # appear in this fight's timeline. UI uses this to extend the remap
    # panel so DPS/tanks can also be renamed / class-overridden.
    raid_cd_players: list[NotePlayer] = Field(default_factory=list)


class HealerSpecRef(BaseModel):
    wow_class: str
    spec: str


class HealerSpecsResponse(BaseModel):
    """The 6 retail healer (class, spec) pairs the bot tracks. Drives the
    web UI's checkbox grid so frontend doesn't hardcode them."""
    specs: list[HealerSpecRef]


class TrackedSpell(BaseModel):
    spell_id: int
    name: str
    label: str               # display name (e.g. "Avenging Wrath / Crusader")
    category: str            # "raid" | "external"
    wow_class: str
    spec: str                # for class-wide raid CDs this is "(any)"
    group: str               # "healer" or "raid" — drives UI grouping
    default_excluded: bool   # True for class-wide raid CDs (opt-in)


class TrackedSpellsResponse(BaseModel):
    spells: list[TrackedSpell]


class SettingsPayload(BaseModel):
    """Persistent user settings.

    Two-list filter model:
      - `excluded_spell_ids` covers spells that default to INCLUDED (healer
        CDs); membership means user has disabled it.
      - `enabled_spell_ids` covers spells that default to EXCLUDED (DPS-class
        raid CDs); membership means user has opted them in.
    """
    excluded_spell_ids: list[int] = Field(default_factory=list)
    enabled_spell_ids: list[int] = Field(default_factory=list)


class GuildRosterMember(BaseModel):
    """A healer found in a guild's recent reports. `specs` may contain
    multiple entries if the player has been seen on more than one healing
    spec across the queried reports — frontend lets the user narrow down."""
    name: str
    wow_class: str
    specs: list[str]


class GuildLookupResponse(BaseModel):
    guild_name: str
    server_slug: str
    server_region: str
    reports_scanned: int
    members: list[GuildRosterMember]


class LogImportRequest(BaseModel):
    log: str = Field(..., description="WCL report URL or report code")


class LogImportResponse(BaseModel):
    report_code: str
    fight_id: int | None = None        # set if input URL had #fight=N
    healers: list[GuildRosterMember]


class RosterMember(BaseModel):
    name: str
    wow_class: str
    specs: list[str] = Field(default_factory=list)


class RosterPayload(BaseModel):
    """Used for both GET response and PUT request — the whole roster moves
    as a single document. Single-user app, no merging concerns."""
    members: list[RosterMember]
