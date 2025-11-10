import json
import logging
import re
from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Dict, Iterable, List, Optional

import requests
from bs4 import BeautifulSoup, Tag

from extractors.utils_filters import ReviewFilters, apply_filters, parse_review_date

LOGGER = logging.getLogger("yelp_reviews_scraper.yelp_parser")

@dataclass
class BusinessInfo:
    business_url: str
    business_name: Optional[str] = None
    average_rating: Optional[float] = None
    total_reviews_text: Optional[str] = None
    price_range: Optional[str] = None
    business_address: Optional[str] = None
    contact_number: Optional[str] = None

class YelpPageParser:
    """
    Responsible for fetching and parsing a Yelp business page into structured data.

    This parser is intentionally conservative and tries to avoid depending on
    brittle CSS class names. It focuses on semantic attributes and general
    structure, so it can be adapted as Yelp's markup evolves.
    """

    def __init__(
        self,
        session: requests.Session,
        timeout: int = 15,
        scraper_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        self.session = session
        self.timeout = timeout
        self.scraper_settings = scraper_settings or {}

    # ------------------------------------------------------------------ #
    # Public API
    # ------------------------------------------------------------------ #

    def parse_business_page(self, url: str) -> List[Dict[str, Any]]:
        """
        High-level entry point: fetch a single business URL and return a list of
        review records including business metadata duplicated per review.
        """
        html = self._fetch_html(url)
        soup = BeautifulSoup(html, "lxml")

        business = self._parse_business_info(soup, url)
        reviews = self._parse_reviews(soup)

        max_reviews = int(self.scraper_settings.get("max_reviews", 200))
        reviews = reviews[:max_reviews]

        filters = ReviewFilters.from_settings(self.scraper_settings)
        reviews = apply_filters(reviews, filters)

        review_counts_by_rating = self._build_rating_histogram(reviews)

        records: List[Dict[str, Any]] = []
        for review in reviews:
            record: Dict[str, Any] = {
                "business_url": business.business_url,
                "business_name": business.business_name,
                "average_rating": (
                    f"{business.average_rating:.1f}"
                    if business.average_rating is not None
                    else None
                ),
                "total_reviews": business.total_reviews_text,
                "price_range": business.price_range,
                "business_address": business.business_address,
                "contact_number": business.contact_number,
                "review_counts_by_rating": review_counts_by_rating,
            }
            record.update(review)
            records.append(record)

        return records

    # ------------------------------------------------------------------ #
    # HTTP
    # ------------------------------------------------------------------ #

    def _fetch_html(self, url: str) -> str:
        LOGGER.info("Fetching %s", url)
        try:
            resp = self.session.get(url, timeout=self.timeout)
            resp.raise_for_status()
        except requests.RequestException as exc:
            LOGGER.error("HTTP error while fetching %s: %s", url, exc)
            raise

        LOGGER.debug("Fetched %d bytes from %s", len(resp.text), url)
        return resp.text

    # ------------------------------------------------------------------ #
    # Business parsing
    # ------------------------------------------------------------------ #

    def _parse_business_info(self, soup: BeautifulSoup, url: str) -> BusinessInfo:
        business_name = self._extract_business_name(soup)
        avg_rating = self._extract_average_rating(soup)
        total_reviews_text = self._extract_total_reviews_text(soup)
        price_range = self._extract_price_range(soup)
        address = self._extract_business_address(soup)
        phone = self._extract_phone_number(soup)

        LOGGER.debug(
            "Business parsed: name=%r rating=%r reviews=%r",
            business_name,
            avg_rating,
            total_reviews_text,
        )

        return BusinessInfo(
            business_url=url,
            business_name=business_name,
            average_rating=avg_rating,
            total_reviews_text=total_reviews_text,
            price_range=price_range,
            business_address=address,
            contact_number=phone,
        )

    @staticmethod
    def _extract_business_name(soup: BeautifulSoup) -> Optional[str]:
        # Most Yelp pages have the business name in the first <h1>
        h1 = soup.find("h1")
        if h1 and h1.get_text(strip=True):
            return h1.get_text(strip=True)
        # Fallback: meta og:title
        og_title = soup.find("meta", property="og:title")
        if og_title and og_title.get("content"):
            return og_title["content"].strip()
        return None

    @staticmethod
    def _extract_average_rating(soup: BeautifulSoup) -> Optional[float]:
        # Look for an element with aria-label like "4.5 star rating"
        rating_el = soup.find(attrs={"aria-label": re.compile(r"star rating", re.I)})
        if rating_el:
            label = rating_el.get("aria-label", "")
            match = re.search(r"([0-9.]+)", label)
            if match:
                try:
                    return float(match.group(1))
                except ValueError:
                    pass
        # Fallback: JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict) and data.get("@type") in {"Restaurant", "LocalBusiness"}:
                agg = data.get("aggregateRating") or {}
                try:
                    return float(agg.get("ratingValue"))
                except (TypeError, ValueError):
                    continue
        return None

    @staticmethod
    def _extract_total_reviews_text(soup: BeautifulSoup) -> Optional[str]:
        # Common pattern: "123 reviews"
        text_candidates: List[str] = []
        for el in soup.find_all(text=re.compile(r"\breviews?\b", re.I)):
            val = el.strip()
            if re.search(r"\d+", val):
                text_candidates.append(val)
        if text_candidates:
            # Choose the shortest one that looks like "123 reviews"
            text_candidates.sort(key=len)
            return text_candidates[0]
        return None

    @staticmethod
    def _extract_price_range(soup: BeautifulSoup) -> Optional[str]:
        # Typical price range: "$$", "$$$"
        for el in soup.find_all(string=re.compile(r"^\$+$")):
            val = el.strip()
            if 1 <= len(val) <= 4:
                return val
        return None

    @staticmethod
    def _extract_business_address(soup: BeautifulSoup) -> Optional[str]:
        # Prefer <address> tag
        addr_tag = soup.find("address")
        if addr_tag:
            text = " ".join(addr_tag.stripped_strings)
            if text:
                return text
        # Fallback: Schema.org in JSON-LD
        for script in soup.find_all("script", type="application/ld+json"):
            try:
                data = json.loads(script.string or "")
            except Exception:  # noqa: BLE001
                continue
            if isinstance(data, dict) and data.get("@type") in {"Restaurant", "LocalBusiness"}:
                address = data.get("address") or {}
                if isinstance(address, dict):
                    parts = [
                        address.get("streetAddress", ""),
                        address.get("addressLocality", ""),
                        address.get("addressRegion", ""),
                        address.get("postalCode", ""),
                    ]
                    text = " ".join(p for p in parts if p).strip()
                    if text:
                        return text
        return None

    @staticmethod
    def _extract_phone_number(soup: BeautifulSoup) -> Optional[str]:
        # Look for tel: URI
        for link in soup.find_all("a", href=True):
            href = link["href"]
            if href.startswith("tel:"):
                return link.get_text(strip=True) or href.replace("tel:", "")
        return None

    # ------------------------------------------------------------------ #
    # Review parsing
    # ------------------------------------------------------------------ #

    def _parse_reviews(self, soup: BeautifulSoup) -> List[Dict[str, Any]]:
        containers = self._locate_review_containers(soup)
        reviews: List[Dict[str, Any]] = []

        for container in containers:
            try:
                review = self._parse_single_review(container)
            except Exception as exc:  # noqa: BLE001
                LOGGER.debug("Failed to parse one review block: %s", exc)
                continue
            reviews.append(review)

        LOGGER.info("Parsed %d review blocks", len(reviews))
        return reviews

    @staticmethod
    def _locate_review_containers(soup: BeautifulSoup) -> Iterable[Tag]:
        """
        Try a few different strategies to locate individual review containers.
        Yelp changes its markup occasionally, so we keep this flexible.
        """
        # Strategy 1: elements with data-review-id
        containers = soup.select("[data-review-id]")
        if containers:
            return containers

        # Strategy 2: list items with 'review' in class
        containers = soup.select("li[class*='review']")
        if containers:
            return containers

        # Strategy 3: sections with role="region" that contain rating info
        containers = [
            section
            for section in soup.find_all("section")
            if section.find(attrs={"aria-label": re.compile(r"star rating", re.I)})
        ]
        return containers

    def _parse_single_review(self, container: Tag) -> Dict[str, Any]:
        reviewer_name = self._extract_reviewer_name(container)
        avatar_url = self._extract_avatar_url(container)
        reviewer_details = self._extract_reviewer_details(container)
        reviewer_location = self._extract_reviewer_location(container)
        reviewer_rating = self._extract_reviewer_rating(container)
        review_date_raw = self._extract_review_date_raw(container)
        review_date_iso = (
            parse_review_date(review_date_raw).isoformat()
            if review_date_raw
            else None
        )
        review_text = self._extract_review_text(container)
        review_media_urls = self._extract_review_media_urls(container)
        helpful_count, thanks_count, love_this_count, oh_no_count = (
            self._extract_reaction_counts(container)
        )
        response_author_name, response_date, response_content = (
            self._extract_business_response(container)
        )

        return {
            "latest_reviewer_name": reviewer_name,
            "review_avatar_url": avatar_url,
            "latest_reviewer_details": reviewer_details,
            "latest_reviewer_location": reviewer_location,
            "latest_reviewer_rating": reviewer_rating,
            "review_date": review_date_iso,
            "review_text": review_text,
            "review_media_urls": review_media_urls,
            "helpful_count": helpful_count,
            "thanks_count": thanks_count,
            "love_this_count": love_this_count,
            "oh_no_count": oh_no_count,
            "response_author_name": response_author_name,
            "response_date": response_date,
            "response_content": response_content,
        }

    @staticmethod
    def _extract_reviewer_name(container: Tag) -> Optional[str]:
        # First try link with profile href
        for link in container.find_all("a", href=True):
            if "/user_details?" in link["href"]:
                text = link.get_text(strip=True)
                if text:
                    return text
        # Fallback: strong/bold username near rating
        strong = container.find("strong")
        if strong and strong.get_text(strip=True):
            return strong.get_text(strip=True)
        return None

    @staticmethod
    def _extract_avatar_url(container: Tag) -> Optional[str]:
        img = container.find("img")
        if img and img.get("src"):
            return img["src"]
        return None

    @staticmethod
    def _extract_reviewer_details(container: Tag) -> Dict[str, int]:
        """
        Attempt to derive reviewer stats like total reviews, friends, and photos.
        We look for small numeric text segments near the user name block.
        """
        details_text = " ".join(container.stripped_strings)
        stats = {
            "total_reviews": 0,
            "total_friends": 0,
            "business_photos_uploaded": 0,
        }

        patterns = {
            "total_reviews": re.compile(r"(\d+)\s+reviews?\b", re.I),
            "total_friends": re.compile(r"(\d+)\s+friends?\b", re.I),
            "business_photos_uploaded": re.compile(r"(\d+)\s+photos?\b", re.I),
        }

        for key, pattern in patterns.items():
            match = pattern.search(details_text)
            if match:
                try:
                    stats[key] = int(match.group(1))
                except ValueError:
                    continue

        return stats

    @staticmethod
    def _extract_reviewer_location(container: Tag) -> Optional[str]:
        # Look for a small text block that appears near the name and has city-like pattern
        candidates = []
        for span in container.find_all("span"):
            text = span.get_text(strip=True)
            if "," in text and any(c.isalpha() for c in text):
                candidates.append(text)
        if candidates:
            # Choose shortest candidate with comma (likely "City, ST")
            candidates.sort(key=len)
            return candidates[0]
        return None

    @staticmethod
    def _extract_reviewer_rating(container: Tag) -> Optional[int]:
        rating_el = container.find(attrs={"aria-label": re.compile("star rating", re.I)})
        if rating_el:
            label = rating_el.get("aria-label", "")
            match = re.search(r"(\d+)", label)
            if match:
                try:
                    return int(match.group(1))
                except ValueError:
                    pass
        return None

    @staticmethod
    def _extract_review_date_raw(container: Tag) -> Optional[str]:
        # Look for <span> or <time> elements that look like dates
        for time_el in container.find_all("time"):
            text = time_el.get_text(strip=True)
            if text:
                return text
        for span in container.find_all("span"):
            text = span.get_text(strip=True)
            # Heuristic: contain a month name or year digits
            if re.search(
                r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", text, re.I
            ) or re.search(r"\b20\d{2}\b", text):
                return text
        return None

    @staticmethod
    def _extract_review_text(container: Tag) -> Optional[str]:
        paragraphs = []
        for p in container.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text.split()) > 3:  # avoid tiny snippets
                paragraphs.append(text)
        if paragraphs:
            return " ".join(paragraphs)
        return None

    @staticmethod
    def _extract_review_media_urls(container: Tag) -> List[str]:
        urls: List[str] = []
        for img in container.find_all("img"):
            src = img.get("src")
            if src and "yelp" in src and src not in urls:
                urls.append(src)
        for video in container.find_all("video"):
            for source in video.find_all("source"):
                src = source.get("src")
                if src and src not in urls:
                    urls.append(src)
        return urls

    @staticmethod
    def _extract_reaction_counts(container: Tag) -> tuple[int, int, int, int]:
        """
        Extract counts for "Helpful", "Thanks", "Love this", and "Oh no" buttons.
        """
        text = " ".join(container.stripped_strings)
        patterns = {
            "helpful": re.compile(r"(\d+)\s+Helpful", re.I),
            "thanks": re.compile(r"(\d+)\s+Thanks", re.I),
            "love": re.compile(r"(\d+)\s+Love this", re.I),
            "oh_no": re.compile(r"(\d+)\s+Oh no", re.I),
        }

        def match(pattern: re.Pattern[str]) -> int:
            m = pattern.search(text)
            if m:
                try:
                    return int(m.group(1))
                except ValueError:
                    return 0
            return 0

        helpful = match(patterns["helpful"])
        thanks = match(patterns["thanks"])
        love_this = match(patterns["love"])
        oh_no = match(patterns["oh_no"])

        return helpful, thanks, love_this, oh_no

    @staticmethod
    def _extract_business_response(container: Tag) -> tuple[Optional[str], Optional[str], Optional[str]]:
        """
        Extract a business owner's response if present.
        This is necessarily heuristic; we look for nested blocks that
        appear like a reply with another date and body.
        """
        # Look for nested blocks with "Business Owner" or similar
        response_block = None
        for div in container.find_all("div"):
            if re.search(r"Business Owner|Owner", div.get_text(" ", strip=True), re.I):
                response_block = div
                break

        if not response_block:
            return None, None, None

        # Author name: first bold/strong inside the block
        author_name = None
        strong = response_block.find("strong")
        if strong and strong.get_text(strip=True):
            author_name = strong.get_text(strip=True)

        # Response date: use generic date parsing
        response_date_raw = None
        for time_el in response_block.find_all("time"):
            text = time_el.get_text(strip=True)
            if text:
                response_date_raw = text
                break
        if not response_date_raw:
            for span in response_block.find_all("span"):
                text = span.get_text(strip=True)
                if re.search(
                    r"\b(Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Oct|Nov|Dec)\b", text, re.I
                ):
                    response_date_raw = text
                    break
        response_date_iso = (
            parse_review_date(response_date_raw).isoformat()
            if response_date_raw
            else None
        )

        # Response text: paragraphs under this block
        paragraphs = []
        for p in response_block.find_all("p"):
            text = p.get_text(" ", strip=True)
            if len(text.split()) > 3:
                paragraphs.append(text)
        response_content = " ".join(paragraphs) if paragraphs else None

        return author_name, response_date_iso, response_content

    @staticmethod
    def _build_rating_histogram(reviews: Iterable[Dict[str, Any]]) -> Dict[str, int]:
        counter: Counter[int] = Counter()
        for r in reviews:
            rating = r.get("latest_reviewer_rating")
            if isinstance(rating, int) and 1 <= rating <= 5:
                counter[rating] += 1
        return {f"{stars}stars": counter.get(stars, 0) for stars in range(1, 6)}