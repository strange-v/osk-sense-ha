"""Constants for the OSK Sense integration."""

from typing import Final

DOMAIN: Final = "osk_sense"
CONF_TOKEN: Final = "token"
CONF_PULSE_COUNTERS: Final = "pulse_counters"
CONF_DEVICE_CLASS: Final = "device_class"
CONF_UNIT: Final = "unit"
CONF_UNITS_PER_PULSE: Final = "units_per_pulse"
MANUFACTURER: Final = "OSK"
STALE_AFTER_SECONDS: Final = 2 * 60 * 60 + 15 * 60
