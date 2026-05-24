"""Smoke test for the WCL GraphQL client.

Usage:
    python -m examples.fetch_report <report_code>

Loads WCL_CLIENT_ID / WCL_CLIENT_SECRET from .env (or the environment),
fetches a report's kill fights, and prints a summary.
"""
from __future__ import annotations

import asyncio
import sys

from dotenv import load_dotenv

from wcl_bot.wcl import Report, WCLClient, queries


async def main(code: str) -> None:
    async with WCLClient() as client:
        data = await client.execute(
            queries.GET_REPORT_FIGHTS, {"code": code}
        )
        report = Report.from_dict(data["reportData"]["report"])

    print(f"Report: {report.title} ({report.code})")
    print(f"Zone:   {report.zone.name if report.zone else 'unknown'}")
    print(f"Kills:  {len(report.fights)}")
    for fight in report.fights:
        duration_s = (fight.end_time - fight.start_time) / 1000
        print(
            f"  [{fight.id:>3}] {fight.name:<30} "
            f"enc={fight.encounter_id} diff={fight.difficulty} "
            f"size={fight.size} dur={duration_s:.0f}s"
        )


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print("Usage: python -m examples.fetch_report <report_code>")
        sys.exit(1)
    load_dotenv()
    asyncio.run(main(sys.argv[1]))
