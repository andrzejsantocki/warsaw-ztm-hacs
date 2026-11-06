"""Exercise the same transport engine without a Home Assistant installation."""
import argparse
import asyncio
import importlib.util
import json
import os
from pathlib import Path
import sys
import types

ROOT = Path(__file__).resolve().parent
# Load the core as a namespace package; do not execute the HA adapter __init__.
package = types.ModuleType("warsaw_transit_core")
package.__path__ = [str(ROOT / "custom_components" / "warsaw_transit")]
sys.modules[package.__name__] = package


def load_env():
    """Read literal KEY=VALUE settings, never execute a shell file."""
    path = ROOT / ".env"
    if path.exists():
        for line in path.read_text().splitlines():
            if line.strip() and not line.lstrip().startswith("#"):
                key, separator, value = line.partition("=")
                if separator and key.strip() in {"WARSAW_API_KEY", "WARSAW_USERNAME"}:
                    os.environ.setdefault(key.strip(), value.strip())


async def main():
    import aiohttp
    from warsaw_transit_core.api import TransitClient
    from warsaw_transit_core.engine import TransitEngine
    from warsaw_transit_core.models import RouteConfig

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("config", type=Path, help="JSON file with exactly two route configurations")
    parser.add_argument("--sample-seconds", type=int, default=20, help="Second GPS sample interval, minimum 20")
    args = parser.parse_args()
    if args.sample_seconds < 20:
        parser.error("--sample-seconds must be at least 20")
    load_env()
    key = os.environ.get("WARSAW_API_KEY")
    if not key:
        parser.error("Set WARSAW_API_KEY in the environment or private .env file")
    raw = json.loads(args.config.read_text())
    if not isinstance(raw, list) or len(raw) != 2:
        parser.error("Configuration must be a JSON array containing exactly two routes")
    routes = [RouteConfig.model_validate(item) for item in raw]
    async with aiohttp.ClientSession() as session:
        engine = TransitEngine(TransitClient(session, key), routes)
        await engine.async_setup()
        await engine.async_refresh()
        await asyncio.sleep(args.sample_seconds)
        result = await engine.async_refresh()
        print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except (KeyboardInterrupt, SystemExit):
        raise
    except Exception as error:
        # Validation messages must not accidentally echo API keys.
        load_env()
        message = str(error)
        key = os.environ.get("WARSAW_API_KEY", "")
        if key:
            message = message.replace(key, "[REDACTED]")
        print(f"Live check failed: {message}", file=sys.stderr)
        sys.exit(1)
