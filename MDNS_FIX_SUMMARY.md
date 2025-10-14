# mDNS Discovery Fix Summary

## Problem Identified

Your Home Assistant integration was failing to discover Nymea devices via mDNS in the Docker environment due to two issues:

### Issue 1: Incorrect Service Type Format
**Location**: `custom_components/leaflet_hems/const.py`

The service type constant was set to:
```python
NYMD_SERVICE_TYPE = "_jsonrpc._tcp.local."
```

**Problem**: The `.local.` suffix should NOT be included when using `ServiceBrowser` from the zeroconf library. The `ServiceBrowser` API expects just the service type (e.g., `_jsonrpc._tcp.`), and it automatically handles the `.local` domain.

**Fix**: Changed to:
```python
NYMD_SERVICE_TYPE = "_jsonrpc._tcp."
```

### Issue 2: Docker Bridge Network Isolation
**Location**: `docker-compose.yml`

The Docker container was using bridge networking mode, which isolates the container from the host's network interface. This prevents mDNS/Zeroconf broadcasts from reaching the container since mDNS relies on multicast UDP packets that don't cross Docker bridge networks by default.

**Problem**: With bridge networking:
- The container has its own network namespace
- mDNS multicast packets (224.0.0.251) don't reach the container
- The container cannot discover devices on the host's LAN

**Fix**: Changed to host networking mode:
```yaml
network_mode: host
```

With host networking:
- The container shares the host's network stack
- mDNS multicast packets are directly accessible
- The container can discover all devices on the LAN
- Note: The `ports:` mapping is no longer needed as the container directly uses host ports

## Changes Made

### 1. `custom_components/leaflet_hems/const.py`
- Changed `NYMD_SERVICE_TYPE = "_jsonrpc._tcp.local."` to `NYMD_SERVICE_TYPE = "_jsonrpc._tcp."`

### 2. `docker-compose.yml`
- Removed `networks: - homeassistant_bridge` and `ports: - "8123:8123"`
- Added `network_mode: host`

### 3. `custom_components/leaflet_hems/config_flow.py`
- Updated debug listener to use correct service type format (removed `.local.` suffix)

## How to Test

1. **Rebuild and restart the container**:
   ```bash
   docker compose down
   docker compose build --no-cache
   docker compose up -d
   ```

2. **Access Home Assistant**:
   - Navigate to http://localhost:8123
   - Complete the initial setup if needed

3. **Test device discovery**:
   - Go to Configuration → Integrations
   - Click "Add Integration"
   - Search for "Leaflet HEMS"
   - Select "Discover" option
   - Wait 10 seconds for the discovery process
   - Your Nymea devices should now appear in the list

4. **Check logs for discovery**:
   ```bash
   docker logs home_assistant | grep -i "leaflet\|mdns\|nymea\|discovery"
   ```

   Look for messages like:
   - "Starting mDNS discovery for service type _jsonrpc._tcp."
   - "Discovered Leaflet HEMS at X.X.X.X:2222"
   - "DEBUG: Found mDNS service: nymea-tcp-default..."

## Expected Behavior

After the fix, you should see:
- Discovered devices showing up in the integration setup UI
- Log messages confirming mDNS discovery
- Your two Nymea devices (as you confirmed with `dns-sd`) appearing in the device list

## Alternative Solutions (if host networking is not desired)

If you cannot use host networking (e.g., for security or networking policy reasons), you have two alternatives:

### Option A: Use macvlan networking
Create a macvlan network that bridges directly to your physical network interface.

### Option B: Use avahi-daemon proxy
Install and configure avahi-daemon on the host to proxy mDNS between the bridge network and the host's network.

However, **host networking is the simplest and most reliable solution** for mDNS discovery in Docker, especially for development environments.

## Verification

To verify your devices are being broadcast correctly on the network:
```bash
# On macOS (host)
dns-sd -B _jsonrpc._tcp

# Inside the container (after fix)
docker exec -it home_assistant bash
# Then use avahi-browse if available, or check HA logs
```

## Notes

- The `network_mode: host` only works on Linux hosts. On macOS/Windows with Docker Desktop, the VM layer may still cause some issues, but the service type fix alone should help significantly.
- If you're on macOS and still experiencing issues, it might be due to Docker Desktop's networking implementation. In that case, consider testing on a Linux host or using Docker for Mac's host.docker.internal features.
