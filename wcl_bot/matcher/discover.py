"""Cross-guild kill discovery using WCL's server-side comp filter.

`comp_filters` is a list of (healer_count_hint, filter_string) tuples:
  - hint == int  → exact pool; require matching N total healers, exact specs
  - hint is None → broad pool; subset matching (comp specs are present, but
                    fight may have more healers / extras)

We run all pools in parallel. Per-pool failures are isolated and logged —
one bad WCL response doesn't sink the whole request. Results are deduped
by (report_code, fight_id) across pools (exact wins on tie).
"""
from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass

from wcl_bot.matcher.comp import TargetComp
from wcl_bot.matcher.parsing import (
    HealerEntry,
    RankingFight,
    parse_healers,
    parse_rankings_page,
)
from wcl_bot.wcl import queries
from wcl_bot.wcl.client import (
    CACHE_TTL_LEADERBOARD,
    CACHE_TTL_STATIC,
    WCLClient,
    WCLError,
)

log = logging.getLogger(__name__)


@dataclass
class MatchedKill:
    ranking: RankingFight
    healers: list[HealerEntry]

    @property
    def url(self) -> str:
        return (
            f"https://www.warcraftlogs.com/reports/"
            f"{self.ranking.report_code}#fight={self.ranking.fight_id}"
        )


async def find_matching_kills(
    client: WCLClient,
    *,
    encounter_id: int,
    difficulty: int,
    target_comp: TargetComp,
    comp_filters: list[tuple[int | None, str]],
    max_pages: int = 20,
    drop_fastest_pct: float = 0.25,
    skip_top: int = 0,
    metric: str = "execution",
    server_region: str | None = None,
    concurrency: int = 10,
) -> list[MatchedKill]:
    sem = asyncio.Semaphore(concurrency)

    async def evaluate(r: RankingFight, use_subset: bool) -> MatchedKill | None:
        async with sem:
            try:
                data = await client.execute(
                    queries.GET_REPORT_PLAYER_DETAILS,
                    {"code": r.report_code, "fightIDs": [r.fight_id]},
                    cache_ttl_seconds=CACHE_TTL_STATIC,
                )
                healers = parse_healers(data)
            except WCLError as exc:
                log.warning(
                    "playerDetails failed for %s#%d: %s",
                    r.report_code, r.fight_id, exc,
                )
                return None
            class_specs = [(h.wow_class, h.spec) for h in healers]
            ok = (
                target_comp.is_subset_of(class_specs)
                if use_subset
                else target_comp.matches(class_specs)
            )
            if not ok:
                log.warning(
                    "Server-side filter matched but client-side rejected: "
                    "%s#%d healers=%s",
                    r.report_code, r.fight_id, class_specs,
                )
                return None
            return MatchedKill(ranking=r, healers=healers)

    async def run_pool(
        hint: int | None, filter_str: str
    ) -> list[MatchedKill]:
        label = "exact" if hint is not None else "broad"
        try:
            rankings = await _fetch_rankings(
                client,
                encounter_id=encounter_id,
                difficulty=difficulty,
                max_pages=max_pages,
                metric=metric,
                server_region=server_region,
                comp_filter=filter_str,
            )
        except WCLError as exc:
            log.warning("[%s] fightRankings failed (%s) — pool yields 0", label, exc)
            return []

        log.info("[%s] Discovered %d ranking entries", label, len(rankings))
        # Dedupe within pool.
        seen: set[tuple[str, int]] = set()
        unique: list[RankingFight] = []
        for r in rankings:
            key = (r.report_code, r.fight_id)
            if key in seen:
                continue
            seen.add(key)
            unique.append(r)

        if skip_top > 0 and unique:
            actual = min(skip_top, len(unique) // 2)
            if actual < skip_top:
                log.warning(
                    "[%s] skip_top=%d clamped to %d (half of pool of %d)",
                    label, skip_top, actual, len(unique),
                )
            unique = unique[actual:]

        use_subset = hint is None
        results = await asyncio.gather(
            *(evaluate(r, use_subset) for r in unique),
            return_exceptions=False,  # evaluate catches its own
        )
        pool_matches = [m for m in results if m is not None]
        log.info("[%s] Matched %d / %d unique fights", label, len(pool_matches), len(unique))

        pool_matches.sort(key=lambda m: m.ranking.duration_ms, reverse=True)
        if pool_matches and drop_fastest_pct > 0:
            keep = len(pool_matches) - int(len(pool_matches) * drop_fastest_pct)
            pool_matches = pool_matches[: max(1, keep)]

        return pool_matches

    # Run all pools in parallel. Order them so the exact pool's results
    # appear first in the dedupe pass (exact wins ties).
    sorted_filters = sorted(comp_filters, key=lambda kv: 0 if kv[0] is not None else 1)
    pool_results = await asyncio.gather(*(run_pool(h, f) for h, f in sorted_filters))

    # Cross-pool dedupe by (report, fight). Earlier pool wins.
    seen: set[tuple[str, int]] = set()
    merged: list[MatchedKill] = []
    for pool in pool_results:
        for m in pool:
            key = (m.ranking.report_code, m.ranking.fight_id)
            if key in seen:
                continue
            seen.add(key)
            merged.append(m)

    log.info("Total matched (deduped across pools): %d", len(merged))
    return merged


async def _fetch_rankings(
    client: WCLClient,
    *,
    encounter_id: int,
    difficulty: int,
    max_pages: int,
    metric: str,
    server_region: str | None,
    comp_filter: str,
) -> list[RankingFight]:
    out: list[RankingFight] = []
    for page in range(1, max_pages + 1):
        data = await client.execute(
            queries.GET_ENCOUNTER_FIGHT_RANKINGS,
            {
                "encounterID": encounter_id,
                "difficulty": difficulty,
                "page": page,
                "metric": metric,
                "serverRegion": server_region,
                "filter": comp_filter,
            },
            cache_ttl_seconds=CACHE_TTL_LEADERBOARD,
        )
        blob = data["worldData"]["encounter"]["fightRankings"]
        fights, has_more = parse_rankings_page(blob)
        out.extend(fights)
        if not has_more:
            break
    return out
