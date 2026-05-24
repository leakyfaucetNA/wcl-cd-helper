"""Dump raw fightRankings + playerDetails JSON for shape debugging.

Usage:
    python -m examples.debug_shapes <encounter_id> <difficulty>
"""
from __future__ import annotations

import asyncio
import json
import sys

from dotenv import load_dotenv

from wcl_bot.matcher.parsing import parse_healers, parse_rankings_page
from wcl_bot.wcl import WCLClient, queries


async def main(encounter_id: int, difficulty: int) -> None:
    async with WCLClient() as client:
        # Page 1 of rankings
        rdata = await client.execute(
            queries.GET_ENCOUNTER_FIGHT_RANKINGS,
            {
                "encounterID": encounter_id,
                "difficulty": difficulty,
                "page": 1,
                "metric": "speed",
                "serverRegion": None,
            },
        )
        rblob = rdata["worldData"]["encounter"]["fightRankings"]
        print("=" * 70)
        print("FIGHTRANKINGS blob — top-level keys:", list(rblob) if isinstance(rblob, dict) else type(rblob).__name__)
        print("=" * 70)
        # Print just first entry pretty
        if isinstance(rblob, dict) and isinstance(rblob.get("rankings"), list) and rblob["rankings"]:
            print("first ranking entry:")
            print(json.dumps(rblob["rankings"][0], indent=2)[:2000])
        else:
            print(json.dumps(rblob, indent=2)[:2000])

        fights, _ = parse_rankings_page(rblob)
        if not fights:
            print("no fights parsed; aborting")
            return
        sample = fights[0]
        print()
        print("=" * 70)
        print(f"PLAYERDETAILS for {sample.report_code} fight {sample.fight_id}")
        print("=" * 70)
        pdata = await client.execute(
            queries.GET_REPORT_PLAYER_DETAILS,
            {"code": sample.report_code, "fightIDs": [sample.fight_id]},
        )
        print("top-level keys:", list(pdata) if isinstance(pdata, dict) else type(pdata).__name__)
        print(json.dumps(pdata, indent=2)[:4000])
        print()
        print("=" * 70)
        print("parse_healers result:")
        try:
            print(parse_healers(pdata))
        except Exception as e:
            print(f"ERROR: {type(e).__name__}: {e}")


if __name__ == "__main__":
    if len(sys.argv) < 3:
        print(__doc__)
        sys.exit(1)
    load_dotenv()
    asyncio.run(main(int(sys.argv[1]), int(sys.argv[2])))
