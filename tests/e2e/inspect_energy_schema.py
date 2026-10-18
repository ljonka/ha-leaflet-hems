from homeassistant.components.energy import data

for name in (
    "GRID_SOURCE_SCHEMA",
    "SOLAR_SOURCE_SCHEMA",
    "BATTERY_SOURCE_SCHEMA",
    "POWER_CONFIG_SCHEMA",
    "GRID_POWER_SOURCE_SCHEMA",
):
    schema = getattr(data, name, None)
    print("###", name)
    print(schema)
    print()
