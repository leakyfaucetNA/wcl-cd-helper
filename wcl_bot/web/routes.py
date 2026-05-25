"""API endpoints for the web UI."""
from __future__ import annotations

import asyncio
import json
import logging
import re
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request

from wcl_bot.cooldowns import (
    CLASS_RAID_COOLDOWNS,
    HEALER_COOLDOWNS,
    extract_cooldowns,
    fetch_fight_cooldowns,
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
    SettingsPayload,
    TrackedSpell,
    TrackedSpellsResponse,
    TimelineEntry,
    ZoneRef,
    ZonesResponse,
)

# Persistent roster lives in the user's XDG config dir. Single JSON document;
# single-user app (LAN-only, no auth). Multi-browser/multi-PC access is the
# whole point of moving this off localStorage.
ROSTER_STORE_PATH = Path.home() / ".config" / "wcl_bot" / "roster.json"
SETTINGS_STORE_PATH = Path.home() / ".config" / "wcl_bot" / "settings.json"

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


def _load_settings_from_disk() -> SettingsPayload:
    try:
        raw = json.loads(SETTINGS_STORE_PATH.read_text())
        return SettingsPayload(**raw)
    except (FileNotFoundError, json.JSONDecodeError, ValueError, OSError) as exc:
        if not isinstance(exc, FileNotFoundError):
            log.warning("Settings file unreadable at %s: %s", SETTINGS_STORE_PATH, exc)
        return SettingsPayload()


def _save_settings_to_disk(s: SettingsPayload) -> None:
    try:
        SETTINGS_STORE_PATH.parent.mkdir(parents=True, exist_ok=True)
        SETTINGS_STORE_PATH.write_text(json.dumps(s.model_dump(), indent=2))
        SETTINGS_STORE_PATH.chmod(0o600)
    except OSError as exc:
        log.error("Could not write settings to %s: %s", SETTINGS_STORE_PATH, exc)
        raise


@router.get("/settings", response_model=SettingsPayload)
async def get_settings() -> SettingsPayload:
    return _load_settings_from_disk()


@router.put("/settings", response_model=SettingsPayload)
async def put_settings(payload: SettingsPayload) -> SettingsPayload:
    try:
        _save_settings_to_disk(payload)
    except OSError as exc:
        raise HTTPException(status_code=500, detail=f"Disk write failed: {exc}") from exc
    return payload


@router.get("/tracked_spells", response_model=TrackedSpellsResponse)
async def tracked_spells() -> TrackedSpellsResponse:
    """Every cooldown the bot can track, flat list with class/spec context.
    Drives the Search panel's spell-filter checkboxes. The 'group' field
    distinguishes always-tracked healer CDs from class-wide raid CDs that
    only fire when the user's 'Include DPS raid CDs' setting is on."""
    out: list[TrackedSpell] = []
    for (cls, spec), spells in HEALER_COOLDOWNS.items():
        for s in spells:
            out.append(
                TrackedSpell(
                    spell_id=s.spell_id, name=s.name, label=s.label,
                    category=s.category, wow_class=cls, spec=spec,
                    group="healer", default_excluded=False,
                )
            )
    for cls, spells in CLASS_RAID_COOLDOWNS.items():
        for s in spells:
            out.append(
                TrackedSpell(
                    spell_id=s.spell_id, name=s.name, label=s.label,
                    category=s.category, wow_class=cls, spec="(any)",
                    group="raid", default_excluded=True,
                )
            )
    return TrackedSpellsResponse(spells=out)


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
# Fight selector can live in the fragment (#fight=5) or query (?fight=5&...)
_FIGHT_ID_FROM_URL = re.compile(r"[#?&]fight=(\d+)")


def _extract_report_code(s: str) -> str | None:
    s = s.strip()
    m = _REPORT_CODE_FROM_URL.search(s)
    if m:
        return m.group(1)
    m = _BARE_REPORT_CODE.match(s)
    if m:
        return m.group(1)
    return None


def _extract_fight_id(s: str) -> int | None:
    m = _FIGHT_ID_FROM_URL.search(s.strip())
    return int(m.group(1)) if m else None


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
    """Pull healers from a single WCL report.

    Accepts either a full WCL URL (https://www.warcraftlogs.com/reports/...)
    or the bare report code. If the URL contains `#fight=N`, only that
    fight is scanned (useful for grabbing the comp from a specific pull);
    otherwise every kill in the report is aggregated.

    Same shape as /api/guild's `members` so the frontend can reuse
    roster-merging logic."""
    from wcl_bot.matcher.parsing import parse_healers

    code = _extract_report_code(payload.log)
    if not code:
        raise HTTPException(
            status_code=400,
            detail=f"Couldn't extract a WCL report code from {payload.log!r}",
        )
    fight_id = _extract_fight_id(payload.log)

    client = _client(request)
    try:
        if fight_id is not None:
            pd = await client.execute(
                queries.GET_REPORT_PLAYER_DETAILS,
                {"code": code, "fightIDs": [fight_id]},
                cache_ttl_seconds=CACHE_TTL_STATIC,
            )
        else:
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
    return LogImportResponse(report_code=code, fight_id=fight_id, healers=members)


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

    # When the user ticks "Fresh Call" in the UI, use a one-shot client
    # in cache-refresh mode for THIS request only. Refresh mode skips the
    # cache READ (always hits the API) but still WRITES the fresh response
    # back to disk, so the next normal request benefits from the new data.
    if payload.bypass_cache:
        from wcl_bot.wcl import WCLClient
        async with WCLClient(cache_refresh=True) as one_shot:
            return await _discover_with_client(one_shot, payload, target_comp, filters)
    return await _discover_with_client(_client(request), payload, target_comp, filters)


async def _discover_with_client(client, payload, target_comp, filters) -> DiscoverResponse:
    try:
        matches = await find_matching_kills(
            client,
            encounter_id=payload.encounter_id,
            difficulty=payload.difficulty,
            target_comp=target_comp,
            comp_filters=filters,
            max_pages=payload.pages,
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

    # Pre-warm the cooldown cache (note generation reuses these results).
    # We don't need score_logs anymore — replaced by per-fight HPS / active-%
    # metrics fetched separately.
    fight_cds = await extract_cooldowns(client, matches)

    # Pull per-fight rankings + healing-table metrics in parallel. One pair of
    # cached calls per match. The data we want:
    #   - HPS (rankings.amount) → sum across healers = total_hps
    #   - rankPercent           → mean across healers = avg_rank_percent
    #   - activeTime (table)    → / fight duration   = active_time_pct
    sem = asyncio.Semaphore(10)
    async def one_metrics(fc):
        async with sem:
            return await _fetch_per_healer_metrics(
                client,
                fc.kill.ranking.report_code,
                fc.kill.ranking.fight_id,
                fc.fight_duration_ms,
            )
    metrics_by_match = await asyncio.gather(*(one_metrics(fc) for fc in fight_cds))

    summaries: list[MatchSummary] = []
    for idx, (fc, per_healer) in enumerate(zip(fight_cds, metrics_by_match), start=1):
        r = fc.kill.ranking
        # Restrict aggregation to this fight's healers (per_healer might
        # contain DPS/tanks too if WCL returns them; we only care about
        # the matched comp).
        healer_names = {h.name for h in fc.kill.healers}
        hps_vals = []
        rp_vals = []
        at_vals = []
        for name in healer_names:
            m = per_healer.get(name, {})
            if m.get("hps") is not None:
                hps_vals.append(m["hps"])
            if m.get("parse_percent") is not None:
                rp_vals.append(m["parse_percent"])
            if m.get("active_time_pct") is not None:
                at_vals.append(m["active_time_pct"])

        summaries.append(
            MatchSummary(
                index=idx,
                report_code=r.report_code,
                fight_id=r.fight_id,
                url=fc.kill.url,
                guild=r.guild_name,
                region=r.server_region,
                duration_ms=fc.fight_duration_ms,
                healer_count=len(fc.kill.healers),
                guild_rank=r.rank,
                total_hps=sum(hps_vals) if hps_vals else None,
                avg_rank_percent=(sum(rp_vals) / len(rp_vals)) if rp_vals else None,
                avg_active_pct=(sum(at_vals) / len(at_vals)) if at_vals else None,
                min_active_pct=min(at_vals) if at_vals else None,
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

    # We need the fight duration to compute active-time %, so grab fight
    # info alongside playerDetails. Both cached.
    from wcl_bot.matcher.parsing import parse_healers
    pd_data = await client.execute(
        queries.GET_REPORT_PLAYER_DETAILS,
        {"code": payload.report_code, "fightIDs": [payload.fight_id]},
        cache_ttl_seconds=CACHE_TTL_STATIC,
    )
    healers = parse_healers(pd_data)

    fight_info = await client.execute(
        queries.GET_FIGHT_TIMES,
        {"code": payload.report_code, "fightID": payload.fight_id},
        cache_ttl_seconds=CACHE_TTL_STATIC,
    )
    fights = fight_info["reportData"]["report"]["fights"]
    if fights:
        duration_ms = int(fights[0]["endTime"]) - int(fights[0]["startTime"])
    else:
        duration_ms = 0

    metrics = await _fetch_per_healer_metrics(
        client, payload.report_code, payload.fight_id, duration_ms
    )

    return LogDetailResponse(
        report_code=payload.report_code,
        fight_id=payload.fight_id,
        healers=[
            HealerDetail(
                name=h.name,
                wow_class=h.wow_class,
                spec=h.spec,
                parse_percent=metrics.get(h.name, {}).get("parse_percent"),
                hps=metrics.get(h.name, {}).get("hps"),
                active_time_pct=metrics.get(h.name, {}).get("active_time_pct"),
            )
            for h in healers
        ],
    )


async def _fetch_per_healer_metrics(
    client, report_code: str, fight_id: int, fight_duration_ms: int,
) -> dict[str, dict]:
    """For one (report, fight), pull per-healer Parse %, HPS, active-time %.
    Returns {player_name: {"parse_percent"?, "hps"?, "active_time_pct"?}}.

    Parse % is THIS fight's actual percentile (matches WCL's healing-tab
    "Parse" column). Sourced by fanning out to each healer's
    characterData.character.encounterRankings and locating the rank entry
    whose report.code matches this report — report.rankings(fightIDs)
    only exposes character-aggregate stats, not per-fight values.
    """
    out: dict[str, dict] = {}
    healer_info: dict[str, dict] = {}    # name -> {server_slug, server_region}
    encounter_id: int | None = None

    # 1. Report rankings — fast (one cached call). Extract encounter id and
    # per-healer (name, server, region) so we can fan out to char rankings.
    try:
        rank_data = await client.execute(
            queries.GET_REPORT_FIGHT_RANKINGS,
            {"code": report_code, "fightID": fight_id},
            cache_ttl_seconds=CACHE_TTL_STATIC,
        )
        for name, info in _extract_healer_server_info(
            rank_data.get("reportData", {}).get("report", {}).get("rankings")
        ).items():
            healer_info[name] = info
        encounter_id = _extract_encounter_id(
            rank_data.get("reportData", {}).get("report", {}).get("rankings")
        )
    except (WCLError, KeyError) as exc:
        log.warning("rankings fetch failed for %s#%d: %s", report_code, fight_id, exc)

    # 2. Per-healer character rankings — one call each. Gives this-fight Parse %.
    if encounter_id is not None and healer_info:
        sem = asyncio.Semaphore(10)
        async def one_parse(name, info):
            async with sem:
                pct = await _fetch_char_fight_parse(
                    client, name, info["server_slug"], info["server_region"],
                    encounter_id, report_code, fight_id,
                )
            return name, pct
        results = await asyncio.gather(
            *(one_parse(n, i) for n, i in healer_info.items())
        )
        for name, pct in results:
            if pct is not None:
                out.setdefault(name, {})["parse_percent"] = pct

    # 3. Healing table → activeTime + total healing (HPS). One cached call.
    try:
        tbl_data = await client.execute(
            queries.GET_HEALING_TABLE,
            {"code": report_code, "fightID": fight_id},
            cache_ttl_seconds=CACHE_TTL_STATIC,
        )
        duration_s = fight_duration_ms / 1000.0 if fight_duration_ms > 0 else 0.0
        for name, fields in _extract_healing_table_data(
            tbl_data.get("reportData", {}).get("report", {}).get("table")
        ).items():
            slot = out.setdefault(name, {})
            active_ms = fields.get("active_time_ms")
            if active_ms is not None and fight_duration_ms > 0:
                pct = max(0.0, min(100.0, active_ms / fight_duration_ms * 100.0))
                slot["active_time_pct"] = pct
            total_heal = fields.get("total_healing")
            if total_heal is not None and duration_s > 0:
                slot["hps"] = total_heal / duration_s
    except (WCLError, KeyError) as exc:
        log.warning("healing table fetch failed for %s#%d: %s", report_code, fight_id, exc)

    return out


def _extract_healer_server_info(blob) -> dict[str, dict]:
    """From the report rankings blob, pull {name: {server_slug, server_region}}
    for every healer entry."""
    if not isinstance(blob, dict):
        return {}
    entries = blob.get("data") or [blob]
    out: dict[str, dict] = {}
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        healers = (entry.get("roles") or {}).get("healers") or {}
        for c in healers.get("characters") or []:
            if not isinstance(c, dict):
                continue
            name = c.get("name")
            server = c.get("server") or {}
            if not name:
                continue
            out[name] = {
                "server_slug": _normalize_realm_slug(server.get("name", "")),
                "server_region": str(server.get("region", "")).upper(),
            }
    return out


def _extract_encounter_id(blob) -> int | None:
    """Encounter ID lives under data[0].encounter.id in the rankings blob."""
    if not isinstance(blob, dict):
        return None
    entries = blob.get("data") or [blob]
    for entry in entries:
        if not isinstance(entry, dict):
            continue
        enc = entry.get("encounter") or {}
        eid = enc.get("id")
        if isinstance(eid, int):
            return eid
    return None


async def _fetch_char_fight_parse(
    client, char_name: str, server_slug: str, server_region: str,
    encounter_id: int, report_code: str, fight_id: int,
) -> float | None:
    """Pull the character's encounterRankings list and locate the parse
    matching (report_code [, fight_id]). Return its per-fight percentile —
    prefer bracketPercent (ilvl-filtered, matches WCL's headline Parse %)
    then rankPercent. None if the character or matching parse isn't found.
    """
    if not (char_name and server_slug and server_region):
        return None
    try:
        data = await client.execute(
            queries.GET_CHARACTER_ENCOUNTER_RANKINGS,
            {
                "name": char_name,
                "serverSlug": server_slug,
                "serverRegion": server_region,
                "encounterID": encounter_id,
                # WCL defaults to "dps" — we always want healing percentiles.
                "metric": "hps",
            },
            cache_ttl_seconds=CACHE_TTL_STATIC,
        )
    except WCLError as exc:
        log.warning("char rankings failed for %s (%s-%s): %s",
                    char_name, server_slug, server_region, exc)
        return None
    char = (data.get("characterData") or {}).get("character")
    if not isinstance(char, dict):
        return None
    blob = char.get("encounterRankings")
    if not isinstance(blob, dict):
        return None
    ranks = blob.get("ranks") or []
    for r in ranks:
        if not isinstance(r, dict):
            continue
        rep = r.get("report") or {}
        if rep.get("code") != report_code:
            continue
        # Some shapes also expose fightID per rank — match when present.
        if rep.get("fightID") is not None and rep.get("fightID") != fight_id:
            continue
        return r.get("bracketPercent") or r.get("rankPercent")
    return None


def _extract_healing_table_data(blob) -> dict[str, dict[str, float]]:
    """Walk the healing-table JSON blob and pull per-player active time and
    total healing. Returns {name: {"active_time_ms", "total_healing"}}.

    Common shape: data.entries[] each with name, activeTime (ms), total
    (raw healing). Tolerant of variants since `table` returns a JSON scalar.
    """
    if not isinstance(blob, dict):
        return {}
    container = blob.get("data") if isinstance(blob.get("data"), dict) else blob
    entries = container.get("entries") or []
    if not isinstance(entries, list):
        return {}
    out: dict[str, dict[str, float]] = {}
    for e in entries:
        if not isinstance(e, dict):
            continue
        name = e.get("name")
        if not name:
            continue
        row: dict[str, float] = {}
        active = e.get("activeTime")
        if isinstance(active, (int, float)):
            row["active_time_ms"] = float(active)
        # Prefer overheal-removed total if present; otherwise raw `total`.
        total = e.get("total")
        if isinstance(total, (int, float)):
            row["total_healing"] = float(total)
        if row:
            out[name] = row
    return out


@router.post("/note", response_model=NoteResponse)
async def note(request: Request, payload: NoteRequest) -> NoteResponse:
    """Format the selected log as an MRT or NSRT note. Bypasses discovery
    — works on any (report, fight) the user knows about."""
    client = _client(request)
    try:
        fc = await fetch_fight_cooldowns(
            client, payload.report_code, payload.fight_id,
        )
    except WCLError as exc:
        raise HTTPException(status_code=502, detail=f"WCL: {exc}") from exc

    # Apply the spell-exclusion filter once and use the trimmed FightCooldowns
    # for both note formatting AND the returned timeline — keeps the textarea,
    # preview, and any other downstream view consistent.
    excluded = set(payload.excluded_spell_ids)
    if excluded:
        from dataclasses import replace
        fc = replace(
            fc,
            events=[ev for ev in fc.events if ev.cast_spell_id not in excluded],
        )

    style = NoteStyle(payload.style)
    note_text = format_note(
        fc, style,
        name_overrides=payload.name_overrides,
        class_overrides=payload.class_overrides,
    )

    # Build the timeline with the SAME substitution rules so the preview /
    # any UI rendering of the timeline matches the textarea exactly.
    from wcl_bot.cooldowns import substitute_for
    timeline: list[TimelineEntry] = []
    for ev in fc.events:
        target_class = payload.class_overrides.get(ev.healer.name)
        if target_class:
            sub = substitute_for(ev.spell, target_class)
            if sub is None:
                continue
            spell_id = sub.spell_id
            spell_label = sub.label
            display_class = target_class
        else:
            spell_id = ev.cast_spell_id
            spell_label = ev.spell.label
            display_class = ev.healer.wow_class
        timeline.append(
            TimelineEntry(
                time_ms=ev.time_into_fight_ms,
                healer_name=payload.name_overrides.get(ev.healer.name, ev.healer.name),
                healer_class=display_class,
                healer_spec=ev.healer.spec,
                spell_label=spell_label,
                spell_id=spell_id,
            )
        )
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
