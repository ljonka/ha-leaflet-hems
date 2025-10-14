# macOS Docker Networking for Home Assistant Development

## Issue Summary

On macOS, Docker Desktop runs in a Linux VM, which creates networking challenges when trying to access local network devices from containers. Your Leaflet device at `192.168.178.42:2222` cannot be reached directly from Docker containers.

## What Was Fixed

✅ **SSL Blocking Warnings** - Modified `custom_components/leaflet_hems/config_flow.py` to use `asyncio.to_thread()` for SSL context creation, preventing blocking I/O operations in the event loop.

## Networking Solutions for macOS

Since Docker networking on macOS is limited, here are your options:

### Option 1: Run Home Assistant Without Docker (Recommended for Development)

Install Home Assistant directly on your Mac:

```bash
# Create a virtual environment
python3 -m venv venv
source venv/bin/activate

# Install Home Assistant
pip install homeassistant

# Create config directory
mkdir -p ~/homeassistant/config
cd ~/homeassistant

# Copy your custom component
cp -r /path/to/custom_components/leaflet_hems config/custom_components/

# Run Home Assistant
hass -c config
```

**Pros:**
- Direct network access to all local devices
- Faster development cycle (no Docker rebuild needed)
- Easier debugging

**Cons:**
- Different environment than production
- More setup required

### Option 2: Deploy to a Linux Machine

Run Home Assistant on a Linux system (Raspberry Pi, VM, or remote server) where `network_mode: host` works properly.

### Option 3: Configure Device Firewall

If possible, configure your Leaflet device to accept connections from Docker's subnet (typically `172.x.x.x`). This requires access to the device's firewall settings.

### Option 4: Use GitHub Codespaces / Remote Development

Develop on a cloud Linux instance where Docker networking works as expected.

## Current Status

- ✅ SSL blocking warnings fixed
- ✅ Docker compose simplified
- ⚠️ Network connectivity requires one of the solutions above

## Testing Your Integration

Once you have network connectivity, you can test the integration through Home Assistant's UI at `http://localhost:8123` (or the appropriate port).
