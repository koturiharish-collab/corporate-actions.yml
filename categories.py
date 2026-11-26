import json
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent
PREFS_FILE = BASE_DIR / "preferences.json"

CATEGORIES = {
    "results": "Results",
    "ema": "EMA signals",
    "stops": "Stops",
    "order_wins": "Order wins",
    "high_volume": "High volume",
    "splits_buybacks": "Splits and buybacks",
    "promoter_deals": "Promoter buying and selling",
    "news": "News",
}

DEFAULT_CATEGORIES = {
    "results": True,
    "ema": True,
    "stops": False,
    "order_wins": False,
    "high_volume": False,
    "splits_buybacks": False,
    "promoter_deals": False,
    "news": False,
}


def category_enabled(category):
    saved = {}

    try:
        with open(PREFS_FILE, encoding="utf-8") as file:
            saved = json.load(file).get("categories", {})
    except (OSError, json.JSONDecodeError, AttributeError):
        pass  # missing or broken file: fall back to defaults

    merged = {**DEFAULT_CATEGORIES, **saved}

    # Unknown categories stay off.
    return merged.get(category, False) is True