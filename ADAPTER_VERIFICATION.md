# Adapter verification

Actual Home Assistant 2025.1.4, Python 3.12.16, Pydantic 2.10.6 in `.ha312-venv`. Adapter tests import real HA modules; upstream transport/engine calls and selected lifecycle service objects are mocked. No core files, credentials, or parent documentation were modified by the adapter worker.

Executed after reviewed fixes:

```
.ha312-venv/bin/python -m pytest tests/test_adapter*.py -q -o asyncio_default_fixture_loop_scope=function
# 19 passed, 9 third-party warnings
.ha312-venv/bin/python -m pytest tests -q -o asyncio_default_fixture_loop_scope=function
# 34 passed, 9 warnings, 8 subtests passed in 2.64s
```

The initial regression run failed all 11 cases before fixes, exposing stale 200 responses, null-schedule crashes, stale attributes, discarded stop suggestions, uncaught Pydantic errors, recreated clients, and repeated configuration setup.

Verified fixes:
- Cached `/api/warsaw_transit/{entry_id}/{route_name}/gps` and `/schedule` return 503 with `status: unavailable`, null current resource, `fresh: false`, and `last_success` when the last refresh failed. Successful cached responses include freshness metadata. Unknown/unloaded routes retain 404. Handlers never refresh upstream data.
- Real HA HTTP server registration and authentication middleware exercised over local sockets using a real HA auth manager and generated test access token: absent/invalid tokens return 401, valid token returns 200, failed refresh returns 503, unknown route returns 404. No authentication bypass mocks.
- Config flow catches Pydantic validation errors without rendering input/context/custom validator messages. Field paths and validation codes remain actionable. ConfigurationError preserves exact stop/post suggestions with API-key redaction. Both English string files use `{detail}` and every form supplies it. Error forms preserve route inputs but clear the API key.
- Flow retries retain the same client/cooldown for the same key. A real TransitClient with failed transport makes one request across repeated same-key submissions; changed key permits a new client/request.
- Coordinator latches ConfigurationError until reload/reconfigure, disables polling, and raises ConfigEntryError with exact sanitized details. Real HA refresh handling marks unavailable and retains the error. Three retries do not call setup again; a new coordinator can recover. Permanent configuration errors do not become retryable ConfigEntryNotReady failures.
- Sensors tolerate null schedules and suppress stale current values, vehicle/departure attributes, and healthy status after refresh failure; expose freshness and last-success metadata.

Tests: existing `tests/test_adapter.py`, `tests/test_adapter_coordinator.py`, `tests/test_adapter_lifecycle.py`; new `tests/test_adapter_regressions.py` (13 regression cases).

Warnings: seven existing pyOpenSSL/acme/HA application deprecations plus two HA/aiohttp-cors string AppKey warnings from the real HTTP server test. No live Warsaw requests, HACS installation, frontend clickthrough, deployment, or newer HA version testing performed. No repository `.git` is present, so no git diff/status verification or commits were possible.
