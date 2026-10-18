"""Tests for the Leaflet HEMS config flow and Zeroconf discovery."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

import pytest
from homeassistant import config_entries
from homeassistant.data_entry_flow import FlowResultType
from pytest_homeassistant_custom_component.common import MockConfigEntry

from custom_components.leaflet_hems import config_flow
from custom_components.leaflet_hems.client import (
    NymeaAuthenticationError,
    NymeaConnectionError,
)
from custom_components.leaflet_hems.const import (
    CONF_NYMEA_NAME,
    CONF_NYMEA_TOKEN,
    CONF_NYMEA_UUID,
    DOMAIN,
)

UUID = "12345678-1234-1234-1234-123456789abc"
HELLO = {
    "uuid": f"{{{UUID}}}",
    "name": "Test Leaflet",
    "version": "1.7.26",
    "authenticationRequired": False,
    "authenticated": True,
}

DISCOVERY = SimpleNamespace(
    host="192.168.1.10",
    port=2222,
    hostname="leaflet.local.",
    name="nymea-tcp-default._jsonrpc._tcp.local.",
    properties={
        "uuid": f"{{{UUID}}}".encode(),
        "name": b"Test Leaflet",
        "manufacturer": b"nymea GmbH",
        "serverVersion": b"1.7.26",
    },
)


@pytest.mark.asyncio
async def test_user_step_shows_manual(hass):
    result = await hass.config_entries.flow.async_init(
        DOMAIN, context={"source": config_entries.SOURCE_USER}
    )
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "manual"


@pytest.mark.asyncio
async def test_manual_setup_success(hass):
    with patch.object(
        config_flow,
        "_async_probe",
        new=AsyncMock(return_value=(HELLO, "token-123")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.1.10", "port": 2222, "name": "My HEMS"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["title"] == "My HEMS"
    assert result["data"][CONF_NYMEA_UUID] == UUID
    assert result["data"][CONF_NYMEA_TOKEN] == "token-123"


@pytest.mark.asyncio
async def test_manual_setup_requires_auth(hass):
    responses = [
        NymeaAuthenticationError("credentials required"),
        (HELLO, "token-123"),
    ]
    with patch.object(
        config_flow, "_async_probe", new=AsyncMock(side_effect=responses)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.1.10", "port": 2222},
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "auth"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user", "password": "secret"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NYMEA_TOKEN] == "token-123"


@pytest.mark.asyncio
async def test_manual_setup_cannot_connect(hass):
    with patch.object(
        config_flow,
        "_async_probe",
        new=AsyncMock(side_effect=NymeaConnectionError("nope")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN, context={"source": config_entries.SOURCE_USER}
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"host": "192.168.1.10", "port": 2222},
        )

    assert result["type"] == FlowResultType.FORM
    assert result["errors"]["base"] == "cannot_connect"


@pytest.mark.asyncio
async def test_zeroconf_existing_entry_aborts_without_probe(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=f"{{{UUID}}}",
        data={
            "host": "192.168.1.10",
            "port": 2222,
            CONF_NYMEA_UUID: f"{{{UUID}}}",
            CONF_NYMEA_NAME: "Test Leaflet",
        },
    )
    entry.add_to_hass(hass)

    with patch.object(
        config_flow, "_async_hello", new=AsyncMock(return_value=HELLO)
    ) as hello:
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=DISCOVERY,
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "already_configured"
    hello.assert_not_called()
    assert entry.unique_id == UUID


@pytest.mark.asyncio
async def test_zeroconf_new_device_confirm(hass):
    with patch.object(
        config_flow, "_async_hello", new=AsyncMock(return_value=HELLO)
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=DISCOVERY,
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "discovery_confirm"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NYMEA_UUID] == UUID


@pytest.mark.asyncio
async def test_zeroconf_auth_required(hass):
    hello = {**HELLO, "authenticationRequired": True, "authenticated": False}
    with patch.object(
        config_flow, "_async_hello", new=AsyncMock(return_value=hello)
    ), patch.object(
        config_flow,
        "_async_probe",
        new=AsyncMock(return_value=(HELLO, "token-123")),
    ):
        result = await hass.config_entries.flow.async_init(
            DOMAIN,
            context={"source": config_entries.SOURCE_ZEROCONF},
            data=DISCOVERY,
        )
        result = await hass.config_entries.flow.async_configure(
            result["flow_id"], {}
        )
        assert result["type"] == FlowResultType.FORM
        assert result["step_id"] == "auth"

        result = await hass.config_entries.flow.async_configure(
            result["flow_id"],
            {"username": "user", "password": "secret"},
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NYMEA_TOKEN] == "token-123"


@pytest.mark.asyncio
async def test_zeroconf_parse_handles_bytes_and_braces(hass):
    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass
    parsed = flow._parse_discovery_info(DISCOVERY)

    assert parsed is not None
    assert parsed[CONF_NYMEA_UUID] == UUID
    assert parsed[CONF_NYMEA_NAME] == "Test Leaflet"
    assert parsed["hostname"] == "leaflet"


@pytest.mark.asyncio
async def test_zeroconf_rejects_other_manufacturer(hass):
    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass
    discovery = SimpleNamespace(
        host="192.168.1.10",
        port=2222,
        hostname="other.local.",
        properties={
            "uuid": UUID,
            "name": "Other",
            "manufacturer": "Other Vendor",
        },
    )
    assert flow._parse_discovery_info(discovery) is None


@pytest.mark.asyncio
async def test_reauthentication_updates_token(hass):
    entry = MockConfigEntry(
        domain=DOMAIN,
        unique_id=UUID,
        data={
            "host": "192.168.1.10",
            "port": 2222,
            CONF_NYMEA_UUID: UUID,
            CONF_NYMEA_NAME: "Test Leaflet",
            CONF_NYMEA_TOKEN: "old-token",
        },
    )
    entry.add_to_hass(hass)

    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass
    flow.context = {"entry_id": entry.entry_id, "source": "reauth"}

    result = await flow.async_step_reauth(entry.data)
    assert result["type"] == FlowResultType.FORM
    assert result["step_id"] == "reauth_confirm"

    with patch.object(
        config_flow,
        "_async_probe",
        new=AsyncMock(return_value=(HELLO, "new-token")),
    ):
        result = await flow.async_step_reauth_confirm(
            {"username": "user", "password": "secret"}
        )

    assert result["type"] == FlowResultType.ABORT
    assert result["reason"] == "reauth_successful"
    assert entry.data[CONF_NYMEA_TOKEN] == "new-token"


@pytest.mark.asyncio
async def test_import_with_existing_token(hass):
    flow = config_flow.LeafletHEMSFlowHandler()
    flow.hass = hass
    flow.context = {"source": "import"}
    with patch.object(
        config_flow,
        "_async_probe",
        new=AsyncMock(return_value=(HELLO, "token-123")),
    ):
        result = await flow.async_step_import(
            {
                "host": "192.168.1.10",
                "port": 2222,
                "nymea_uuid": f"{{{UUID}}}",
                "nymea_token": "token-123",
            }
        )

    assert result["type"] == FlowResultType.CREATE_ENTRY
    assert result["data"][CONF_NYMEA_UUID] == UUID
    assert result["data"][CONF_NYMEA_TOKEN] == "token-123"
