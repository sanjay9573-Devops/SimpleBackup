"""
config.py

Loads and saves user settings (source folders, destination folder, auto-backup
interval) to a small JSON file in the user's home directory, so the app
remembers its setup between launches.
"""

import json
from pathlib import Path

CONFIG_DIR = Path.home() / ".backupapp"
CONFIG_PATH = CONFIG_DIR / "config.json"

DEFAULTS = {
    "source_dirs": [],
    "dest_dir": "",
    "auto_backup_enabled": False,
    "auto_backup_interval_minutes": 60,
}


def load_config():
    if not CONFIG_PATH.exists():
        return dict(DEFAULTS)
    try:
        with open(CONFIG_PATH, "r") as f:
            data = json.load(f)
        merged = dict(DEFAULTS)
        merged.update(data)
        return merged
    except (json.JSONDecodeError, OSError):
        return dict(DEFAULTS)


def save_config(config):
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    with open(CONFIG_PATH, "w") as f:
        json.dump(config, f, indent=2)
