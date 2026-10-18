# Leaflet HEMS Integration for Home Assistant

![Consolinno Energy logo](custom_components/leaflet_hems/logo.png)

Consolinno Energy GmbH — Integration "Leaflet HEMS"

This integration connects Home Assistant to Consolinno Leaflet HEMS
(Heim Energie Management System) devices, which are based on the nymea
framework. It exposes the electricity data needed by the Home Assistant
Energy Dashboard and keeps a persistent, event-driven connection to the
device.

[![Open your Home Assistant instance and open a repository inside the Home Assistant Community Store.](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=ljonka&repository=ha-leaflet-hems&category=integration)

## Features

- **Automatic Discovery**: mDNS/Zeroconf discovery via
  `_jsonrpc._tcp.local.` including device name, UUID and server version.
- **Secure persistent connection**: TLS + token authentication on port
  `2222`, using the nymea JSON-RPC protocol.
- **Event-driven updates**: the integration subscribes to
  `Energy.PowerBalanceChanged`, `Integrations.StateChanged`,
  `Energy.RootMeterChanged` and the `Hems.*Configuration*` notifications.
  Normal operation does not poll.
- **Compact startup snapshot**: only the configured root meter, PV and
  battery things are loaded, and thing classes are requested with a
  `thingClassIds` filter. Heavy unfiltered `GetThings` / `GetThingClasses`
  calls are never repeated periodically.
- **Reconnect handling**: exponential backoff with jitter, automatic
  re-authentication, notification re-subscription and a fresh snapshot after
  every reconnect. A token rejection starts the Home Assistant reauth flow.
- **Grouped entities**: four devices instead of one device per nymea thing.
- **Energy Dashboard ready**: grid import/export, PV generation and battery
  charge/discharge energy sensors with `device_class=energy`,
  `state_class=total_increasing` and `kWh`.

## Installation

### Using HACS (Recommended)

1. Open HACS in your Home Assistant instance.
2. Go to "Integrations", click the three-dot menu and add this repository as a
   custom repository if it is not listed yet:
   `https://github.com/ljonka/ha-leaflet-hems`.
3. Search for "Leaflet HEMS", download it and restart Home Assistant.

### Manual Installation

Copy `custom_components/leaflet_hems` into the `custom_components` directory
of your Home Assistant configuration and restart Home Assistant.

## Configuration

### Automatic Discovery

1. Go to "Settings" > "Devices & Services" > "Add Integration".
2. Search for "Leaflet HEMS". Discovered Leaflet devices are shown with their
   name, hostname and UUID.
3. Select the device, confirm it and enter the device username/password when
   authentication is required.

The credentials are only used once to obtain a device token. The password is
not stored.

### Manual Setup

If mDNS is not available, choose "Leaflet HEMS" and enter the device IP
address and port (default `2222`), then the username/password if required.

### Reauthentication

If the stored token is revoked, Home Assistant shows a reauth notification.
Enter the device credentials again; the new token is stored transparently.

## Entities

The integration creates one base device and four grouped devices:

| Group | Sensor | Unit | HA class |
| --- | --- | --- | --- |
| Grid Connection Point | Grid Import Power | W | power / measurement |
| | Grid Export Power | W | power / measurement |
| | Grid Import Energy | kWh | energy / total_increasing |
| | Grid Export Energy | kWh | energy / total_increasing |
| Generators | Generation Power | W | power / measurement |
| | Generation Energy | kWh | energy / total_increasing |
| Storage | Storage Power (+ charging, - discharging) | W | power / measurement |
| | Storage Charging Power | W | power / measurement |
| | Storage Discharging Power | W | power / measurement |
| | Storage Energy Stored | kWh | energy / total_increasing |
| | Storage Energy Provided | kWh | energy / total_increasing |
| Household Consumption | Household Consumption Power | W | power / measurement |
| | Household Consumption Energy | kWh | energy / total_increasing |

There are intentionally **no per-thing battery/inverter entities**. This
keeps the entity count stable even when devices are added or replaced.

Household consumption is calculated by the Leaflet energy balance from the
grid connection point, generators and storage. The integration exposes the
calculated power directly and computes the energy counter as:

```
consumption_energy =
    grid_import_energy + generation_energy
    + storage_energy_provided - storage_energy_stored
    - grid_export_energy
```

The grid energy counters come from the root meter state values in kWh; the
generation counter is the sum of the configured PV inverter counters; the
storage counters are the sums of the configured battery counters.

## Home Assistant Energy Dashboard

Configure the Energy Dashboard under "Settings" > "Dashboards" > "Energy":

| Dashboard field | Sensor |
| --- | --- |
| Grid consumption | `Grid Import Energy` |
| Return to grid | `Grid Export Energy` |
| Solar production | `Generation Energy` |
| Battery energy in | `Storage Energy Stored` |
| Battery energy out | `Storage Energy Provided` |

The grouped power sensors are used automatically for the live flow display.
The integration also provides a script for automated setups; see
[`tests/e2e/README.md`](tests/e2e/README.md):

```bash
docker exec homeassistant python /config/e2e/setup_energy.py --title "<entry title>"
docker exec homeassistant python /config/e2e/energy_checks.py --title "<entry title>" --validation-wait 360
```

`energy_checks.py` verifies all electricity energy and power points, the
long-term statistics, plausible values (a single-family house with 8 kWp
cannot accumulate GWh), and the Energy Dashboard validation result.

## Data sources and units

The integration uses the nymea JSON-RPC API:

- `Energy.GetPowerBalance` / `Energy.PowerBalanceChanged` for live power and
  household consumption.
- `Energy.GetRootMeter` to identify the grid connection meter.
- `Integrations.GetStateValues` / `Integrations.StateChanged` for the root
  meter, PV and battery state values.
- `Hems.GetPvConfigurations`, `Hems.GetBatteryConfigurations` and the
  corresponding configuration notifications to track generators and storage.

Power values use **Watt**. Energy counters use **kWh**. The grid and PV
counters are read from the corresponding nymea thing states; the legacy
`Energy.GetPowerBalance` totals are not used for grid energy because older
firmware versions can report them with an inconsistent Wh/kWh base.

## Development

### Unit tests

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pytest tests -q
```

The unit tests cover the persistent client, reconnect/backoff, keepalive,
notification buffering, filtered snapshots, grouped data mapping, energy
scaling/rounding, the sensor platform, the config flow and Zeroconf
de-duplication.

### Live tests against Home Assistant

The scripts in [`tests/e2e`](tests/e2e) run inside the Home Assistant
container and use its WebSocket/REST API. They resolve config entries and
entity ids dynamically and contain no device-specific identifiers. See
[`tests/e2e/README.md`](tests/e2e/README.md).

## Troubleshooting

- **Device not discovered**: mDNS must work between Home Assistant and the
  Leaflet. Use manual setup with the device IP as a fallback.
- **Cannot connect**: port `2222` must be reachable and `nymead` must be
  running. The integration does not fall back to the unauthenticated
  localhost interface.
- **Sensors unavailable**: the connection is down or the token was revoked.
  Check the Home Assistant logs and complete the reauth flow if prompted.
- **Energy Dashboard shows no data**: make sure the six energy sensors above
  are selected. New sensors need one recorder statistics cycle (about five
  minutes) before the dashboard can show them.

## Requirements

- Home Assistant 2025.10.2 or newer.
- A Consolinno Leaflet HEMS device with nymea and the Energy/Hems experience
  plugins.
- Network access to the device on port `2222` (TCP/TLS).

## License

MIT License — see [LICENSE](LICENSE).

---

**Disclaimer:** This is an unofficial integration. Use at your own risk. The
author is not responsible for any damage caused by the use of this software.
