"""Find cross-guild kill logs matching a healer comp.

Usage:
    python -m examples.find_matches <encounter_id> <difficulty> <comp_spec> [options]

Comp spec is comma-separated. Each token is one of:
    Class           — any healer spec of this class      (e.g. "Paladin")
    Class/Spec      — exact spec                         (e.g. "Priest/Holy")
    short alias     — e.g. "hpriest", "rdruid", "preg"

Examples:
    python -m examples.find_matches 3009 5 "hpriest,hpal,rdruid,preg"
    python -m examples.find_matches 3009 5 "Priest/Holy,Paladin,Druid/Restoration,Evoker/Preservation" --region US

Options:
    --pages N          max ranking pages to fetch (default 20). Pages are
                       cheap; pre-filter by healer count keeps API cost low.
    --skip-top N       skip the top N ranked fights (default 100) to avoid
                       world-first / HoF guilds whose strats aren't replicable
    --metric M         "execution" (parse %, default) or "speed"
    --region REGION    US / EU / KR / TW (default: all)
    --serial           use concurrency=1 (for debugging)
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

from wcl_bot.matcher import find_matching_kills, parse_comp_spec
from wcl_bot.wcl import WCLClient


def _fmt_duration(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


async def main(args: argparse.Namespace) -> int:
    target_comp = parse_comp_spec(args.comp)
    print(f"Target comp ({len(target_comp.slots)} healers):")
    for slot in target_comp.slots:
        print(f"  - {slot.wow_class}/{slot.spec or '*any healer spec*'}")
    print()

    async with WCLClient() as client:
        matches = await find_matching_kills(
            client,
            encounter_id=args.encounter_id,
            difficulty=args.difficulty,
            target_comp=target_comp,
            max_pages=args.pages,
            skip_top=args.skip_top,
            metric=args.metric,
            server_region=args.region,
            concurrency=1 if args.serial else 10,
        )

    if not matches:
        print("No matching kills found.")
        return 1

    print(f"\n{len(matches)} matching kills (sorted longest first):\n")
    for m in matches:
        healer_str = ", ".join(
            f"{h.name} ({h.wow_class}/{h.spec})" for h in m.healers
        )
        guild = m.ranking.guild_name or "?"
        region = m.ranking.server_region or "?"
        print(
            f"  [{_fmt_duration(m.ranking.duration_ms):>5}] "
            f"{guild:<24} ({region}) "
            f"rank={m.ranking.rank} "
            f"pct={m.ranking.percentile}\n"
            f"          healers: {healer_str}\n"
            f"          {m.url}\n"
        )
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("encounter_id", type=int)
    p.add_argument("difficulty", type=int, help="3=Normal, 4=Heroic, 5=Mythic")
    p.add_argument("comp", help='e.g. "hpriest,hpal,rdruid,preg"')
    p.add_argument("--pages", type=int, default=20)
    p.add_argument(
        "--skip-top",
        type=int,
        default=100,
        help="Skip the top N ranked fights (world-first / HoF guilds)",
    )
    p.add_argument(
        "--metric",
        default="execution",
        choices=["execution", "speed"],
    )
    p.add_argument("--region", default=None)
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
