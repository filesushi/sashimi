_DEFAULT_CONFIG_STRING = """# Sashimi Global Configuration
# This config defines default behavior of the sashimi library.
# A project-specific config ('sashimi.toml') can override these settings.
# This file is TOML. See: https://toml.io/

[global]
verbose = 0
priority = "normal"

[thumbs]
output_width = 1280
cols = 3
rows = 3
sample_duration = 2.0
start_offset = "5%"
end_offset = "5%"
output_fps = 12
quality = 80
loop = true
"""
