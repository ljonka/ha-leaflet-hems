# Live / E2E checks

These scripts run **inside the Home Assistant container** and talk to the
running instance over `http://127.0.0.1:8123` and its WebSocket API.  They
contain no device serial numbers, IP addresses or hardcoded entity ids.

Copy the `tests/e2e` directory to `/config/e2e` on the HA host, then run:

```bash
# Configure the HA Energy Dashboard from the grouped Leaflet sensors
docker exec homeassistant python /config/e2e/setup_energy.py --title "<entry title>"

# Verify entities, devices, mDNS and system logs
docker exec homeassistant python /config/e2e/live_checks.py --title "<entry title>"

# TDD check for all electricity energy + power points, statistics and
# Energy Dashboard validation. The first run may need to wait for the
# recorder's 5-minute statistics cycle.
docker exec homeassistant python /config/e2e/energy_checks.py \
    --title "<entry title>" --validation-wait 360

# Print the current Energy preferences and statistics for debugging
docker exec homeassistant python /config/e2e/energy_audit.py --title "<entry title>"
```

`--title` can be omitted when exactly one `leaflet_hems` config entry exists.
All entity ids are resolved through the entity registry using the stable
unique-id suffixes (`grid_import_energy`, `generation_power`, ...).

## Expected sources

The Energy Dashboard is configured with these grouped sensors:

| Source | Energy | Power |
| --- | --- | --- |
| Grid | `grid_import_energy`, `grid_export_energy` | `grid_import_power`, `grid_export_power` |
| Solar | `generation_energy` | `generation_power` |
| Battery | `storage_energy_provided` (out), `storage_energy_stored` (in) | `storage_discharging_power`, `storage_charging_power` |
| Household | `consumption_energy` | `consumption_power` |

Household consumption is calculated from grid, generation and storage.
The HA Energy Dashboard calculates the same balance; the household sensors are
available for dashboards and automations.

## Mock nymea device (no real device required)

`tests/fake_nymea.py` contains a full in-process mock of the nymea JSON-RPC
API: TLS, token auth, Hello (including the id-0 quirk), filtered
things/classes, energy balance, live notifications with notification ids,
configuration changes and restart/reconnect support.

The unit tests use it directly. It can also be run as a standalone server:

```bash
python -m tests.fake_nymea \
    --host 0.0.0.0 --port 12222 --tls --simulate
```

It prints the mock device UUID, token, username and password. Add it to Home
Assistant with manual setup using the printed host/port and credentials.
`--simulate` changes PV and battery power every five seconds so the live
power view can be tested without a Leaflet. Use `--no-auth` for an
unauthenticated mock.

This is also the recommended way to test reconnect handling without
restarting a real nymead:

```bash
.venv/bin/pytest tests/test_mock_nymea.py -q
```

## Discovery lifecycle helpers

These helpers are useful for manual testing of mDNS discovery and add/remove:

- `plain_admin.py` — create/remove a temporary user via the device's plain
  `127.0.0.1:2223` interface (run on the Leaflet device).
- `complete_discovery.py` — complete a Zeroconf discovery flow for a UUID.
- `manage_entry.py` — list/remove a Leaflet config entry by title.
- `flow_progress.py` — print all in-progress HA config flows.
- `mdns_browse.py` — browse `_jsonrpc._tcp.local.` services.
- `plain_notifications.py` — listen to JSON-RPC notifications on the device's
  plain local interface.
- `tls_notify_probe.py` — verify that the integration's own TLS+token client
  receives live notifications (`JSONRPC.Hello` must use id `0`, and nymea
  notifications carry their own `id` field).
- `tls_raw_probe.py` — raw TLS probe for the Hello/SetNotificationStatus
  sequence.
- `tls_notification_variants.py` — compare `SetNotificationStatus` variants.
- `hacs_check.py` — check HACS update detection for the public repository
  (`--install` downloads the latest release). HACS must be active.
- `add_mock_entry.py` — add the standalone mock server through the manual
  config flow (host, port, username, password).
