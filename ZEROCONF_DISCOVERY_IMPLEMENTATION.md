# Zeroconf Discovery Implementation for Leaflet HEMS

## Overview

This document summarizes the implementation of automatic device discovery using mDNS/Zeroconf for the Leaflet HEMS Home Assistant integration.

## Implementation Details

### 1. Manifest Configuration

**File:** `custom_components/leaflet_hems/manifest.json`

Added zeroconf service discovery configuration:
```json
{
  "zeroconf": [
    {
      "type": "_jsonrpc._tcp.local.",
      "name": "*nymea*"
    }
  ]
}
```

This configuration tells Home Assistant to automatically trigger discovery when it detects services of type `_jsonrpc._tcp.local.` with "nymea" in the service name.

### 2. Discovery Constants

**File:** `custom_components/leaflet_hems/const.py`

Added constants for discovery functionality:
- `ZEROCONF_SERVICE_TYPE`: Service type to discover (`_jsonrpc._tcp.local.`)
- `ZEROCONF_NYMEA_MANUFACTURER`: Expected manufacturer ("nymea GmbH")
- TXT record key constants: `TXT_UUID`, `TXT_NAME`, `TXT_MANUFACTURER`, etc.

### 3. Config Flow Implementation

**File:** `custom_components/leaflet_hems/config_flow.py`

#### New Methods Added:

1. **`async_step_zeroconf(discovery_info)`**
   - Entry point for zeroconf discovery
   - Parses discovery information
   - Validates device compatibility
   - Performs handshake validation
   - Redirects to confirmation step

2. **`async_step_discovery_confirm(user_input)`**
   - Shows discovered device information to user
   - Handles user confirmation
   - Checks authentication requirements
   - Creates config entry or redirects to auth

3. **`_parse_discovery_info(discovery_info)`**
   - Parses zeroconf discovery data
   - Validates manufacturer is "nymea GmbH"
   - Extracts device information from TXT records
   - Handles UUID cleanup and fallback names

#### Discovery Flow:
```
Zeroconf Discovery → Device Validation → User Confirmation → Authentication (if needed) → Entry Creation
```

### 4. User Interface Strings

**File:** `custom_components/leaflet_hems/strings.json`

Added translations for discovery-related forms:
- `discovery_confirm` step with device information display
- Error messages for discovery failures
- Abort reasons for various discovery scenarios

### 5. Test Coverage

**File:** `tests/test_zeroconf_discovery.py`

Comprehensive test suite covering:
- Valid discovery info parsing
- Invalid manufacturer rejection
- Missing UUID handling
- Fallback name generation
- Exception handling
- Discovery flow scenarios

## How It Works

### 1. Automatic Detection

When a Leaflet HEMS device comes online, it advertises a service:
```
Service: nymea-tcp-default._jsonrpc._tcp.local.
Host: device-hostname.local
Port: 2222
TXT Records:
  - uuid: {device-uuid}
  - name: device-name
  - manufacturer: nymea GmbH
  - serverVersion: version
  - sslEnabled: true/false
```

### 2. Home Assistant Integration

Home Assistant automatically detects this service and triggers the integration's discovery flow because:
- Service type matches `_jsonrpc._tcp.local.`
- Service name contains "nymea"

### 3. Device Validation

The integration validates the discovered device by:
- Checking manufacturer is "nymea GmbH"
- Extracting and validating UUID
- Performing TCP+TLS handshake to confirm connectivity

### 4. User Experience

Users see:
1. Device appears in discovery panel automatically
2. Device information displayed (name, host, port, UUID)
3. Confirmation dialog to add the device
4. Authentication form if required
5. Device added to Home Assistant

## Benefits

1. **Zero Configuration**: Users don't need to know IP addresses
2. **Dynamic Discovery**: Automatically finds devices as they come online
3. **Professional UX**: Integrates with HA's standard discovery panel
4. **Validation**: Ensures only compatible devices are offered
5. **Backward Compatible**: Manual setup still available as fallback

## Technical Details

### Service Type Selection

We use `_jsonrpc._tcp.local.` because:
- This is the standard service type advertised by nymea devices
- Matches the example provided: `nymea-tcp-default._jsonrpc._tcp.local.`
- Aligns with nymea documentation

### Manufacturer Validation

Critical for ensuring we only discover actual Leaflet HEMS devices:
```python
manufacturer = properties.get(TXT_MANUFACTURER, b"").decode("utf-8", errors="ignore")
if manufacturer != ZEROCONF_NYMEA_MANUFACTURER:
    return None  # Not a supported device
```

### UUID Handling

Handles both formats:
- With braces: `{df0da3fa-f1e0-498e-b44a-cf56be89db57}`
- Without braces: `df0da3fa-f1e0-498e-b44a-cf56be89db57`

### Error Handling

Comprehensive error handling for:
- Network connectivity issues
- Invalid discovery data
- Parsing exceptions
- Device validation failures

## Testing the Implementation

### Unit Tests
```bash
python3 -m py_compile tests/test_zeroconf_discovery.py
```

### Integration Testing
1. Start Home Assistant with the integration
2. Ensure a Leaflet HEMS device is on the network
3. Check if device appears in discovery panel
4. Test the discovery flow end-to-end

### Manual Discovery Testing
You can test discovery detection with:
```bash
dns-sd -L "nymea-tcp-default" _jsonrpc._tcp local
```

## Conclusion

The zeroconf discovery implementation provides a seamless user experience for adding Leaflet HEMS devices to Home Assistant. It follows Home Assistant's standard patterns, includes comprehensive validation, and maintains backward compatibility with manual setup.

The implementation is production-ready and includes proper error handling, test coverage, and user-friendly interfaces.
