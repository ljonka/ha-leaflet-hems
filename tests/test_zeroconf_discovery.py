"""Test zeroconf discovery for Leaflet HEMS integration."""

import pytest
from unittest.mock import MagicMock, patch

from custom_components.leaflet_hems.config_flow import LeafletHEMSFlowHandler
from custom_components.leaflet_hems.const import (
    CONF_HOST,
    CONF_PORT,
    CONF_NYMEA_UUID,
    CONF_NYMEA_NAME,
    TXT_UUID,
    TXT_NAME,
    TXT_MANUFACTURER,
    TXT_SERVER_VERSION,
    TXT_SSL_ENABLED,
    ZEROCONF_NYMEA_MANUFACTURER,
)


class TestZeroconfDiscovery:
    """Test zeroconf discovery functionality."""

    def test_parse_discovery_info_valid(self):
        """Test parsing valid discovery info."""
        flow = LeafletHEMSFlowHandler()
        
        # Mock discovery info like what zeroconf provides (HA provides strings, not bytes)
        discovery_info = MagicMock()
        discovery_info.host = "192.168.1.100"
        discovery_info.port = 2222
        discovery_info.properties = {
            TXT_UUID: "{df0da3fa-f1e0-498e-b44a-cf56be89db57}",
            TXT_NAME: "1u0022-co-82",
            TXT_MANUFACTURER: ZEROCONF_NYMEA_MANUFACTURER,
            TXT_SERVER_VERSION: "1.7.021-1",
            TXT_SSL_ENABLED: "true",
        }
        
        result = flow._parse_discovery_info(discovery_info)
        
        assert result is not None
        assert result[CONF_HOST] == "192.168.1.100"
        assert result[CONF_PORT] == 2222
        assert result[CONF_NYMEA_UUID] == "df0da3fa-f1e0-498e-b44a-cf56be89db57"
        assert result[CONF_NYMEA_NAME] == "1u0022-co-82"

    def test_parse_discovery_info_wrong_manufacturer(self):
        """Test parsing discovery info with wrong manufacturer."""
        flow = LeafletHEMSFlowHandler()
        
        discovery_info = MagicMock()
        discovery_info.host = "192.168.1.100"
        discovery_info.port = 2222
        discovery_info.properties = {
            TXT_UUID: "{df0da3fa-f1e0-498e-b44a-cf56be89db57}",
            TXT_NAME: "some-device",
            TXT_MANUFACTURER: "wrong manufacturer",
            TXT_SERVER_VERSION: "1.0.0",
            TXT_SSL_ENABLED: "false",
        }
        
        result = flow._parse_discovery_info(discovery_info)
        
        assert result is None

    def test_parse_discovery_info_no_uuid(self):
        """Test parsing discovery info without UUID."""
        flow = LeafletHEMSFlowHandler()
        
        discovery_info = MagicMock()
        discovery_info.host = "192.168.1.100"
        discovery_info.port = 2222
        discovery_info.properties = {
            TXT_NAME: "some-device",
            TXT_MANUFACTURER: ZEROCONF_NYMEA_MANUFACTURER,
            TXT_SERVER_VERSION: "1.0.0",
            TXT_SSL_ENABLED: "false",
        }
        
        result = flow._parse_discovery_info(discovery_info)
        
        assert result is None

    def test_parse_discovery_info_fallback_name(self):
        """Test parsing discovery info with missing name uses fallback."""
        flow = LeafletHEMSFlowHandler()
        
        discovery_info = MagicMock()
        discovery_info.host = "192.168.1.100"
        discovery_info.port = 2222
        discovery_info.properties = {
            TXT_UUID: "{df0da3fa-f1e0-498e-b44a-cf56be89db57}",
            TXT_MANUFACTURER: ZEROCONF_NYMEA_MANUFACTURER,
            TXT_SERVER_VERSION: "1.0.0",
            TXT_SSL_ENABLED: "false",
        }
        
        result = flow._parse_discovery_info(discovery_info)
        
        assert result is not None
        assert result[CONF_NYMEA_NAME] == "Leaflet HEMS (192.168.1.100)"

    def test_parse_discovery_info_exception_handling(self):
        """Test that exceptions during parsing are handled gracefully."""
        flow = LeafletHEMSFlowHandler()
        
        # Mock discovery info that will cause an exception
        discovery_info = MagicMock()
        discovery_info.host = "192.168.1.100"
        discovery_info.port = 2222
        discovery_info.properties = None  # This will cause an exception
        
        result = flow._parse_discovery_info(discovery_info)
        
        assert result is None

    @pytest.mark.asyncio
    async def test_zeroconf_discovery_flow_abort_not_supported(self):
        """Test zeroconf discovery aborts when device not supported."""
        hass = MagicMock()
        flow = LeafletHEMSFlowHandler()
        flow.hass = hass
        
        # Mock discovery info for unsupported device
        discovery_info = MagicMock()
        discovery_info.host = "192.168.1.100"
        discovery_info.port = 2222
        discovery_info.properties = {
            TXT_UUID: "{df0da3fa-f1e0-498e-b44a-cf56be89db57}",
            TXT_NAME: "unsupported-device",
            TXT_MANUFACTURER: "wrong manufacturer",
        }
        
        with patch.object(flow, '_parse_discovery_info', return_value=None):
            result = await flow.async_step_zeroconf(discovery_info)
        
        assert result["type"] == "abort"
        assert result["reason"] == "not_supported"

    @pytest.mark.asyncio
    async def test_zeroconf_discovery_flow_abort_already_configured(self):
        """Test zeroconf discovery aborts when device already configured."""
        hass = MagicMock()
        flow = LeafletHEMSFlowHandler()
        flow.hass = hass
        
        # Mock discovery info for valid device
        discovery_info = MagicMock()
        parsed_info = {
            CONF_HOST: "192.168.1.100",
            CONF_PORT: 2222,
            CONF_NYMEA_UUID: "df0da3fa-f1e0-498e-b44a-cf56be89db57",
            CONF_NYMEA_NAME: "test-device",
        }
        
        with patch.object(flow, '_parse_discovery_info', return_value=parsed_info), \
             patch.object(flow, 'async_set_unique_id'), \
             patch.object(flow, '_abort_if_unique_id_configured', side_effect=Exception("already_configured")):
            
            with pytest.raises(Exception, match="already_configured"):
                await flow.async_step_zeroconf(discovery_info)
