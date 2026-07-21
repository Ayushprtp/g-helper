"""
VTU (vtu.ac.in) website scraper.

Scrapes:
  - Latest announcements / news ticker
  - Examination notifications (paginated)
  - Academic / affiliation notifications (paginated)
  - Results page links
  - Navigation structure

Outputs one JSON file per section in ./output/.
Run:
    python3 scraper.py [--pages N] [--output OUTPUT_DIR]
"""

import argparse
import csv
import json
import os
import re
import sys
import time
from datetime import datetime
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

BASE_URL = "https://vtu.ac.in"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}
REQUEST_DELAY = 1.0  # seconds between requests


# ---------------------------------------------------------------------------
# Utility helpers
# ---------------------------------------------------------------------------

def get(url: str, retries: int = 3) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=15)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            print(f"  [WARN] {url} attempt {attempt + 1}/{retries}: {exc}")
            if attempt < retries - 1:
                time.sleep(2 ** attempt)
    return None


def soup(response: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(response.text, "lxml")


def save_json(data, path: str):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    print(f"  -> saved {path}  ({len(data)} records)")


def save_csv(data: list[dict], path: str):
    if not data:
        return
    os.makedirs(os.path.dirname(path), exist_ok=True)
    # Collect all keys across all records to handle heterogeneous rows
    seen_keys: dict[str, None] = {}
    for row in data:
        seen_keys.update(dict.fromkeys(row.keys()))
    keys = list(seen_keys)
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=keys, extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    print(f"  -> saved {path}")


# ---------------------------------------------------------------------------
# Scrapers
# ---------------------------------------------------------------------------

def scrape_homepage() -> dict:
    """Scrape navigation links and news ticker from the homepage."""
    print("[1/5] Scraping homepage …")
    r = get(BASE_URL)
    if not r:
        return {}

    pg = soup(r)
    result = {"scraped_at": datetime.utcnow().isoformat(), "nav_links": [], "ticker_links": []}

    # Navigation <a> tags in the main menu
    nav = pg.find("nav") or pg.find("div", class_=re.compile(r"nav|menu", re.I))
    if nav:
        for a in nav.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True)
            if href and text:
                result["nav_links"].append({"text": text, "url": urljoin(BASE_URL, href)})

    # News ticker / marquee links
    for tag in pg.find_all(["marquee", "div"], class_=re.compile(r"ticker|scroll|marquee|news", re.I)):
        for a in tag.find_all("a", href=True):
            href = a["href"].strip()
            text = a.get_text(strip=True)
            if href and text:
                result["ticker_links"].append({"text": text, "url": urljoin(BASE_URL, href)})

    # Also pick up post-style links on front page
    for a in pg.find_all("a", href=re.compile(r"/\d{4}/\d{2}/\d+")):
        href = a["href"].strip()
        text = a.get_text(strip=True)
        if text:
            result["ticker_links"].append({"text": text, "url": urljoin(BASE_URL, href)})

    # Deduplicate while preserving order
    seen: set[str] = set()
    for section in ("nav_links", "ticker_links"):
        deduped = []
        for item in result[section]:
            if item["url"] not in seen:
                seen.add(item["url"])
                deduped.append(item)
        result[section] = deduped

    return result


def _scrape_category_pages(category_url: str, max_pages: int, label: str) -> list[dict]:
    """Generic scraper for VTU WordPress category archive pages."""
    items: list[dict] = []
    url = category_url
    page_num = 0

    while url and page_num < max_pages:
        page_num += 1
        print(f"  {label} page {page_num}: {url}")
        r = get(url)
        if not r:
            break
        pg = soup(r)

        # WordPress article list
        articles = pg.find_all("article") or pg.find_all("div", class_=re.compile(r"post|entry|item", re.I))
        found_on_page = 0
        for art in articles:
            title_tag = art.find(["h1", "h2", "h3"], class_=re.compile(r"title|entry", re.I)) or art.find(["h1", "h2", "h3"])
            a_tag = title_tag.find("a") if title_tag else None
            if not a_tag:
                a_tag = art.find("a", href=True)
            if not a_tag:
                continue

            title = a_tag.get_text(strip=True)
            post_url = urljoin(BASE_URL, a_tag["href"])

            # Date
            date_tag = art.find(["time", "span", "div"], class_=re.compile(r"date|time|published|posted", re.I))
            date_str = ""
            if date_tag:
                date_str = date_tag.get("datetime", "") or date_tag.get_text(strip=True)

            # Category / tags
            cat_tag = art.find(["span", "a"], class_=re.compile(r"cat|tag|label", re.I))
            category = cat_tag.get_text(strip=True) if cat_tag else ""

            items.append({"title": title, "url": post_url, "date": date_str, "category": category})
            found_on_page += 1

        if found_on_page == 0:
            print(f"  No articles found on page {page_num}, stopping.")
            break

        # Next page link
        next_a = pg.find("a", class_=re.compile(r"next", re.I)) or pg.find("a", string=re.compile(r"next|»|›", re.I))
        if next_a and next_a.get("href"):
            url = urljoin(BASE_URL, next_a["href"])
            time.sleep(REQUEST_DELAY)
        else:
            break

    return items


def scrape_examination_notifications(max_pages: int) -> list[dict]:
    print(f"[2/5] Scraping examination notifications (up to {max_pages} pages) …")
    return _scrape_category_pages(
        "https://vtu.ac.in/en/category/examination/",
        max_pages,
        "Exam"
    )


def scrape_affiliation_notifications(max_pages: int) -> list[dict]:
    print(f"[3/5] Scraping affiliation/academic notifications (up to {max_pages} pages) …")
    return _scrape_category_pages(
        "https://vtu.ac.in/en/category/affiliation/",
        max_pages,
        "Affiliation"
    )


def scrape_results() -> list[dict]:
    """Scrape the results portal index for available result links."""
    print("[4/5] Scraping results portal …")
    # Try HTTPS first (ignore self-signed cert), fall back to HTTP; portal is often slow/down
    import urllib3
    urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)
    for url in ("https://results.vtu.ac.in/", "http://results.vtu.ac.in/"):
        try:
            r = requests.get(url, headers=HEADERS, timeout=8, verify=False)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [WARN] results portal ({url}): {exc}")
            continue

        pg = soup(r)
        results = []
        seen: set[str] = set()
        for a in pg.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"].strip()
            full = urljoin(url, href)
            if text and href and not href.startswith("#") and full not in seen:
                seen.add(full)
                results.append({"title": text, "url": full})
        return results

    print("  [INFO] Results portal unreachable — skipping.")
    return []


def _parse_college_page(url: str, region: str) -> list[dict]:
    r = get(url)
    if not r:
        return []
    pg = soup(r)
    colleges: list[dict] = []

    # Tables
    for table in pg.find_all("table"):
        headers = [th.get_text(strip=True) for th in table.find_all("th")]
        for row in table.find_all("tr")[1:]:
            cells = [td.get_text(strip=True) for td in row.find_all("td")]
            if cells:
                record = dict(zip(headers, cells)) if headers else {"data": " | ".join(cells)}
                record["region"] = region
                link = row.find("a", href=True)
                if link:
                    record["url"] = urljoin(BASE_URL, link["href"])
                colleges.append(record)

    # Fallback: ordered/unordered list
    if not colleges:
        for li in pg.find_all("li"):
            text = li.get_text(strip=True)
            a = li.find("a", href=True)
            if text and len(text) > 5:
                colleges.append({
                    "name": text,
                    "region": region,
                    "url": urljoin(BASE_URL, a["href"]) if a else "",
                })

    return colleges


def scrape_affiliated_colleges() -> list[dict]:
    """Scrape VTU affiliated colleges by region."""
    print("[5/5] Scraping affiliated colleges by region …")
    regions = {
        "Bengaluru": "https://vtu.ac.in/en/bengaluru-affiliated-colleges/",
        "Belagavi":  "https://vtu.ac.in/en/belagavi-affiliated-colleges/",
        "Kalaburagi":"https://vtu.ac.in/en/kalburagi-affilated-colleges/",
        "Mysuru":    "https://vtu.ac.in/en/mysuru-affiliated-colleges/",
        "Autonomous":"https://vtu.ac.in/en/autonomous-colleges/",
    }
    all_colleges: list[dict] = []
    for region, url in regions.items():
        print(f"  Region: {region}")
        cols = _parse_college_page(url, region)
        print(f"    found {len(cols)} entries")
        all_colleges.extend(cols)
        time.sleep(REQUEST_DELAY)
    return all_colleges


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape vtu.ac.in")
    parser.add_argument("--pages", type=int, default=3, help="Max category archive pages to fetch (default: 3)")
    parser.add_argument("--output", default="output", help="Output directory (default: ./output)")
    args = parser.parse_args()

    out = args.output
    os.makedirs(out, exist_ok=True)

    # 1. Homepage
    homepage = scrape_homepage()
    save_json(homepage, f"{out}/homepage.json")
    time.sleep(REQUEST_DELAY)

    # 2. Examination notifications
    exam_notifs = scrape_examination_notifications(args.pages)
    save_json(exam_notifs, f"{out}/examination_notifications.json")
    save_csv(exam_notifs, f"{out}/examination_notifications.csv")
    time.sleep(REQUEST_DELAY)

    # 3. Affiliation notifications
    affil_notifs = scrape_affiliation_notifications(args.pages)
    save_json(affil_notifs, f"{out}/affiliation_notifications.json")
    save_csv(affil_notifs, f"{out}/affiliation_notifications.csv")
    time.sleep(REQUEST_DELAY)

    # 4. Results portal
    results = scrape_results()
    save_json(results, f"{out}/results_portal.json")
    save_csv(results, f"{out}/results_portal.csv")
    time.sleep(REQUEST_DELAY)

    # 5. Affiliated colleges
    colleges = scrape_affiliated_colleges()
    save_json(colleges, f"{out}/affiliated_colleges.json")
    save_csv(colleges, f"{out}/affiliated_colleges.csv")

    # Summary
    print("\n=== Summary ===")
    print(f"  Nav links:                  {len(homepage.get('nav_links', []))}")
    print(f"  News ticker links:          {len(homepage.get('ticker_links', []))}")
    print(f"  Examination notifications:  {len(exam_notifs)}")
    print(f"  Affiliation notifications:  {len(affil_notifs)}")
    print(f"  Results portal links:       {len(results)}")
    print(f"  Affiliated colleges:        {len(colleges)}")
    print(f"\nAll output written to: {os.path.abspath(out)}/")


if __name__ == "__main__":
    main()
