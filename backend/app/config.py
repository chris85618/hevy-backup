import os

DB_PATH = os.environ.get("DB_PATH", "/data/fitir.db")

# Env vars seed the settings table on first boot; the GUI can override later.
ENV_SETTING_DEFAULTS = {
    "hevy_api_key": os.environ.get("HEVY_API_KEY", ""),
    "wger_base_url": os.environ.get("WGER_BASE_URL", ""),
    "wger_api_key": os.environ.get("WGER_API_KEY", ""),
    "sync_cron": os.environ.get("SYNC_CRON", ""),
}

SECRET_SETTINGS = {"hevy_api_key", "wger_api_key"}
