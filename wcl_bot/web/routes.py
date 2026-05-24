"""API endpoints for the web UI."""
from __future__ import annotations

import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from wcl_bot.cooldowns import (
    HEALER_COOLDOWNS,
    extract_cooldowns,
    fetch_fight_cooldowns,
    score_logs,
)
from wcl_bot.matcher import (
    CompFilterError,
    HealerSlot,
    TargetComp,
    build_comp_filters,
    find_matching_kills,
)
from wcl_bot.notes import NoteStyle, format_note
from wcl_bot.wcl import WCLClient, queries
from wcl_bot.wcl.client import (
    CACHE_TTL_GAME_DATA,
    CACHE_TTL_LEADERBOARD,
    CACHE_TTL_STATIC,
    WCLError,
)
from wcl_bot.web.models import (
    DiscoverRequest,
    DiscoverResponse,
    EncounterRef,
    GuildLookupResponse,
    GuildRosterMember,
    HealerDetail,
    HealerSpecRef,
    HealerSpecsResponse,
    LogDetailRequest,
    LogDetailResponse,
    LogImportRequest,
    LogImportResponse,
    MatchSummary,
    NoteRequest,
    NoteResponse,
    RosterMember,
    RosterPayload,
    TimelineEntry,
    ZoneRef,
    ZonesResponse,
)

# Persistent roster lives in the user's XDG config dir. Single JSON document;
# single-user app (LAN-only, no auth). Multi-browser/multi-PC access is the
# whole point of moving this off localStorage.
ROSTER_STORE_PATH = Path.home() / ".config" / "wcl_bot" / "roster.json"

log = logging.getLogger(__name__)
router = APIRouter()


def _client(request: Request) -> WCLClient:
    return request.app.state.wcl


def _load_roster_from_disk() -> list[RosterMember]:
    try:
        raw = json.loads(ROSTER_STORE_PATH.read_text())
        members = raw.get("members") or []
        return [RosterMember(**m) for m in members]
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError) as exc:
        if not isinstance(exc, FileNotFoundError):
            log.warning("Roster file unreadable at %s: %s", ROSTER_STORE_PATH, exc)
        return []


def _save_roster_to_disk(members: list[RosterMember]) -> None:
    try:
        ROSTER_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        ROSTER_STORE_PATH.write_text(
            json.dumps({"members": [m.model_dump() for m in members]}, indent=2)
        )
        ROSTER_STORE_PATH.chmod(0o600)
    except OSError as exc:
        log.error("Could not write roster to %s: %s", ROSTER_STORE_PATH, exc)
        raise


@router.get("/roster", response_model=RosterPayload)
async def get_roster() -> RosterPayload:
    return RosterPayload(members=_load_roster_from_disk())


@router.put("/roster", response_model=RosterPayload)
async def put_roster(payload: RosterPayload) -> RosterPayload:
    try:
        _save_roster_to_disk(payload.members)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Disk write failed: {exc}") from exc
    return payload


@router.get("/healer_specs", response_model=HealerSpecsResponse)
async def healer_specs() -> HealerSpecsResponse:
    """The 6 healer (class, spec) combos the bot tracks. Drives the
    frontend's checkbox grid."""
    return HealerSpecsResponse(
        specs=[
            HealerSpecRef(wow_class=cls, spec=spec)
            for (cls, spec) in HEALER_COOLDOWNS.keys()
        ]
    )


# Report codes in WCL URLs are typically 16 chars, but we accept any
# reasonable alphanumeric run rather than hardcode a length — codes have
# changed format historically and may again.
_REPORT_CODE_FROM_URL = re.compile(r"/reports/([A-Za-z0-9]+)")
_BARE_REPORT_CODE = re.compile(r"^([A-Za-z0-9]{8,32})$")


def _extract_report_code(s: str) -> str | None:
    s = s.strip()
    m = _REPORT_CODE_FROM_URL.search(s)
    if m:
        return m.group(1)
    m = _BARE_REPORT_CODE.match(s)
    if m:
        return m.group(1)
    return None


def _normalize_realm_slug(name: str) -> str:
    """'Bleeding Hollow' / 'bleeding hollow' / 'Mal'Ganis' -> WCL slug form.

    Lowercase, collapse runs of non-alphanumeric chars to single hyphens,
    strip leading/trailing hyphens. Matches WCL's URL convention.
    """
    s = name.lower().strip()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s


@router.get("/guild", response_model=GuildLookupResponse)
async def guild_lookup(
    request: Request,
    name: str,
    server: str,
    region: str,
    limit: int = 3,
) -> GuildLookupResponse:
    """Look up a guild's recent healers by scanning its most recent N
    reports' playerDetails. Returns aggregated (name, class, specs) where
    specs collects every healing spec the player has been seen on across
    the scanned reports."""
    from wcl_bot.matcher.parsing import parse_healers

    client = _client(request)
    server_slug = _normalize_realm_slug(server)

    try:
        guild_resp = await client.execute(
            queries.GET_GUILD_BY_NAME,
            {
                "name": name,
                "serverSlug": server_slug,
                "serverRegion": region.upper(),
            },
            cache_ttl_seconds=CACHE_TTL_LEADERBOARD,
        )
    except WCLError as exc:
        raise HTTPException(status_code=502, detail=f"WCL: {exc}") from exc

    guild = (guild_resp.get("guildData") or {}).get("guild")
    if not guild:
        raise HTTPException(
            status_code=404,
            detail=f"Guild '{name}' not found on {server_slug}-{region.upper()}",
        )

    try:
        reports_resp = await client.execute(
            queries.GET_REPORTS_FOR_GUILD,
            {"guildID": int(guild["id"]), "limit": limit},
            cache_ttl_seconds=CACHE_TTL_LEADERBOARD,
        )
    except WCLError as exc:
        raise HTTPException(status_code=502, detail=f"WCL: {exc}") from exc

    reports = (
        ((reports_resp.get("reportData") or {}).get("reports") or {}).get("data")
        or []
    )

    # (name, class) -> set of specs seen
    aggregate: dict[tuple[str, str], set[str]] = {}
    scanned = 0
    for r in reports:
        try:
            pd = await client.execute(
                queries.GET_REPORT_PLAYER_DETAILS_ALL_KILLS,
                {"code": r["code"]},
                cache_ttl_seconds=CACHE_TTL_STATIC,
            )
            healers = parse_healers(pd)
        except WCLError as exc:
            log.warning("playerDetails failed for guild report %s: %s", r.get("code"), exc)
            continue
        scanned += 1
        for h in healers:
            aggregate.setdefault((h.name, h.wow_class), set()).add(h.spec)

    members = sorted(
        [
            GuildRosterMember(name=n, wow_class=c, specs=sorted(s))
            for (n, c), s in aggregate.items()
        ],
        key=lambda m: (m.wow_class, m.name),
    )
    return GuildLookupResponse(
        guild_name=guild.get("name", name),
        server_slug=server_slug,
        server_region=region.upper(),
        reports_scanned=scanned,
        members=members,
    )


@router.post("/log_healers", response_model=LogImportResponse)
async def log_healers(
    request: Request, payload: LogImportRequest
) -> LogImportResponse:
    """Pull every healer that appeared on a kill in a single WCL report.

    Accepts either a full WCL URL (https://www.warcraftlogs.com/reports/...)
    or the bare report code. Same shape as /api/guild's `members` so the
    frontend can reuse roster-merging logic."""
    from wcl_bot.matcher.parsing import parse_healers

    code = _extract_report_code(payload.log)
    if not code:
        raise HTTPException(
            status_code=400,
            detail=f"Couldn't extract a WCL report code from {payload.log!r}",
        )

    client = _client(request)
    try:
        pd = await client.execute(
            queries.GET_REPORT_PLAYER_DETAILS_ALL_KILLS,
            {"code": code},
            cache_ttl_seconds=CACHE_TTL_STATIC,
        )
        healers = parse_healers(pd)
    except WCLError as exc:
        raise HTTPException(status_code=502, detail=f"WCL: {exc}") from exc

    aggregate: dict[tuple[str, str], set[str]] = {}
    for h in healers:
        aggregate.setdefault((h.name, h.wow_class), set()).add(h.spec)

    members = sorted(
        [
            GuildRosterMember(name=n, wow_class=c, specs=sorted(s))
            for (n, c), s in aggregate.items()
        ],
        key=lambda m: (m.wow_class, m.name),
    )
    return LogImportResponse(report_code=code, healers=members)


@router.get("/zones", response_model=ZonesResponse)
async def zones(request: Request) -> ZonesResponse:
    """All WCL zones with their encounters. Cached 30d server-side."""
    client = _client(request)
    data = await client.execute(
        queries.GET_ZONES, cache_ttl_seconds=CACHE_TTL_GAME_DATA
    )
    out: list[ZoneRef] = []
    for z in data["worldData"]["zones"]:
        exp = z.get("expansion") or {}
        out.append(
            ZoneRef(
                id=z["id"],
                name=z["name"],
                expansion=exp.get("name"),
                frozen=bool(z.get("frozen")),
                encounters=[
                    EncounterRef(id=e["id"], name=e["name"])
                    for e in (z.get("encounters") or [])
                ],
            )
        )
    return ZonesResponse(zones=out)


@router.post("/discover", response_model=DiscoverResponse)
async def discover(
    request: Request, payload: DiscoverRequest
) -> DiscoverResponse:
    """Find matching kills, extract cooldowns, return per-log scoring."""
    # Build TargetComp (expand counts → individual slots so the matcher
    # permutation logic works as expected).
    try:
        slots = tuple(
            HealerSlot.create(h.wow_class, h.spec)
            for h in payload.healers
            for _ in range(h.count)
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    target_comp = TargetComp(slots=slots)

    try:
        filters = build_comp_filters(
            target_comp,
            include_extra_healers=payload.include_extra_healers,
        )
    except CompFilterError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc

    client = _client(request)
    try:
        matches = await find_matching_kills(
            client,
            encounter_id=payload.encounter_id,
            difficulty=payload.difficulty,
            target_comp=target_comp,
            comp_filters=filters,
            max_pages=payload.pages,
            drop_fastest_pct=payload.drop_fastest_pct,
            skip_top=payload.skip_top,
            metric=payload.metric,
            server_region=payload.region,
        )
    except WCLError as exc:
        raise HTTPException(status_code=502, detail=f"WCL: {exc}") from exc

    server_filter_display = " ; ".join(
        f"[{'heal.' + str(hc) if hc is not None else 'broad'}] {f}"
        for hc, f in filters
    )

    if not matches:
        return DiscoverResponse(server_filter=server_filter_display, matches=[])

    fight_cds = await extract_cooldowns(client, matches)
    scores = score_logs(
        fight_cds,
        outlier_threshold_ms=int(payload.outlier_threshold_seconds * 1000),
    )

    summaries: list[MatchSummary] = []
    for s in scores:
        r = s.fight.kill.ranking
        summaries.append(
            MatchSummary(
                index=s.index,
                report_code=r.report_code,
                fight_id=r.fight_id,
                url=s.fight.kill.url,
                guild=r.guild_name,
                region=r.server_region,
                duration_ms=s.fight.fight_duration_ms,
                healer_count=len(s.fight.kill.healers),
                n_presses=s.n_presses,
                n_unique_spells=s.n_unique_spells,
                avg_shift_ms=s.avg_shift_ms,
                n_outliers=s.n_outliers,
            )
        )
    return DiscoverResponse(server_filter=server_filter_display, matches=summaries)


@router.post("/log_detail", response_model=LogDetailResponse)
async def log_detail(
    request: Request, payload: LogDetailRequest
) -> LogDetailResponse:
    """For an expanded row in the UI: return healers + overall parse
    percentile for the specified (report, fight). Uses report.rankings
    which returns a JSON scalar — shape is parsed tolerantly."""
    client = _client(request)

    # Fetch playerDetails for healer identity, and rankings for percentiles.
    pd_data = await client.execute(
        queries.GET_REPORT_PLAYER_DETAILS,
        {"code": payload.report_code, "fightIDs": [payload.fight_id]},
        cache_ttl_seconds=CACHE_TTL_STATIC,
    )
    from wcl_bot.matcher.parsing import parse_healers
    healers = parse_healers(pd_data)

    try:
        rank_data = await client.execute(
            queries.GET_REPORT_FIGHT_RANKINGS,
            {"code": payload.report_code, "fightID": payload.fight_id},
            cache_ttl_seconds=CACHE_TTL_STATIC,
        )
        blob = rank_data["reportData"]["report"]["rankings"]
        per_healer_metrics = _extract_healer_metrics(blob)
    except (WCLError, KeyError) as exc:
        log.warning(
            "rankings fetch failed for %s#%d: %s — returning healers without metrics",
            payload.report_code, payload.fight_id, exc,
        )
        per_healer_metrics = {}

    return LogDetailResponse(
        report_code=payload.report_code,
        fight_id=payload.fight_id,
        healers=[
            HealerDetail(
                name=h.name,
                wow_class=h.wow_class,
                spec=h.spec,
                rank_percent=_pick_parse_percent(per_healer_metrics.get(h.name, {})),
                metrics=per_healer_metrics.get(h.name, {}),
            )
            for h in healers
        ],
    )


# Field-name candidates for "Parse %" (this-fight percentile), in priority order.
# Adjust as we confirm which one WCL actually uses.
_PARSE_PCT_FIELDS = ("rankPercent", "todayPercent", "parsePercent", "percentile")


def _pick_parse_percent(metrics: dict[str, float]) -> float | None:
    for k in _PARSE_PCT_FIELDS:
        if k in metrics:
            return metrics[k]
    return None


def _extract_healer_metrics(blob) -> dict[str, dict[str, float]]:
    """Walk the rankings JSON blob and pull every numeric field per healer.

    Returns {player_name: {field_name: value, ...}}. Surfacing all numeric
    fields (not just one) lets the UI display them and lets us identify
    which one corresponds to "Parse %" on the WCL site.
    """
    if not isinstance(blob, dict):
        return {}
    entries = blob.get("data") or [blob]
    out: dict[str, dict[str, float]] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        roles = entry.get("roles") or {}
        for _role_name, role_block in roles.items():
            chars = (role_block or {}).get("characters") or []
            for c in chars:
                if not isinstance(c, dict):
                    continue
                name = c.get("name")
                if not name:
                    continue
                metrics: dict[str, float] = {}
                for k, v in c.items():
                    if isinstance(v, bool):
                        continue
                    if isinstance(v, (int, float)):
                        metrics[k] = float(v)
                out[name] = metrics
    return out


@router.post("/note", response_model=NoteResponse)
async def note(request: Request, payload: NoteRequest) -> NoteResponse:
    """Format the selected log as an MRT or NSRT note. Bypasses discovery
    — works on any (report, fight) the user knows about."""
    client = _client(request)
    try:
        fc = await fetch_fight_cooldowns(
            client, payload.report_code, payload.fight_id
        )
    except WCLError as exc:
        raise HTTPException(status_code=502, detail=f"WCL: {exc}") from exc

    style = NoteStyle(payload.style)
    note_text = format_note(fc, style, name_overrides=payload.name_overrides)
    timeline = [
        TimelineEntry(
            time_ms=ev.time_into_fight_ms,
            healer_name=ev.healer.name,
            healer_class=ev.healer.wow_class,
            healer_spec=ev.healer.spec,
            spell_label=ev.spell.label,
            spell_id=ev.cast_spell_id,
        )
        for ev in fc.events
    ]
    return NoteResponse(
        report_code=fc.kill.ranking.report_code,
        fight_id=fc.kill.ranking.fight_id,
        url=fc.kill.url,
        duration_ms=fc.fight_duration_ms,
        healers=[
            f"{h.name} ({h.wow_class}/{h.spec})" for h in fc.kill.healers
        ],
        note_text=note_text,
        timeline=timeline,
    )
