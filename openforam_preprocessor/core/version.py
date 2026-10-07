"""Application and data-format versions."""

APP_VERSION = "0.1.0"  # keep in sync with pyproject.toml (tested)

# Project configuration schema. Version 2 introduced required source units,
# feature-extraction and snappy quality settings. Files from a newer
# application are rejected rather than misread.
CONFIG_SCHEMA_VERSION = 2
