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
    drop_fastest_pct: float = Field(0.25, ge=0.0, le=1.0)
    outlier_threshold_seconds: float = Field(10.0, ge=0.0)
    include_extra_healers: bool = False


class MatchSummary(BaseModel):
    index: int                       # 1-based for UI display
    report_code: str
    fight_id: int
    url: str
    guild: str | None
    region: str | None
    duration_ms: int
    healer_count: int                # for UI grouping (4 / 5 / 6 healers)
    n_presses: int
    n_unique_spells: int
    avg_shift_ms: int
    n_outliers: int


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


class LogDetailRequest(BaseModel):
    report_code: str
    fight_id: int


class HealerDetail(BaseModel):
    name: str
    wow_class: str
    spec: str
    rank_percent: float | None        # what we believe is "Parse %" for this fight
    # All numeric fields from the rankings blob, surfaced for diagnosis.
    # Helps figure out which field is actually "Parse %" vs "Best %" etc.
    metrics: dict[str, float] = {}


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


class NoteResponse(BaseModel):
    report_code: str
    fight_id: int
    url: str
    duration_ms: int
    healers: list[str]               # display strings like "Mâvêr (Druid/Restoration)"
    note_text: str                   # the {time:...} formatted note
    timeline: list[TimelineEntry]    # structured equivalent for UI rendering


class HealerSpecRef(BaseModel):
    wow_class: str
    spec: str


class HealerSpecsResponse(BaseModel):
    """The 6 retail healer (class, spec) pairs the bot tracks. Drives the
    web UI's checkbox grid so frontend doesn't hardcode them."""
    specs: list[HealerSpecRef]


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
