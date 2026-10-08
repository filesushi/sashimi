_DEFAULT_CONFIG_STRING = """# Sashimi Global Configuration
# This config defines default behavior of the sashimi library.
# A project-specific config ('sashimi.toml') can override these settings.
# This file is TOML. See: https://toml.io/

[global]
verbose = 0
priority = "normal"

[thumbs]
static_width = 2560
animated_width = 1280
cols = "auto"
rows = "auto"
gap = 5
jpeg_quality = 85
output_format = "webp"
sample_duration = 2.0
start_offset_percent = 7.0
end_offset_percent = 7.0
output_fps = 12
loop = true
"""
