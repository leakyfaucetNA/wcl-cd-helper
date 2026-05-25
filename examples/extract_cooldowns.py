"""End-to-end: discover matching kills + extract cooldown timings.

Usage:
    python -m examples.extract_cooldowns <encounter_id> <difficulty> <comp_spec> [options]

Options match `find_matches.py`. Output: per matched kill, a timeline of every
tracked cooldown press (caster, spell, time-into-fight).
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

from dotenv import load_dotenv

from wcl_bot.cooldowns import extract_cooldowns
from wcl_bot.matcher import find_matching_kills, parse_comp_spec
from wcl_bot.wcl import WCLClient


def _fmt_mmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


async def main(args: argparse.Namespace) -> int:
    target_comp = parse_comp_spec(args.comp)
    print(f"Target comp: {', '.join(f'{s.wow_class}/{s.spec or 'any'}' for s in target_comp.slots)}\n")

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
            print("No matching kills.")
            return 1
        if args.limit:
            matches = matches[: args.limit]
        print(f"Extracting cooldowns from {len(matches)} fight(s)...\n")
        fight_cds = await extract_cooldowns(
            client, matches, concurrency=1 if args.serial else 5
        )

    for fc in fight_cds:
        guild = fc.kill.ranking.guild_name or "?"
        region = fc.kill.ranking.server_region or "?"
        dur = _fmt_mmss(fc.fight_duration_ms)
        print(f"=== {guild} ({region}) — {dur} — {fc.kill.url}")
        if not fc.events:
            print("    (no tracked cooldowns found)")
            print()
            continue
        for ev in fc.events:
            print(
                f"    {_fmt_mmss(ev.time_into_fight_ms):>5}  "
                f"{ev.healer.name:<15} "
                f"{ev.healer.wow_class}/{ev.healer.spec:<12} "
                f"{ev.spell.label}"
            )
        print()
    return 0


def _parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument("encounter_id", type=int)
    p.add_argument("difficulty", type=int, help="3=Normal, 4=Heroic, 5=Mythic")
    p.add_argument("comp", help='e.g. "hpriest,hpal,rdruid,preg"')
    p.add_argument("--pages", type=int, default=20)
    p.add_argument("--skip-top", type=int, default=100)
    p.add_argument("--metric", default="execution", choices=["execution", "speed"])
    p.add_argument("--region", default=None)
    p.add_argument("--limit", type=int, default=None, help="Only extract from first N matches")
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
