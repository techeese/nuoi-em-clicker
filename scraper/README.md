# mobanbds.com scraper

A small, reusable scraper that pulls real-estate listings from
[mobanbds.com](https://mobanbds.com/) (or similar listing sites) and exports
them to CSV.

> **Note:** This must be run somewhere that can actually reach the site. The
> Claude Code web sandbox blocks `mobanbds.com` via its network policy
> (`403 host_not_allowed`), so run it on your own machine.

## Install

```bash
cd scraper
pip3 install -r requirements.txt
```

## Run

```bash
# Default: start at the homepage, follow up to 10 pages, write mobanbds.csv
python3 mobanbds_scraper.py

# A specific section, 5 pages, 2s between requests, custom output
python3 mobanbds_scraper.py \
    --url "https://mobanbds.com/nha-dat-ban" \
    --max-pages 5 --delay 2.0 --out nha-dat-ban.csv

# Test against a saved HTML file (no network needed)
python3 mobanbds_scraper.py --from-file page.html --out out.csv
```

The CSV columns are: `title, price, area, location, image, url, source_page`
(UTF-8 with BOM so Vietnamese text opens cleanly in Excel).

## Tuning the selectors

Listing markup varies and changes over time. The script ships with sensible
defaults plus a heuristic fallback, but for clean results you should confirm
the real CSS selectors:

1. Open a listing page in your browser.
2. Right-click a listing card → **Inspect**.
3. Note the container class and the inner elements for title/price/area/etc.
4. Pass them in, e.g.:

```bash
python3 mobanbds_scraper.py \
    --sel-card "div.product-item" \
    --sel-title "h3 a" \
    --sel-price ".price" \
    --sel-area ".dien-tich" \
    --sel-location ".dia-chi" \
    --sel-next-page "a.next"
```

You can also edit the `SELECTORS` dict at the top of `mobanbds_scraper.py`
to make your choices the new defaults.

If you get `0 listings`, the selectors don't match the page — save the HTML
and iterate with `--from-file`.

## Be a good citizen

- Check the site's `robots.txt` and terms of service before scraping.
- Keep `--delay` reasonable so you don't hammer the server.
- Only scrape data you're authorized to use.
