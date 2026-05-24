"""Confirm every tracked cooldown spell ID resolves to the expected name
via WCL's gameData.ability lookup.

Usage:
    python -m examples.verify_spell_ids

Prints a MISMATCH or NOT FOUND line for any ID that doesn't resolve to a name
matching what's in spells.py. Run this whenever spell IDs are added or after
a major patch to catch broken IDs early.
"""
from __future__ import annotations

import asyncio

from dotenv import load_dotenv

from wcl_bot.cooldowns import HEALER_COOLDOWNS
from wcl_bot.wcl import WCLClient
from wcl_bot.wcl.client import CACHE_TTL_GAME_DATA

LOOKUP_ABILITY = """
query LookupAbility($id: Int!) {
  gameData {
    ability(id: $id) {
      id
      name
    }
  }
}
"""


async def main() -> int:
    problems = 0
    async with WCLClient() as client:
        for (wow_class, spec), spells in HEALER_COOLDOWNS.items():
            print(f"\n{wow_class}/{spec}")
            for spell in spells:
                for sid in spell.all_ids:
                    is_alias = sid != spell.spell_id
                    suffix = " (alias)" if is_alias else ""
                    try:
                        data = await client.execute(
                            LOOKUP_ABILITY,
                            {"id": sid},
                            cache_ttl_seconds=CACHE_TTL_GAME_DATA,
                        )
                    except Exception as e:
                        print(f"  ERROR  {sid:>7}  {spell.name}{suffix} — {e}")
                        problems += 1
                        continue
                    ability = data.get("gameData", {}).get("ability")
                    if not ability:
                        print(f"  NOT FOUND  {sid:>7}  expected {spell.name}{suffix}")
                        problems += 1
                        continue
                    api_name = ability["name"]
                    # For aliases, expect a different name; just print it so user can eyeball.
                    if is_alias:
                        print(f"  ok     {sid:>7}  alias resolves to {api_name!r}")
                    elif api_name == spell.name:
                        print(f"  ok     {sid:>7}  {api_name}")
                    else:
                        print(
                            f"  MISMATCH {sid:>7}  expected {spell.name!r}, "
                            f"got {api_name!r}"
                        )
                        problems += 1
    print(f"\n{problems} problem(s)." if problems else "\nAll IDs verified.")
    return 1 if problems else 0


if __name__ == "__main__":
    load_dotenv()
    raise SystemExit(asyncio.run(main()))
