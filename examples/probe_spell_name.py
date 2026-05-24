"""Probe WCL gameData.ability over an ID range and print any whose name
contains a given substring. One-off helper for finding related spell IDs.

Usage:
    python -m examples.probe_spell_name <substring> <start_id> <end_id>

Example:
    python -m examples.probe_spell_name stasis 370530 370560
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from wcl_bot.wcl import WCLClient

LOOKUP = """
query LookupAbility($id: Int!) {
  gameData { ability(id: $id) { id name } }
}
"""


async def main(needle: str, start: int, end: int) -> None:
    needle_lc = needle.lower()
    async with WCLClient() as client:
        sem = asyncio.Semaphore(10)

        async def check(sid: int) -> None:
            async with sem:
                try:
                    data = await client.execute(LOOKUP, {"id": sid})
                except Exception:
                    return
                ability = data.get("gameData", {}).get("ability")
                if ability and needle_lc in ability["name"].lower():
                    print(f"  {sid}: {ability['name']}")

        await asyncio.gather(*(check(i) for i in range(start, end + 1)))


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    load_dotenv()
    asyncio.run(main(sys.argv[1], int(sys.argv[2]), int(sys.argv[3])))
