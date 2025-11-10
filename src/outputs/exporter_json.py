import json
import logging
import os
from typing import Any, Dict, List

LOGGER = logging.getLogger("yelp_reviews_scraper.exporter")

def export_to_json(
    records: List[Dict[str, Any]],
    output_path: str,
    indent: int = 2,
) -> None:
    """
    Write the given records list to the specified JSON file.
    """
    directory = os.path.dirname(os.path.abspath(output_path))
    if directory and not os.path.exists(directory):
        try:
            os.makedirs(directory, exist_ok=True)
            LOGGER.info("Created output directory %s", directory)
        except OSError as exc:
            LOGGER.error("Failed to create output directory %s: %s", directory, exc)
            raise

    try:
        with open(output_path, "w", encoding="utf-8") as fh:
            json.dump(records, fh, indent=indent, ensure_ascii=False)
    except OSError as exc:
        LOGGER.error("Failed to write JSON to %s: %s", output_path, exc)
        raise

    LOGGER.info("Successfully wrote %d records to %s", len(records), output_path)