"""Dump unique (spell_id, name) cast by one player in one fight.

Useful for finding spell IDs that don't show up in nearby-ID probes — e.g.
talent variants, hero-talent additions, store/release pairs.

Usage:
    python -m examples.probe_caster_spells <report_code> <fight_id> <player_name> [name_filter]

Example:
    python -m examples.probe_caster_spells d4cyWPRzHJaX8Tqv 14 "<evoker name>" stasis
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from wcl_bot.matcher.parsing import _find_player_details
from wcl_bot.wcl import WCLClient, queries

EVENTS = """
query CasterCasts($code: String!, $fightID: Int!, $sourceID: Int!) {
  reportData {
    report(code: $code) {
      events(
        fightIDs: [$fightID]
        sourceID: $sourceID
        dataType: Casts
        limit: 10000
      ) {
        data
        nextPageTimestamp
      }
    }
  }
}
"""


def _find_source_id(player_details_blob: dict, name: str) -> int | None:
    pd = _find_player_details(player_details_blob)
    if not isinstance(pd, dict):
        return None
    for role_key in ("tanks", "healers", "dps"):
        for entry in pd.get(role_key, []) or []:
            if entry.get("name", "").lower() == name.lower():
                return entry["id"]
    for entry in pd.get("entries", []) or []:
        if entry.get("name", "").lower() == name.lower():
            return entry["id"]
    return None


async def main(code: str, fight_id: int, player_name: str, name_filter: str | None) -> int:
    async with WCLClient() as client:
        pd = await client.execute(
            queries.GET_REPORT_PLAYER_DETAILS,
            {"code": code, "fightIDs": [fight_id]},
        )
        source_id = _find_source_id(pd, player_name)
        if source_id is None:
            print(f"Player {player_name!r} not found in fight {fight_id}.")
            pd_inner = _find_player_details(pd) or {}
            names = []
            for role in ("tanks", "healers", "dps"):
                for e in pd_inner.get(role, []) or []:
                    names.append(e.get("name"))
            print(f"Players in this fight: {names}")
            return 1
        print(f"Resolved {player_name!r} → sourceID {source_id}")

        data = await client.execute(
            EVENTS,
            {"code": code, "fightID": fight_id, "sourceID": source_id},
        )
        events = data["reportData"]["report"]["events"]["data"] or []

    seen: dict[int, str] = {}
    for ev in events:
        ability = ev.get("ability") or {}
        sid = ability.get("guid") or ev.get("abilityGameID")
        name = ability.get("name") or "<unknown>"
        if sid and sid not in seen:
            seen[sid] = name

    needle = name_filter.lower() if name_filter else None
    print(f"\n{len(seen)} unique cast spell IDs in this fight:")
    for sid, name in sorted(seen.items(), key=lambda kv: kv[1]):
        if needle and needle not in name.lower():
            continue
        print(f"  {sid:>7}  {name}")
    return 0


if __name__ == "__main__":
    if len(sys.argv) < 4:
        print(__doc__)
        sys.exit(1)
    load_dotenv()
    raise SystemExit(asyncio.run(main(
        sys.argv[1],
        int(sys.argv[2]),
        sys.argv[3],
        sys.argv[4] if len(sys.argv) > 4 else None,
    )))
