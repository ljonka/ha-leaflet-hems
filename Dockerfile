# Use the official Home Assistant base image
ARG HASSIO_VERSION=2025.10.2
FROM ghcr.io/home-assistant/home-assistant:${HASSIO_VERSION}

# Set working directory
WORKDIR /config

# Copy the custom integration into the container
# This places the leaflet_hems integration into the custom_components directory
COPY custom_components/ /config/custom_components/

# Copy requirements if needed (though HA usually handles its own deps)
# For custom components with external deps, they might need to be installed here
# However, manifest.json should list them, and HA generally picks them up.
# If specific Python package installation is needed for the component itself:
# RUN pip install -r /config/custom_components/leaflet_hems/requirements.txt

# Copy any other necessary configuration files if needed
# For example, a configuration.yaml for initial setup
# COPY configuration.yaml /config/configuration.yaml

# Expose the Home Assistant port
EXPOSE 8123

# The base image entrypoint and cmd will handle running Home Assistant
