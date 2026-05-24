"""End-to-end: discover → extract → aggregate cooldown rotation timeline.

Usage:
    python -m examples.aggregate_cooldowns <encounter_id> <difficulty> <comp_spec> [options]

Output: a clustered timeline of when each (class, spec, spell) is pressed
across all matched fights.
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

from wcl_bot.cooldowns import aggregate_cooldowns, extract_cooldowns
from wcl_bot.matcher import find_matching_kills, parse_comp_spec
from wcl_bot.wcl import WCLClient


def _fmt_mmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


async def main(args: argparse.Namespace) -> int:
    target_comp = parse_comp_spec(args.comp)

    async with WCLClient() as client:
        matches = await find_matching_kills(
            client,
            encounter_id=args.encounter_id,
            difficulty=args.difficulty,
            target_comp=target_comp,
            max_pages=args.pages,
            drop_fastest_pct=args.drop_pct,
            skip_top=args.skip_top,
            metric=args.metric,
            server_region=args.region,
            concurrency=1 if args.serial else 10,
        )
        if not matches:
            print("No matching kills.")
            return 1
        if args.limit:
            matches = matches[: args.limit]

        fight_cds = await extract_cooldowns(
            client, matches, concurrency=1 if args.serial else 5
        )

    aggregated = aggregate_cooldowns(
        fight_cds,
        cluster_tolerance_ms=int(args.cluster_tolerance * 1000),
        min_confidence=args.min_confidence,
        include_below_threshold=args.show_all,
    )

    print(f"\nAggregated rotation across {len(fight_cds)} fight(s) "
          f"(cluster ±{args.cluster_tolerance}s, "
          f"min_confidence {args.min_confidence:.0%}):\n")
    if not aggregated:
        print("  (no clusters met the confidence threshold)")
        return 0

    for a in aggregated:
        confidence_marker = "" if a.confidence >= args.min_confidence else "  [LOW]"
        print(
            f"  {_fmt_mmss(a.mean_time_ms):>5} ±{a.std_dev_ms // 1000}s  "
            f"{a.healer_class:>7}/{a.healer_spec:<12} "
            f"{a.spell.label:<24} "
            f"({a.fights_seen}/{a.fights_total}){confidence_marker}"
        )
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("encounter_id", type=int)
    p.add_argument("difficulty", type=int, help="3=Normal, 4=Heroic, 5=Mythic")
    p.add_argument("comp", help='e.g. "hpriest,hpal,rdruid,preg"')
    p.add_argument("--pages", type=int, default=20)
    p.add_argument("--drop-pct", type=float, default=0.25)
    p.add_argument("--skip-top", type=int, default=100)
    p.add_argument("--metric", default="execution", choices=["execution", "speed"])
    p.add_argument("--region", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--cluster-tolerance",
        type=float,
        default=5.0,
        help="Max seconds between consecutive presses in a cluster (default 5).",
    )
    p.add_argument(
        "--min-confidence",
        type=float,
        default=0.5,
        help="Drop clusters seen in fewer than this fraction of fights (default 0.5).",
    )
    p.add_argument(
        "--show-all",
        action="store_true",
        help="Include below-threshold clusters in the output (diagnostic).",
    )
    p.add_argument("--serial", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()
    sys.exit(asyncio.run(main(args)))
