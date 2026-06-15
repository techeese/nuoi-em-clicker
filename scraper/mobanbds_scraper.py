#!/usr/bin/env python3
"""
Reusable web scraper for mobanbds.com (and similar real-estate listing sites).

Fetches listing pages, extracts structured fields, and writes them to CSV.

Because listing markup can change over time, the field selectors live in the
SELECTORS config below and can also be overridden on the command line. If a
configured selector finds nothing, a heuristic fallback tries to recover the
most common fields (title, price, area, location, link, image).

Usage examples
--------------
  # Scrape the homepage / default listing path into mobanbds.csv
  python3 mobanbds_scraper.py

  # Scrape a specific listing section, 5 pages, slower to be polite
  python3 mobanbds_scraper.py \
      --url "https://mobanbds.com/nha-dat-ban" \
      --max-pages 5 --delay 2.0 --out nha-dat-ban.csv

  # Point at a saved HTML file instead of the network (handy for testing)
  python3 mobanbds_scraper.py --from-file page.html --out out.csv

Dependencies: requests, beautifulsoup4, lxml  (see requirements.txt)
"""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from dataclasses import dataclass, asdict, fields
from urllib.parse import urljoin, urlparse

try:
    import requests
    from bs4 import BeautifulSoup
except ImportError:  # pragma: no cover - guidance for first-time users
    sys.stderr.write(
        "Missing dependencies. Install them with:\n"
        "    pip3 install -r requirements.txt\n"
        "  (or)  pip3 install requests beautifulsoup4 lxml\n"
    )
    raise


# --------------------------------------------------------------------------- #
# Configuration                                                               #
# --------------------------------------------------------------------------- #

BASE_URL = "https://mobanbds.com/"

DEFAULT_HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0 Safari/537.36"
    ),
    "Accept-Language": "vi,en;q=0.9",
}

# CSS selectors. Tune these to the real DOM after inspecting a live page
# (right-click a listing card in your browser -> Inspect).
#
#   "card"  : selector matching ONE listing container; everything else is
#             searched *within* that container.
#   The remaining selectors are relative to each card. List several
#   comma-separated fallbacks; the first that matches wins.
SELECTORS = {
    "card": "div.product-item, div.item-product, li.product, article, div.listing-item",
    "title": "h3, h2, .product-title, .title, a[title]",
    "price": ".price, .product-price, .gia, [class*='price']",
    "area": ".area, .dien-tich, [class*='area'], [class*='dientich']",
    "location": ".location, .address, .dia-chi, [class*='location'], [class*='address']",
    "link": "a[href]",
    "image": "img",
    # Selector for the "next page" link, used for auto pagination.
    "next_page": "a.next, a[rel='next'], li.next > a, .pagination a[aria-label='Next']",
}

# Output columns, in order.
CSV_FIELDS = ["title", "price", "area", "location", "image", "url", "source_page"]


@dataclass
class Listing:
    title: str = ""
    price: str = ""
    area: str = ""
    location: str = ""
    image: str = ""
    url: str = ""
    source_page: str = ""


# --------------------------------------------------------------------------- #
# Helpers                                                                      #
# --------------------------------------------------------------------------- #

def clean(text: str) -> str:
    """Collapse whitespace and strip."""
    return re.sub(r"\s+", " ", (text or "")).strip()


def select_first(node, selector: str):
    """Return the first element matching any of the comma-separated selectors."""
    if not selector:
        return None
    el = node.select_one(selector)
    return el


def text_of(node, selector: str) -> str:
    el = select_first(node, selector)
    return clean(el.get_text()) if el else ""


def looks_like_price(text: str) -> bool:
    t = text.lower()
    return bool(re.search(r"(tỷ|tỉ|triệu|đồng|vnđ|vnd|\bgiá\b|/m²|/m2)", t)) or bool(
        re.search(r"\d[\d.,]*\s*(tỷ|tỉ|triệu|đ)\b", t)
    )


def looks_like_area(text: str) -> bool:
    return bool(re.search(r"\d[\d.,]*\s*(m²|m2|ha|m\b)", text.lower()))


# --------------------------------------------------------------------------- #
# Extraction                                                                   #
# --------------------------------------------------------------------------- #

def extract_from_card(card, base_url: str, page_url: str) -> Listing:
    listing = Listing(source_page=page_url)

    # Title — prefer a selector, fall back to the most prominent link/heading.
    listing.title = text_of(card, SELECTORS["title"])

    # Link — first anchor with an href, made absolute.
    link_el = select_first(card, SELECTORS["link"])
    if link_el and link_el.get("href"):
        listing.url = urljoin(base_url, link_el["href"])
        if not listing.title:
            listing.title = clean(link_el.get("title") or link_el.get_text())

    listing.price = text_of(card, SELECTORS["price"])
    listing.area = text_of(card, SELECTORS["area"])
    listing.location = text_of(card, SELECTORS["location"])

    img_el = select_first(card, SELECTORS["image"])
    if img_el:
        src = img_el.get("src") or img_el.get("data-src") or img_el.get("data-original")
        if src:
            listing.image = urljoin(base_url, src)

    # Heuristic fallback: scan all text within the card if fields are empty.
    if not (listing.price and listing.area):
        chunks = [clean(s) for s in card.stripped_strings]
        for c in chunks:
            if not listing.price and looks_like_price(c):
                listing.price = c
            elif not listing.area and looks_like_area(c):
                listing.area = c

    return listing


def parse_page(html: str, base_url: str, page_url: str) -> list[Listing]:
    soup = BeautifulSoup(html, "lxml")

    cards = soup.select(SELECTORS["card"])
    listings: list[Listing] = []

    if cards:
        for card in cards:
            listing = extract_from_card(card, base_url, page_url)
            # Keep only cards that yielded at least a title or a link.
            if listing.title or listing.url:
                listings.append(listing)

    # Heuristic fallback if no cards matched: group by anchors that have images.
    if not listings:
        for a in soup.select("a[href]"):
            if not a.find("img"):
                continue
            container = a.find_parent() or a
            listing = extract_from_card(container, base_url, page_url)
            if listing.title or listing.url:
                listings.append(listing)

    return listings


def find_next_page(html: str, base_url: str) -> str | None:
    soup = BeautifulSoup(html, "lxml")
    el = soup.select_one(SELECTORS["next_page"])
    if el and el.get("href"):
        return urljoin(base_url, el["href"])
    return None


# --------------------------------------------------------------------------- #
# Fetching                                                                     #
# --------------------------------------------------------------------------- #

def fetch(session: requests.Session, url: str, timeout: float) -> str:
    resp = session.get(url, timeout=timeout)
    resp.raise_for_status()
    # Let requests/bs4 sort out encoding; mobanbds is UTF-8 Vietnamese.
    resp.encoding = resp.apparent_encoding or resp.encoding
    return resp.text


def scrape(args) -> list[Listing]:
    base = args.url
    all_listings: list[Listing] = []
    seen_urls: set[str] = set()

    if args.from_file:
        with open(args.from_file, "r", encoding="utf-8", errors="replace") as fh:
            html = fh.read()
        return dedupe(parse_page(html, base, args.from_file), seen_urls)

    session = requests.Session()
    session.headers.update(DEFAULT_HEADERS)

    url = base
    for page_num in range(1, args.max_pages + 1):
        try:
            html = fetch(session, url, args.timeout)
        except requests.RequestException as exc:
            sys.stderr.write(f"[warn] failed to fetch {url}: {exc}\n")
            break

        page_listings = parse_page(html, base, url)
        new = dedupe(page_listings, seen_urls)
        all_listings.extend(new)
        sys.stderr.write(
            f"[info] page {page_num}: {len(page_listings)} found, "
            f"{len(new)} new ({url})\n"
        )

        if not new:
            # No new content — likely past the last page.
            break

        next_url = find_next_page(html, base)
        if not next_url or next_url == url:
            break
        url = next_url

        if page_num < args.max_pages:
            time.sleep(args.delay)

    return all_listings


def dedupe(listings: list[Listing], seen: set[str]) -> list[Listing]:
    out = []
    for l in listings:
        key = l.url or (l.title + "|" + l.price)
        if key in seen:
            continue
        seen.add(key)
        out.append(l)
    return out


# --------------------------------------------------------------------------- #
# Output                                                                        #
# --------------------------------------------------------------------------- #

def write_csv(listings: list[Listing], path: str) -> None:
    with open(path, "w", newline="", encoding="utf-8-sig") as fh:
        writer = csv.DictWriter(fh, fieldnames=CSV_FIELDS)
        writer.writeheader()
        for l in listings:
            row = {k: v for k, v in asdict(l).items() if k in CSV_FIELDS}
            writer.writerow(row)


# --------------------------------------------------------------------------- #
# CLI                                                                          #
# --------------------------------------------------------------------------- #

def parse_args(argv=None):
    p = argparse.ArgumentParser(
        description="Scrape mobanbds.com real-estate listings to CSV.",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument("--url", default=BASE_URL, help="Listing page URL to start from.")
    p.add_argument("--out", default="mobanbds.csv", help="Output CSV path.")
    p.add_argument("--max-pages", type=int, default=10, help="Max pages to follow.")
    p.add_argument("--delay", type=float, default=1.0, help="Seconds between requests.")
    p.add_argument("--timeout", type=float, default=30.0, help="Per-request timeout (s).")
    p.add_argument(
        "--from-file",
        help="Parse a local saved HTML file instead of fetching over the network.",
    )
    # Allow overriding any selector from the CLI, e.g. --sel-card "div.card"
    for name in SELECTORS:
        p.add_argument(
            f"--sel-{name.replace('_', '-')}",
            dest=f"sel_{name}",
            help=f"Override the '{name}' CSS selector.",
        )
    return p.parse_args(argv)


def apply_selector_overrides(args) -> None:
    for name in SELECTORS:
        override = getattr(args, f"sel_{name}", None)
        if override:
            SELECTORS[name] = override


def main(argv=None) -> int:
    args = parse_args(argv)
    apply_selector_overrides(args)

    listings = scrape(args)
    write_csv(listings, args.out)

    sys.stderr.write(f"[done] wrote {len(listings)} listings to {args.out}\n")
    if not listings:
        sys.stderr.write(
            "[hint] 0 listings parsed. The site's markup likely differs from the "
            "defaults.\n"
            "       Inspect a listing card in your browser and pass the right\n"
            "       selectors, e.g. --sel-card 'div.your-card' --sel-title 'h3 a'\n"
            "       Or save the page and test with --from-file page.html\n"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
