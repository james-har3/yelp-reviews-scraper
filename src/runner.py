import json
import logging
import os
import sys
from typing import Any, Dict, List, Optional

import requests

# Make src importable when running this file directly
SRC_DIR = os.path.dirname(__file__)
if SRC_DIR not in sys.path:
    sys.path.insert(0, SRC_DIR)

from extractors.yelp_parser import YelpPageParser  # type: ignore
from outputs.exporter_json import export_to_json  # type: ignore

LOGGER = logging.getLogger("yelp_reviews_scraper")

def configure_logging() -> None:
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s - %(message)s",
    )

def load_settings() -> Dict[str, Any]:
    """
    Load settings from settings.json if it exists, otherwise from settings.example.json.
    Falls back to sensible defaults if neither exists.
    """
    config_dir = os.path.join(SRC_DIR, "config")
    primary = os.path.join(config_dir, "settings.json")
    fallback = os.path.join(config_dir, "settings.example.json")

    path_to_use: Optional[str] = None
    if os.path.exists(primary):
        path_to_use = primary
    elif os.path.exists(fallback):
        path_to_use = fallback

    if path_to_use:
        try:
            with open(path_to_use, "r", encoding="utf-8") as fh:
                settings = json.load(fh)
            LOGGER.info("Loaded settings from %s", os.path.basename(path_to_use))
            return settings
        except (OSError, json.JSONDecodeError) as exc:
            LOGGER.error("Failed to read settings file %s: %s", path_to_use, exc)

    LOGGER.warning("Using built-in default settings")
    return {
        "http": {
            "user_agent": "Mozilla/5.0 (compatible; YelpReviewsScraper/1.0)",
            "timeout": 15,
        },
        "scraper": {
            "max_reviews": 200,
            "min_rating": 1,
            "max_rating": 5,
            "from_date": None,
        },
        "output": {
            "directory": "data",
            "file_name": "sample.json",
            "indent": 2,
        },
    }

def get_project_root() -> str:
    """Return absolute path to the project root (one level above src)."""
    return os.path.dirname(SRC_DIR)

def load_input_urls(root: str) -> List[str]:
    """
    Read Yelp business URLs from data/inputs.sample.txt.
    Ignores empty lines and lines starting with '#'.
    """
    input_path = os.path.join(root, "data", "inputs.sample.txt")
    urls: List[str] = []

    if not os.path.exists(input_path):
        LOGGER.warning(
            "Input file %s not found. Please create it with one Yelp URL per line.",
            input_path,
        )
        return urls

    try:
        with open(input_path, "r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line or line.startswith("#"):
                    continue
                urls.append(line)
    except OSError as exc:
        LOGGER.error("Failed to read input URLs from %s: %s", input_path, exc)
        return []

    if not urls:
        LOGGER.warning("No URLs found in %s", input_path)

    return urls

def build_http_session(settings: Dict[str, Any]) -> requests.Session:
    """Create a configured requests.Session."""
    http_settings = settings.get("http", {})
    user_agent = http_settings.get(
        "user_agent",
        "Mozilla/5.0 (compatible; YelpReviewsScraper/1.0)",
    )
    session = requests.Session()
    session.headers.update(
        {
            "User-Agent": user_agent,
            "Accept-Language": "en-US,en;q=0.9",
        }
    )
    return session

def main() -> None:
    configure_logging()
    LOGGER.info("Starting Yelp Reviews Scraper")

    root = get_project_root()
    settings = load_settings()
    urls = load_input_urls(root)

    if not urls:
        LOGGER.error("No input URLs found. Exiting.")
        return

    session = build_http_session(settings)
    parser = YelpPageParser(
        session=session,
        timeout=settings.get("http", {}).get("timeout", 15),
        scraper_settings=settings.get("scraper", {}),
    )

    all_records: List[Dict[str, Any]] = []

    for idx, url in enumerate(urls, start=1):
        LOGGER.info("Processing %d/%d: %s", idx, len(urls), url)
        try:
            records = parser.parse_business_page(url)
        except Exception as exc:  # noqa: BLE001
            LOGGER.exception("Unexpected error while processing %s: %s", url, exc)
            continue

        if not records:
            LOGGER.warning("No reviews extracted for %s", url)
        else:
            LOGGER.info("Extracted %d review records for %s", len(records), url)
            all_records.extend(records)

    if not all_records:
        LOGGER.error("No data extracted from any URL. Exiting without writing output.")
        return

    output_cfg = settings.get("output", {})
    out_dir = output_cfg.get("directory", "data")
    file_name = output_cfg.get("file_name", "sample.json")
    indent = int(output_cfg.get("indent", 2))

    output_path = os.path.join(root, out_dir, file_name)
    try:
        export_to_json(all_records, output_path, indent=indent)
    except Exception as exc:  # noqa: BLE001
        LOGGER.exception("Failed to export data to %s: %s", output_path, exc)
        return

    LOGGER.info("Scraping completed. Output written to %s", output_path)

if __name__ == "__main__":
    main()