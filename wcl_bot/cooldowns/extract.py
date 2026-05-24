"""Per-fight extraction of tracked healer cooldown casts."""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from wcl_bot.cooldowns.spells import CooldownSpell, spells_for
from wcl_bot.matcher.discover import MatchedKill
from wcl_bot.matcher.parsing import HealerEntry, RankingFight, parse_healers
from wcl_bot.wcl import queries
from wcl_bot.wcl.client import CACHE_TTL_STATIC, WCLClient, WCLError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class CooldownEvent:
    healer: HealerEntry
    spell: CooldownSpell
    cast_spell_id: int       # actual spell ID that fired (matters for aliases:
                             # Yu'lon vs Chi-Ji, Revival vs Restoral, etc.)
    time_into_fight_ms: int  # ms since fight start


@dataclass
class FightCooldowns:
    kill: MatchedKill
    fight_start_ms: int  # report-relative
    fight_end_ms: int    # report-relative
    events: list[CooldownEvent]  # sorted ascending by time_into_fight_ms

    @property
    def fight_duration_ms(self) -> int:
        return self.fight_end_ms - self.fight_start_ms


async def fetch_fight_cooldowns(
    client: WCLClient,
    report_code: str,
    fight_id: int,
) -> FightCooldowns:
    """Extract cooldowns from a specific (report, fight), bypassing the
    leaderboard discovery + comp filter pipeline.

    Used by the web /api/note endpoint and any caller that already knows
    the exact log it wants to inspect. Reuses the same cached playerDetails
    + events queries that the discovery pipeline populates.
    """
    pd = await client.execute(
        queries.GET_REPORT_PLAYER_DETAILS,
        {"code": report_code, "fightIDs": [fight_id]},
        cache_ttl_seconds=CACHE_TTL_STATIC,
    )
    healers = parse_healers(pd)
    info = await client.execute(
        queries.GET_FIGHT_TIMES,
        {"code": report_code, "fightID": fight_id},
        cache_ttl_seconds=CACHE_TTL_STATIC,
    )
    fights = info["reportData"]["report"]["fights"]
    if not fights:
        raise WCLError(f"Fight {fight_id} not found in report {report_code}")
    f = fights[0]
    synthetic_ranking = RankingFight(
        report_code=report_code,
        fight_id=fight_id,
        duration_ms=int(f["endTime"]) - int(f["startTime"]),
        guild_name=None,
        server_region=None,
        server_slug=None,
        rank=None,
        percentile=None,
        healer_count=len(healers),
    )
    kill = MatchedKill(ranking=synthetic_ranking, healers=healers)
    results = await extract_cooldowns(client, [kill], concurrency=1)
    return results[0]


async def extract_cooldowns(
    client: WCLClient,
    matched_kills: list[MatchedKill],
    *,
    concurrency: int = 5,
) -> list[FightCooldowns]:
    """For each matched kill, fetch its cast events and filter to tracked CDs.

    Skips fights where the events query fails; logs a warning so a bad
    report can't fail the whole batch.
    """
    sem = asyncio.Semaphore(concurrency)

    async def one(kill: MatchedKill) -> FightCooldowns | None:
        async with sem:
            try:
                return await _extract_one(client, kill)
            except WCLError as exc:
                log.warning(
                    "extract failed for %s#%d: %s",
                    kill.ranking.report_code,
                    kill.ranking.fight_id,
                    exc,
                )
                return None

    results = await asyncio.gather(*(one(k) for k in matched_kills))
    return [r for r in results if r is not None]


async def _extract_one(client: WCLClient, kill: MatchedKill) -> FightCooldowns:
    # 1. Fight time window — needed for absolute → relative conversion.
    info = await client.execute(
        queries.GET_FIGHT_TIMES,
        {"code": kill.ranking.report_code, "fightID": kill.ranking.fight_id},
        cache_ttl_seconds=CACHE_TTL_STATIC,
    )
    fights = info["reportData"]["report"]["fights"]
    if not fights:
        raise WCLError(
            f"fight {kill.ranking.fight_id} not found in report {kill.ranking.report_code}"
        )
    fight_start = int(fights[0]["startTime"])
    fight_end = int(fights[0]["endTime"])

    # 2. Build per-healer lookup of tracked spell IDs → CooldownSpell.
    healers_by_id: dict[int, HealerEntry] = {h.source_id: h for h in kill.healers}
    cd_lookup: dict[int, dict[int, CooldownSpell]] = {}
    for h in kill.healers:
        spell_map: dict[int, CooldownSpell] = {}
        for spell in spells_for(h.wow_class, h.spec):
            for sid in spell.all_ids:
                spell_map[sid] = spell
        cd_lookup[h.source_id] = spell_map

    # 3. Page through cast events covering the fight window.
    raw_events: list[dict] = []
    next_ts: int | None = None
    while True:
        page = await client.execute(
            queries.GET_CASTS_PAGE,
            {
                "code": kill.ranking.report_code,
                "startTime": float(next_ts if next_ts is not None else fight_start),
                "endTime": float(fight_end),
            },
            cache_ttl_seconds=CACHE_TTL_STATIC,
        )
        paginator = page["reportData"]["report"]["events"]
        raw_events.extend(paginator.get("data") or [])
        next_ts = paginator.get("nextPageTimestamp")
        if not next_ts or next_ts >= fight_end:
            break

    # 4. Filter to (healer source, tracked spell) and build CooldownEvent list.
    cooldown_events: list[CooldownEvent] = []
    for ev in raw_events:
        if ev.get("type") != "cast":
            continue
        src = ev.get("sourceID")
        spell_lookup = cd_lookup.get(src) if src is not None else None
        if not spell_lookup:
            continue
        ability_id = ev.get("abilityGameID")
        if ability_id is None:
            ability = ev.get("ability")
            if isinstance(ability, dict):
                ability_id = ability.get("guid")
        if ability_id is None:
            continue
        spell = spell_lookup.get(ability_id)
        if spell is None:
            continue
        cooldown_events.append(
            CooldownEvent(
                healer=healers_by_id[src],
                spell=spell,
                cast_spell_id=int(ability_id),
                time_into_fight_ms=int(ev["timestamp"]) - fight_start,
            )
        )

    cooldown_events.sort(key=lambda e: e.time_into_fight_ms)
    log.info(
        "Extracted %d cooldown events from %s#%d (%d raw casts)",
        len(cooldown_events),
        kill.ranking.report_code,
        kill.ranking.fight_id,
        len(raw_events),
    )
    return FightCooldowns(
        kill=kill,
        fight_start_ms=fight_start,
        fight_end_ms=fight_end,
        events=cooldown_events,
    )
