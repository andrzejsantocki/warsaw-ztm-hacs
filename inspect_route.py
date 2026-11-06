"""Print candidate posts and their common-line headsigns for configuration.

Usage: python inspect_route.py "Start stop" "End stop" [line]
"""
import asyncio
import os
import sys

import aiohttp
from live_check import load_env
from warsaw_transit_core.api import TransitClient


async def main(names, line):
    load_env()
    async with aiohttp.ClientSession() as session:
        c = TransitClient(session, os.environ["WARSAW_API_KEY"])
        stops = await c.stops()
        for name in names:
            for stop in [s for s in stops if s.name == name]:
                lines = await c.lines(stop)
                print(name, stop.group, stop.post, lines)
                if line and line in lines:
                    deps = await c.timetable(stop, line)
                    print(f"sample{line}", [(d.time, d.brigade, d.direction, d.trip) for d in deps[:4]])


if __name__ == "__main__":
    if len(sys.argv) < 3:
        sys.exit(__doc__)
    asyncio.run(main(sys.argv[1:3], sys.argv[3] if len(sys.argv) > 3 else None))
