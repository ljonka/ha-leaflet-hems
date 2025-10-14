.PHONY: build up down logs clean rebuild test_integration test_config_flow

# Variables
HASS_CONTAINER_NAME = home_assistant
HASS_PORT = 8123

# Build the custom Home Assistant image
build:
	@echo "Building Home Assistant image with leaflet_hems integration..."
	docker-compose build

# Start the Home Assistant container
up:
	@echo "Starting Home Assistant..."
	docker-compose up -d
	@echo "Home Assistant should be available at http://localhost:$(HASS_PORT)"
	@echo "Initial startup may take a few minutes. Check logs with 'make logs'"

# Stop the Home Assistant container
down:
	@echo "Stopping Home Assistant..."
	docker-compose down

# View logs of the Home Assistant container
logs:
	@echo "Showing logs for $(HASS_CONTAINER_NAME) (Ctrl+C to stop):"
	docker-compose logs -f $(HASS_CONTAINER_NAME)

# Clean up volumes (removes Home Assistant configuration)
clean:
	@echo "Stopping and removing containers, and pruning volumes..."
	docker-compose down -v --remove-orphans
	docker system prune -f

# Rebuild and start the Home Assistant container
rebuild: clean build up
	@echo "Rebuilt and started Home Assistant."

# Run integration tests (requires test environment to be set up)
# This is a placeholder. Actual test execution might differ.
test_integration:
	@echo "Running integration tests..."
	# Example: docker exec $(HASS_CONTAINER_NAME) python -m pytest /tests/
	# Or run tests from host against a running instance if configured
	pytest tests/ -v

# Run config flow tests specifically
test_config_flow:
	@echo "Running config flow tests..."
	pytest tests/test_config_flow.py -v

# Stop, remove, rebuild image, and start fresh
fresh: clean build up
	@echo "Freshly built and started Home Assistant."

# Enter the running container for debugging
shell:
	@echo "Opening a shell in the $(HASS_CONTAINER_NAME) container..."
	docker exec -it $(HASS_CONTAINER_NAME) sh
