"""List WCL zones + their encounter IDs. Use this to find the encounter_id
to pass to find_matches.

Usage:
    python -m examples.list_zones                    # list all zones
    python -m examples.list_zones <substring>        # filter to zones whose
                                                       name contains substring
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from wcl_bot.wcl import WCLClient

LIST_ZONES = """
query ListZones {
  worldData {
    zones {
      id
      name
      frozen
      expansion { id name }
      encounters { id name }
    }
  }
}
"""


async def main(needle: str | None) -> None:
    async with WCLClient() as client:
        data = await client.execute(LIST_ZONES)
    zones = data["worldData"]["zones"]
    for z in zones:
        name = z["name"]
        if needle and needle.lower() not in name.lower():
            continue
        exp = z["expansion"]["name"] if z.get("expansion") else "?"
        frozen = " [FROZEN]" if z.get("frozen") else ""
        print(f"\nzone {z['id']:>4}  {name}  ({exp}){frozen}")
        for enc in z.get("encounters") or []:
            print(f"    encounter {enc['id']:>5}  {enc['name']}")


if __name__ == "__main__":
    load_dotenv()
    asyncio.run(main(sys.argv[1] if len(sys.argv) > 1 else None))
