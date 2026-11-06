# Adapter/core contract

Adapter imports `TransitClient, AuthError, TransitError, ConfigurationError, CooldownError` from api.py, `TransitEngine` from engine.py, and `RouteConfig` from models.py.

- `TransitClient(aiohttp_session, api_key)` uses the HA-owned session. Client owns cooldown/rate limiting; adapter refreshes every 20 seconds and never polls from HTTP handlers.
- `RouteConfig(name, start_stop, end_stop, start_post=None, end_post=None)` accepts named keyword arguments. Exactly two routes are configured.
- `TransitEngine(client, routes)` exposes `await async_setup()` (stop resolution / initial preparation), and `await async_refresh()` returning `{route_name: {"schedule": [...], "gps": {...} | None, "status": str, "error": str | None}}`.
- Schedule items expose `departure` as a timezone-aware ISO 8601 string, plus line/brigade/headsign metadata. GPS dict exposes `vehicle_id`, `line`, `brigade`, `latitude`, `longitude`, `distance_km`, `gps_updated` as ISO 8601, and optional other metadata. The engine result is JSON-serializable.
- `AuthError` means invalid/expired key and is translated to HA reauthentication. `TransitError` means actionable setup/update failure. Exceptions must never embed API keys or authenticated URLs.
- Core caches schedule and honors per-resource cooldown. HTTP views only read coordinator.data.
- Config entry stores `api_key` plus `routes` list of two plain dictionaries with the RouteConfig fields above. Route names must be distinct URL-safe slugs.
