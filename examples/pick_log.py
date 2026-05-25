"""Discover matching kills (server-side comp filter), score each, let the
user pick one to inspect (interactively or via --pick N), and print that
log's CD timeline — as a plain table or as an MRT/NSRT raid note.

Usage:
    python -m examples.pick_log <encounter_id> <difficulty> <comp_spec> [options]
    python -m examples.pick_log 3177 5 "hpriest,hpal,rdruid,preg" --pick 2
    python -m examples.pick_log 3180 5 "disc,rsham,rdruid,rdruid,mw,hpal" --format nsrt --pick 1
"""
from __future__ import annotations

import argparse
import asyncio
import logging
import sys

log = logging.getLogger(__name__)

from dotenv import load_dotenv

from wcl_bot.cooldowns import (
    FightCooldowns,
    LogScore,
    extract_cooldowns,
    score_logs,
)
from wcl_bot.matcher import (
    CompFilterError,
    build_comp_filters,
    find_matching_kills,
    parse_comp_spec,
)
from wcl_bot.notes import NoteStyle, format_note
from wcl_bot.wcl import WCLClient


def _fmt_mmss(ms: int) -> str:
    s = ms // 1000
    return f"{s // 60}:{s % 60:02d}"


def _fmt_seconds(ms: int) -> str:
    return f"{ms / 1000:.1f}s"


async def _fetch(args: argparse.Namespace) -> list[FightCooldowns]:
    target_comp = parse_comp_spec(args.comp)
    try:
        filters = build_comp_filters(target_comp)
    except CompFilterError as exc:
        print(f"Comp filter cannot be built: {exc}", file=sys.stderr)
        sys.exit(2)
    for hc, f in filters:
        print(f"Server-side filter [heal.{hc}]: {f}", file=sys.stderr)
    print(file=sys.stderr)

    async with WCLClient(cache_disabled=args.no_cache) as client:
        matches = await find_matching_kills(
            client,
            encounter_id=args.encounter_id,
            difficulty=args.difficulty,
            target_comp=target_comp,
            comp_filters=filters,
            max_pages=args.pages,
            skip_top=args.skip_top,
            metric=args.metric,
            server_region=args.region,
            concurrency=1 if args.serial else 10,
        )
        if not matches:
            return []
        if args.limit:
            matches = matches[: args.limit]
        results = await extract_cooldowns(
            client, matches, concurrency=1 if args.serial else 5
        )
        if not args.no_cache:
            log.info(
                "WCL cache: %d hits, %d misses",
                client.cache_hits, client.cache_misses,
            )
        return results


def _print_summary(scores: list[LogScore]) -> None:
    print(f"\n{len(scores)} matching kills:\n")
    for s in scores:
        r = s.fight.kill.ranking
        guild = r.guild_name or "?"
        region = r.server_region or "?"
        guild_region = f"{guild} ({region})"
        print(
            f"  [{s.index}] {_fmt_mmss(s.fight.fight_duration_ms):>4}  "
            f"{guild_region:<28} "
            f"{s.n_presses:>2} presses / {s.n_unique_spells:>2} CDs   "
            f"{_fmt_seconds(s.avg_shift_ms):>6} avg shift"
        )
    print()


def _print_timeline_plain(fc: FightCooldowns) -> None:
    r = fc.kill.ranking
    print(
        f"\n=== {r.guild_name or '?'} ({r.server_region or '?'}) "
        f"{_fmt_mmss(fc.fight_duration_ms)} — {fc.kill.url}\n"
    )
    if not fc.events:
        print("  (no tracked cooldowns)\n")
        return
    for ev in fc.events:
        print(
            f"  {_fmt_mmss(ev.time_into_fight_ms):>5}  "
            f"{ev.healer.name:<15} "
            f"{ev.healer.wow_class}/{ev.healer.spec:<12} "
            f"{ev.spell.label}"
        )
    print()


def _print_timeline_note(fc: FightCooldowns, style: NoteStyle) -> None:
    r = fc.kill.ranking
    header = (
        f"\n=== {r.guild_name or '?'} ({r.server_region or '?'}) "
        f"{_fmt_mmss(fc.fight_duration_ms)} — {fc.kill.url} "
        f"[{style.value.upper()}]\n"
    )
    print(header, file=sys.stderr)
    if not fc.events:
        print("(no tracked cooldowns)", file=sys.stderr)
        return
    # Note body goes to stdout so it can be piped/redirected cleanly.
    print(format_note(fc, style))
    print()


def _prompt_choice(n: int) -> int:
    while True:
        try:
            raw = input(f"Pick a log [1-{n}] (or q to quit): ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            sys.exit(0)
        if raw.lower() in ("q", "quit", "exit"):
            sys.exit(0)
        try:
            choice = int(raw)
        except ValueError:
            print(f"  '{raw}' isn't a number. Try again.")
            continue
        if 1 <= choice <= n:
            return choice
        print(f"  out of range — pick between 1 and {n}.")


def main() -> int:
    args = _parse_args()
    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(levelname)s %(name)s: %(message)s",
    )
    load_dotenv()

    fight_cds = asyncio.run(_fetch(args))
    if not fight_cds:
        print("No matching kills.")
        return 1

    scores = score_logs(fight_cds)

    if args.pick is not None:
        if not (1 <= args.pick <= len(scores)):
            print(f"--pick {args.pick} out of range (1-{len(scores)}).")
            return 1
        choice = args.pick
    else:
        _print_summary(scores)
        choice = _prompt_choice(len(scores))

    selected = scores[choice - 1].fight
    if args.format == "plain":
        _print_timeline_plain(selected)
    else:
        _print_timeline_note(selected, NoteStyle(args.format))
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
        default=0,
        help="Skip the top N ranked fights (default 0 — server-side comp "
             "filter already narrows the pool; HoF-guild skipping is rarely "
             "useful here).",
    )
    p.add_argument("--metric", default="execution", choices=["execution", "speed"])
    p.add_argument("--region", default=None)
    p.add_argument("--limit", type=int, default=None)
    p.add_argument(
        "--pick",
        type=int,
        default=None,
        help="Non-interactive: print this log's timeline directly.",
    )
    p.add_argument(
        "--format",
        default="plain",
        choices=["plain", "nsrt", "mrt"],
        help="Output format. 'plain' = human-readable table (default); "
             "'nsrt' = Northern Sky Raid Tools note string; "
             "'mrt' = Method Raid Tools note string.",
    )
    p.add_argument(
        "--no-cache",
        action="store_true",
        help="Bypass the WCL query cache (~/.cache/wcl_bot/queries/). Force a "
             "fresh API hit for every query, including ones we have cached.",
    )
    p.add_argument("--serial", action="store_true")
    p.add_argument("-v", "--verbose", action="store_true")
    return p.parse_args()


if __name__ == "__main__":
    sys.exit(main())
