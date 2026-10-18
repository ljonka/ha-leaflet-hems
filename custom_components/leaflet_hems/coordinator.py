"""Event-driven data coordinator for the Leaflet HEMS integration.

The coordinator loads a compact snapshot after each (re)connect:

* the root meter thing id (``Energy.GetRootMeter``)
* the configured PV and battery thing ids (``Hems.GetPvConfigurations`` and
  ``Hems.GetBatteryConfigurations``)
* only the thing classes and state types for those things
  (``Integrations.GetThings`` with a ``thingId`` filter and
  ``Integrations.GetThingClasses`` with a ``thingClassIds`` filter)
* the current state values of those things
* the current energy balance (``Energy.GetPowerBalance``)

After that it only reacts to JSON-RPC notifications.  Heavy unfiltered
``GetThings`` / ``GetThingClasses`` calls are never made periodically.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from typing import Any

from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers.event import async_call_later
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator

from .client import (
    NymeaClient,
    NymeaConnectionError,
    NymeaResponseError,
    NymeaTimeoutError,
)
from .const import (
    DOMAIN,
    ENERGY_GET_POWER_BALANCE,
    ENERGY_GET_ROOT_METER,
    ENERGY_POWER_BALANCE_CHANGED,
    ENERGY_ROOT_METER_CHANGED,
    HEMS_BATTERY_CONFIG_EVENTS,
    HEMS_GET_BATTERY_CONFIGS,
    HEMS_GET_PV_CONFIGS,
    HEMS_PV_CONFIG_EVENTS,
    INTEGRATIONS_GET_STATE_TYPES,
    INTEGRATIONS_GET_STATE_VALUES,
    INTEGRATIONS_GET_THING_CLASSES,
    INTEGRATIONS_GET_THINGS,
    INTEGRATIONS_STATE_CHANGED,
    INTEGRATIONS_THING_ADDED,
    INTEGRATIONS_THING_CHANGED,
    INTEGRATIONS_THING_REMOVED,
    NAME,
    RELEVANT_STATE_NAMES,
    STATE_CURRENT_POWER,
    STATE_TOTAL_ENERGY_CONSUMED,
    STATE_TOTAL_ENERGY_PRODUCED,
)
from .util import clean_params, to_float

_LOGGER = logging.getLogger(__name__)

# Notifications can arrive in bursts (one per thing state).  Coalesce them
# into one Home Assistant state update to keep the recorder and event bus
# quiet.
UPDATE_DEBOUNCE = 0.5

# A filtered snapshot is small.  Keep concurrency low so a Raspberry Pi
# based nymea device is never overwhelmed.
SNAPSHOT_CONCURRENCY = 3


class LeafletHEMSCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinate event-driven Leaflet HEMS data."""

    def __init__(
        self,
        hass: HomeAssistant,
        client: NymeaClient,
        nymea_uuid: str,
        nymea_name: str,
    ) -> None:
        super().__init__(
            hass,
            _LOGGER,
            name=f"{NAME} {nymea_name}",
            update_interval=None,
        )
        self.client = client
        self.nymea_uuid = nymea_uuid
        self.nymea_name = nymea_name

        self.data = {}

        self._things: dict[str, dict[str, Any]] = {}
        self._thing_classes: dict[str, dict[str, Any]] = {}
        self._state_types: dict[str, dict[str, str]] = {}
        self._states: dict[str, dict[str, Any]] = {}
        self._power_balance: dict[str, float] = {}

        self._root_meter_id: str | None = None
        self._pv_ids: set[str] = set()
        self._battery_ids: set[str] = set()

        self._connected = False
        self._connected_event = asyncio.Event()
        self._first_snapshot_event = asyncio.Event()
        self._update_handle: Any = None
        self._refresh_tasks: set[str] = set()
        self._notification_tokens: list[str] = []
        self._snapshot_lock = asyncio.Lock()
        self._snapshot_semaphore = asyncio.Semaphore(SNAPSHOT_CONCURRENCY)
        self._disconnect_logged = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------
    async def async_start(self, first_snapshot_timeout: float = 10.0) -> None:
        """Register callbacks and start the persistent client supervisor."""

        callback_token = self.client.register_notification_callback(
            self._handle_notification
        )
        self._notification_tokens.append(callback_token)
        self.client.register_connected_callback(self._async_on_connected)
        self.client.register_disconnected_callback(self._async_on_disconnected)
        await self.client.async_start()
        with_snapshot = await self.async_wait_first_snapshot(first_snapshot_timeout)
        if not with_snapshot:
            _LOGGER.warning(
                "No initial Leaflet HEMS snapshot received from %s within %.0fs; "
                "entities will update as soon as the device is reachable",
                self.nymea_name,
                first_snapshot_timeout,
            )

    async def async_stop(self) -> None:
        """Stop the client and unregister callbacks."""

        self.client.unregister_connected_callback(self._async_on_connected)
        self.client.unregister_disconnected_callback(self._async_on_disconnected)
        for token in self._notification_tokens:
            self.client.unregister_notification_callback(token)
        self._notification_tokens.clear()
        self._cancel_update_handle()
        await self.client.async_stop()

    async def async_wait_first_snapshot(self, timeout: float | None = None) -> bool:
        """Wait until the first snapshot has been published."""

        if self.data:
            return True
        try:
            await asyncio.wait_for(self._first_snapshot_event.wait(), timeout=timeout)
        except asyncio.TimeoutError:
            return False
        return True

    async def _async_update_data(self) -> dict[str, Any]:
        """Return the current cache.

        The coordinator is event driven (``update_interval=None``); this
        method deliberately performs no I/O.
        """

        return dict(self.data or {})

    async def _async_on_connected(
        self, client: NymeaClient, error: Exception | None = None
    ) -> None:
        """Rebuild the compact snapshot after every (re)connect."""

        self._connected = True
        _LOGGER.info("Leaflet HEMS connected; loading startup snapshot")
        try:
            await self._async_snapshot()
        except NymeaConnectionError:
            raise
        except Exception as exc:  # noqa: BLE001
            _LOGGER.warning("Failed to load Leaflet HEMS startup snapshot: %s", exc)
            raise
        self._disconnect_logged = False
        self._connected_event.set()
        self._first_snapshot_event.set()
        _LOGGER.info(
            "Leaflet HEMS snapshot loaded: root meter=%s, PV=%d, batteries=%d, "
            "data points=%d",
            "yes" if self._root_meter_id else "no",
            len(self._pv_ids),
            len(self._battery_ids),
            len(self.data or {}),
        )

    async def _async_on_disconnected(
        self, client: NymeaClient, error: Exception | None = None
    ) -> None:
        """Mark all entities unavailable and clear transient state."""

        self._connected = False
        self._connected_event.clear()
        self._cancel_update_handle()
        self._states.clear()
        self._power_balance.clear()
        self.data = {}
        if not self._disconnect_logged:
            _LOGGER.warning(
                "Leaflet HEMS connection lost (%s); entities are unavailable until reconnect",
                error or "disconnected",
            )
            self._disconnect_logged = True
        else:
            _LOGGER.debug("Leaflet HEMS still disconnected: %s", error)
        self.last_update_success = False
        self.last_exception = None
        self.async_update_listeners()

    # ------------------------------------------------------------------
    # Snapshot loading
    # ------------------------------------------------------------------
    async def _async_snapshot(self) -> None:
        """Load the compact startup/reconnect snapshot."""

        async with self._snapshot_lock:
            self._states.clear()
            self._power_balance.clear()
            self._things.clear()
            self._thing_classes.clear()
            self._state_types.clear()
            self._root_meter_id = None
            self._pv_ids.clear()
            self._battery_ids.clear()

            await self._async_load_root_meter()
            await self._async_load_configs()

            thing_ids = set(self._pv_ids) | set(self._battery_ids)
            if self._root_meter_id:
                thing_ids.add(self._root_meter_id)

            await self._async_load_things(thing_ids)
            await self._async_load_power_balance()
            self._publish(force=True)

    async def _async_load_root_meter(self) -> None:
        params = await self._async_request(ENERGY_GET_ROOT_METER)
        if params is None:
            return
        root_meter_id = params.get("rootMeterThingId")
        if root_meter_id:
            self._root_meter_id = str(root_meter_id)
            _LOGGER.debug("Root meter thing id: %s", self._root_meter_id)

    async def _async_load_configs(self) -> None:
        pv_params = await self._async_request(HEMS_GET_PV_CONFIGS)
        if pv_params is not None:
            for config in pv_params.get("pvConfigurations") or []:
                thing_id = config.get("pvThingId")
                if thing_id:
                    self._pv_ids.add(str(thing_id))

        battery_params = await self._async_request(HEMS_GET_BATTERY_CONFIGS)
        if battery_params is not None:
            for config in battery_params.get("batteryConfigurations") or []:
                thing_id = config.get("batteryThingId")
                if thing_id:
                    self._battery_ids.add(str(thing_id))

    async def _async_load_things(self, thing_ids: set[str]) -> None:
        if not thing_ids:
            return

        async def load_one(thing_id: str) -> None:
            async with self._snapshot_semaphore:
                await self._async_ensure_thing(thing_id)

        ordered_ids = list(thing_ids)
        results = await asyncio.gather(
            *(load_one(thing_id) for thing_id in ordered_ids),
            return_exceptions=True,
        )
        for thing_id, result in zip(ordered_ids, results, strict=False):
            if isinstance(result, NymeaConnectionError):
                raise result
            if isinstance(result, Exception):
                _LOGGER.warning(
                    "Could not load Leaflet HEMS thing %s: %s", thing_id, result
                )

    async def _async_load_power_balance(self) -> None:
        params = await self._async_request(ENERGY_GET_POWER_BALANCE)
        if params is not None:
            self._power_balance = self._numeric_params(params)

    # ------------------------------------------------------------------
    # Thing / class / state loading
    # ------------------------------------------------------------------
    async def _async_ensure_thing(self, thing_id: str) -> None:
        """Fetch one thing plus only the classes/state types it needs."""

        params = await self._async_request(
            INTEGRATIONS_GET_THINGS, {"thingId": thing_id}
        )
        if params is None:
            return

        things = params.get("things") or []
        thing = next(
            (item for item in things if str(item.get("id")) == thing_id), None
        )
        if thing is None:
            _LOGGER.debug("Thing %s is no longer present", thing_id)
            self._things.pop(thing_id, None)
            self._states.pop(thing_id, None)
            return

        class_id = str(thing.get("thingClassId") or "")
        self._things[thing_id] = {
            "id": thing_id,
            "name": thing.get("name"),
            "class_id": class_id,
        }

        if class_id:
            await self._async_ensure_thing_class(class_id)

        await self._async_fetch_state_values(thing_id)

    async def _async_ensure_thing_class(self, class_id: str) -> None:
        """Fetch a filtered thing class and its state types once."""

        if class_id in self._state_types:
            return

        class_params = await self._async_request(
            INTEGRATIONS_GET_THING_CLASSES, {"thingClassIds": [class_id]}
        )
        if class_params is not None:
            for thing_class in class_params.get("thingClasses") or []:
                if str(thing_class.get("id")) == class_id:
                    self._thing_classes[class_id] = thing_class
                    break

        state_type_params = await self._async_request(
            INTEGRATIONS_GET_STATE_TYPES, {"thingClassId": class_id}
        )
        if state_type_params is None:
            return

        state_types: dict[str, str] = {}
        for state_type in state_type_params.get("stateTypes") or []:
            state_type_id = state_type.get("id") or state_type.get("r:id")
            state_name = state_type.get("name")
            if state_type_id and state_name:
                state_types[str(state_type_id)] = str(state_name)
        self._state_types[class_id] = state_types

    async def _async_fetch_state_values(self, thing_id: str) -> None:
        params = await self._async_request(
            INTEGRATIONS_GET_STATE_VALUES, {"thingId": thing_id}
        )
        if params is None:
            return

        thing = self._things.get(thing_id)
        if thing is None:
            return
        state_type_names = self._state_types.get(thing["class_id"], {})
        states: dict[str, Any] = {}
        for item in params.get("values") or []:
            state_type_id = str(item.get("stateTypeId") or "")
            state_name = state_type_names.get(state_type_id)
            if state_name in RELEVANT_STATE_NAMES:
                states[state_name] = item.get("value")
        self._states[thing_id] = states

    # ------------------------------------------------------------------
    # Notifications
    # ------------------------------------------------------------------
    @callback
    def _handle_notification(self, notification: dict[str, Any]) -> None:
        """Handle all event types relevant to the grouped entities."""

        method = notification.get("method") or notification.get("notification")
        params = notification.get("params") or {}
        if not method:
            return

        if method == ENERGY_POWER_BALANCE_CHANGED:
            self._power_balance.update(self._numeric_params(params))
            self._schedule_update()
            return

        if method == INTEGRATIONS_STATE_CHANGED:
            self._handle_state_changed(params)
            return

        if method == ENERGY_ROOT_METER_CHANGED:
            self._schedule_structural_refresh("root_meter", self._async_refresh_root_meter)
            return

        if method in HEMS_PV_CONFIG_EVENTS:
            self._schedule_structural_refresh("pv_configs", self._async_refresh_pv_configs)
            return

        if method in HEMS_BATTERY_CONFIG_EVENTS:
            self._schedule_structural_refresh(
                "battery_configs", self._async_refresh_battery_configs
            )
            return

        if method == INTEGRATIONS_THING_CHANGED:
            thing_id = params.get("thingId") or (params.get("thing") or {}).get("id")
            if thing_id and str(thing_id) in self._things:
                self._schedule_structural_refresh(
                    f"thing:{thing_id}",
                    lambda: self._async_refresh_thing(str(thing_id)),
                )
            return

        if method == INTEGRATIONS_THING_REMOVED:
            thing_id = params.get("thingId") or (params.get("thing") or {}).get("id")
            if thing_id:
                thing_id = str(thing_id)
                self._pv_ids.discard(thing_id)
                self._battery_ids.discard(thing_id)
                self._remove_thing(thing_id)
            return

        if method == INTEGRATIONS_THING_ADDED:
            # New PV/battery things are picked up by the Hems configuration
            # notifications.  Ignore unrelated things.
            return

    @callback
    def _handle_state_changed(self, params: dict[str, Any]) -> None:
        thing_id = params.get("thingId")
        state_type_id = params.get("stateTypeId")
        if not thing_id or not state_type_id:
            return

        thing_id = str(thing_id)
        thing = self._things.get(thing_id)
        if thing is None:
            return

        state_name = self._state_types.get(thing["class_id"], {}).get(
            str(state_type_id)
        )
        if state_name not in RELEVANT_STATE_NAMES:
            return

        value = params.get("value")
        if self._states.setdefault(thing_id, {}).get(state_name) == value:
            return
        self._states[thing_id][state_name] = value
        self._schedule_update()

    async def _async_refresh_root_meter(self) -> None:
        params = await self._async_request(ENERGY_GET_ROOT_METER)
        if params is None:
            return
        new_root = params.get("rootMeterThingId")
        new_root_id = str(new_root) if new_root else None
        if new_root_id == self._root_meter_id:
            return
        if self._root_meter_id:
            self._things.pop(self._root_meter_id, None)
            self._states.pop(self._root_meter_id, None)
        self._root_meter_id = new_root_id
        if new_root_id:
            await self._async_ensure_thing(new_root_id)
        self._schedule_update()

    async def _async_refresh_pv_configs(self) -> None:
        params = await self._async_request(HEMS_GET_PV_CONFIGS)
        if params is None:
            return
        new_ids = {
            str(config["pvThingId"])
            for config in params.get("pvConfigurations") or []
            if config.get("pvThingId")
        }
        added = new_ids - self._pv_ids
        removed = self._pv_ids - new_ids
        self._pv_ids = new_ids
        for thing_id in removed:
            if thing_id not in self._battery_ids:
                self._remove_thing(thing_id)
        for thing_id in added:
            await self._async_ensure_thing(thing_id)
        self._schedule_update()

    async def _async_refresh_battery_configs(self) -> None:
        params = await self._async_request(HEMS_GET_BATTERY_CONFIGS)
        if params is None:
            return
        new_ids = {
            str(config["batteryThingId"])
            for config in params.get("batteryConfigurations") or []
            if config.get("batteryThingId")
        }
        added = new_ids - self._battery_ids
        removed = self._battery_ids - new_ids
        self._battery_ids = new_ids
        for thing_id in removed:
            if thing_id not in self._pv_ids:
                self._remove_thing(thing_id)
        for thing_id in added:
            await self._async_ensure_thing(thing_id)
        self._schedule_update()

    async def _async_refresh_thing(self, thing_id: str) -> None:
        await self._async_ensure_thing(thing_id)
        self._schedule_update()

    def _remove_thing(self, thing_id: str) -> None:
        self._things.pop(thing_id, None)
        self._states.pop(thing_id, None)
        if thing_id == self._root_meter_id:
            self._root_meter_id = None
        self._schedule_update()

    # ------------------------------------------------------------------
    # Data publication
    # ------------------------------------------------------------------
    def _schedule_structural_refresh(self, key: str, func: Any) -> None:
        """Run a structural refresh once at a time per key."""

        if key in self._refresh_tasks:
            return
        self._refresh_tasks.add(key)

        async def runner() -> None:
            try:
                await func()
            except NymeaConnectionError:
                pass
            except Exception:  # noqa: BLE001
                _LOGGER.exception("Failed to refresh Leaflet HEMS data (%s)", key)
            finally:
                self._refresh_tasks.discard(key)

        self.hass.async_create_task(runner())

    @callback
    def _schedule_update(self) -> None:
        if self._update_handle is not None:
            return
        self._update_handle = async_call_later(
            self.hass, UPDATE_DEBOUNCE, self._flush_update
        )

    @callback
    def _flush_update(self, _now: Any = None) -> None:
        self._update_handle = None
        if not self._connected:
            return
        self._publish()

    def _cancel_update_handle(self) -> None:
        if self._update_handle is not None:
            with contextlib.suppress(Exception):
                self._update_handle()
            self._update_handle = None

    def _publish(self, *, force: bool = False) -> None:
        data = self._build_data()
        if data == self.data and not force:
            return
        _LOGGER.debug("Publishing %d Leaflet HEMS data points", len(data))
        self.async_set_updated_data(data)

    def _build_data(self) -> dict[str, Any]:
        root_id = self._root_meter_id
        pb = self._power_balance

        root_power = self._state(root_id, STATE_CURRENT_POWER)
        root_import_energy = self._state(root_id, STATE_TOTAL_ENERGY_CONSUMED)
        root_export_energy = self._state(root_id, STATE_TOTAL_ENERGY_PRODUCED)

        grid_power = to_float(root_power)
        if grid_power is None:
            grid_power = to_float(pb.get("currentPowerAcquisition"))

        grid_import_energy = to_float(root_import_energy)
        if grid_import_energy is None:
            # Power balance grid totals are reported in Wh by nymea.
            acquisition_wh = to_float(pb.get("totalAcquisition"))
            if acquisition_wh is not None:
                grid_import_energy = acquisition_wh / 1000.0

        grid_export_energy = to_float(root_export_energy)
        if grid_export_energy is None:
            return_wh = to_float(pb.get("totalReturn"))
            if return_wh is not None:
                grid_export_energy = return_wh / 1000.0

        pv_power_values = self._state_values(self._pv_ids, STATE_CURRENT_POWER)
        pv_energy_values = self._state_values(
            self._pv_ids, STATE_TOTAL_ENERGY_PRODUCED
        )
        if pv_power_values:
            generation_power = max(0.0, -sum(pv_power_values))
        else:
            production = to_float(pb.get("currentPowerProduction"))
            generation_power = (
                max(0.0, -production) if production is not None else None
            )

        if pv_energy_values:
            generation_energy = sum(pv_energy_values)
        else:
            generation_energy = to_float(pb.get("totalProduction"))

        battery_power_values = self._state_values(
            self._battery_ids, STATE_CURRENT_POWER
        )
        if battery_power_values:
            storage_power = sum(battery_power_values)
        else:
            storage_power = to_float(pb.get("currentPowerStorage"))

        battery_stored_values = self._state_values(
            self._battery_ids, STATE_TOTAL_ENERGY_CONSUMED
        )
        battery_provided_values = self._state_values(
            self._battery_ids, STATE_TOTAL_ENERGY_PRODUCED
        )
        storage_energy_stored = (
            sum(battery_stored_values) if battery_stored_values else None
        )
        storage_energy_provided = (
            sum(battery_provided_values) if battery_provided_values else None
        )

        consumption_power = None
        if grid_power is not None and generation_power is not None:
            charging = max(0.0, storage_power or 0.0)
            discharging = max(0.0, -(storage_power or 0.0))
            consumption_power = (
                grid_power + generation_power + discharging - charging
            )
        if consumption_power is None:
            consumption_power = to_float(pb.get("currentPowerConsumption"))

        consumption_energy = None
        if None not in (
            grid_import_energy,
            grid_export_energy,
            generation_energy,
        ):
            consumption_energy = (
                grid_import_energy
                + generation_energy
                + (storage_energy_provided or 0.0)
                - (storage_energy_stored or 0.0)
                - grid_export_energy
            )
            # Inconsistent device counters must not make a total_increasing
            # sensor go negative.
            consumption_energy = max(0.0, consumption_energy)

        data: dict[str, Any] = {
            "grid_power": grid_power,
            "grid_import_power": (
                max(0.0, grid_power) if grid_power is not None else None
            ),
            "grid_export_power": (
                max(0.0, -grid_power) if grid_power is not None else None
            ),
            "grid_import_energy": grid_import_energy,
            "grid_export_energy": grid_export_energy,
            "generation_power": generation_power,
            "generation_energy": generation_energy,
            "storage_power": storage_power,
            "storage_charging_power": (
                max(0.0, storage_power) if storage_power is not None else None
            ),
            "storage_discharging_power": (
                max(0.0, -storage_power) if storage_power is not None else None
            ),
            "storage_energy_stored": storage_energy_stored,
            "storage_energy_provided": storage_energy_provided,
            "consumption_power": consumption_power,
            "consumption_energy": consumption_energy,
        }
        rounded: dict[str, Any] = {}
        for key, value in data.items():
            if value is None:
                continue
            if "energy" in key:
                rounded[key] = round(value, 3)
            elif "power" in key:
                rounded[key] = round(value, 1)
            else:
                rounded[key] = value
        return rounded

    def _state(self, thing_id: str | None, state_name: str) -> Any:
        if not thing_id:
            return None
        return (self._states.get(thing_id) or {}).get(state_name)

    def _state_values(self, thing_ids: set[str], state_name: str) -> list[float]:
        values: list[float] = []
        for thing_id in thing_ids:
            value = to_float(self._state(thing_id, state_name))
            if value is not None:
                values.append(value)
        return values

    @staticmethod
    def _numeric_params(params: dict[str, Any]) -> dict[str, float]:
        numeric: dict[str, float] = {}
        for key, value in clean_params(params).items():
            number = to_float(value)
            if number is not None:
                numeric[key] = number
        return numeric

    async def _async_request(
        self, method: str, params: dict[str, Any] | None = None
    ) -> dict[str, Any] | None:
        try:
            response = await self.client.request(method, params)
        except NymeaConnectionError:
            raise
        except (NymeaTimeoutError, NymeaResponseError) as exc:
            _LOGGER.warning("Leaflet HEMS request %s failed: %s", method, exc)
            return None
        return clean_params(response.get("params") or {})
