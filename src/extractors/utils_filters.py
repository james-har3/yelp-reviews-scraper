import logging
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

from dateutil import parser as date_parser

LOGGER = logging.getLogger("yelp_reviews_scraper.filters")

@dataclass
class ReviewFilters:
    min_rating: Optional[int] = None
    max_rating: Optional[int] = None
    from_date: Optional[datetime] = None

    @classmethod
    def from_settings(cls, settings: Dict[str, Any]) -> "ReviewFilters":
        """
        Build filter configuration from the scraper settings section.
        """
        min_rating = settings.get("min_rating")
        max_rating = settings.get("max_rating")
        from_date_raw = settings.get("from_date")

        if min_rating is not None:
            try:
                min_rating = int(min_rating)
            except (TypeError, ValueError):
                LOGGER.warning("Invalid min_rating %r in settings", min_rating)
                min_rating = None

        if max_rating is not None:
            try:
                max_rating = int(max_rating)
            except (TypeError, ValueError):
                LOGGER.warning("Invalid max_rating %r in settings", max_rating)
                max_rating = None

        from_date: Optional[datetime] = None
        if from_date_raw:
            try:
                from_date = parse_review_date(str(from_date_raw))
            except Exception as exc:  # noqa: BLE001
                LOGGER.warning("Failed to parse from_date %r: %s", from_date_raw, exc)
                from_date = None

        return cls(min_rating=min_rating, max_rating=max_rating, from_date=from_date)

def parse_review_date(text: str) -> datetime:
    """
    Parse a review date string into a timezone-aware datetime where possible.
    Falls back to naive datetime if timezone cannot be determined.
    """
    try:
        dt = date_parser.parse(text)
    except (ValueError, TypeError) as exc:
        LOGGER.debug("dateutil failed to parse date %r: %s", text, exc)
        raise

    return dt

def apply_filters(
    reviews: Iterable[Dict[str, Any]],
    filters: ReviewFilters,
) -> List[Dict[str, Any]]:
    """
    Apply rating and date filters to a list of reviews.
    Reviews that cannot be evaluated (missing rating/date) are kept unless
    filters explicitly require them.
    """
    result: List[Dict[str, Any]] = []

    for review in reviews:
        if not _passes_rating_filter(review, filters):
            continue
        if not _passes_date_filter(review, filters):
            continue
        result.append(review)

    LOGGER.info(
        "Filter applied: min_rating=%s max_rating=%s from_date=%s -> %d reviews",
        filters.min_rating,
        filters.max_rating,
        filters.from_date.isoformat() if filters.from_date else None,
        len(result),
    )
    return result

def _passes_rating_filter(review: Dict[str, Any], filters: ReviewFilters) -> bool:
    rating = review.get("latest_reviewer_rating")
    if not isinstance(rating, int):
        # If we don't know the rating, keep it unless we have explicit bounds.
        if filters.min_rating is None and filters.max_rating is None:
            return True
        return False

    if filters.min_rating is not None and rating < filters.min_rating:
        return False
    if filters.max_rating is not None and rating > filters.max_rating:
        return False
    return True

def _passes_date_filter(review: Dict[str, Any], filters: ReviewFilters) -> bool:
    if filters.from_date is None:
        return True

    date_raw = review.get("review_date")
    if not date_raw:
        # If no date data but we have a filter, drop the review.
        return False

    try:
        review_dt = _ensure_datetime(date_raw)
    except Exception as exc:  # noqa: BLE001
        LOGGER.debug("Failed to parse review_date %r: %s", date_raw, exc)
        return False

    return review_dt >= filters.from_date

def _ensure_datetime(value: Any) -> datetime:
    if isinstance(value, datetime):
        return value
    if isinstance(value, str):
        return parse_review_date(value)
    raise TypeError(f"Unsupported date value type: {type(value)!r}")