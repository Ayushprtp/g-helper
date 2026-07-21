"""
VTU (vtu.ac.in) website scraper — full coverage.

Categories scraped:
  - examination          (101 pages ~1010 posts)
  - examination/timetable
  - affiliation
  - administration       (circulars & notifications)
  - vtu-regulation
  - ph-d-and-m-sc-engg
  - workshop-seminar-conference-fdp
  - tenders

Static sections:
  - Homepage nav + news ticker
  - Affiliated colleges (5 regions)
  - Results portal links

Usage:
    python3 scraper.py                  # all categories, all pages
    python3 scraper.py --pages 5        # cap each category at 5 pages
    python3 scraper.py --output mydata  # custom output dir
    python3 scraper.py --delay 0.5      # faster (be polite to the server)
"""

import argparse
import csv
import json
import os
import re
import time
import urllib3
from datetime import datetime
from urllib.parse import urljoin

import requests
from bs4 import BeautifulSoup

urllib3.disable_warnings(urllib3.exceptions.InsecureRequestWarning)

BASE_URL = "https://vtu.ac.in"
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (X11; Linux x86_64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/124.0 Safari/537.36"
    )
}

CATEGORIES = {
    "examination":              "https://vtu.ac.in/en/category/examination/",
    "examination_timetable":    "https://vtu.ac.in/en/category/examination/time-table/",
    "affiliation":              "https://vtu.ac.in/en/category/affiliation/",
    "administration":           "https://vtu.ac.in/en/category/administration/",
    "vtu_regulation":           "https://vtu.ac.in/en/category/vtu-regulation/",
    "phd_msc_engg":             "https://vtu.ac.in/en/category/ph-d-and-m-sc-engg/",
    "workshop_seminar_fdp":     "https://vtu.ac.in/category/workshop-seminar-conference-fdp/",
    "tenders":                  "https://vtu.ac.in/en/category/tenders/",
}

COLLEGE_REGIONS = {
    "Bengaluru":  "https://vtu.ac.in/en/bengaluru-affiliated-colleges/",
    "Belagavi":   "https://vtu.ac.in/en/belagavi-affiliated-colleges/",
    "Kalaburagi": "https://vtu.ac.in/en/kalburagi-affilated-colleges/",
    "Mysuru":     "https://vtu.ac.in/en/mysuru-affiliated-colleges/",
    "Autonomous": "https://vtu.ac.in/en/autonomous-colleges/",
}


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def get(url: str, retries: int = 3, timeout: int = 15, verify: bool = True) -> requests.Response | None:
    for attempt in range(retries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=timeout, verify=verify)
            r.raise_for_status()
            return r
        except requests.RequestException as exc:
            wait = 2 ** attempt
            print(f"  [WARN] attempt {attempt+1}/{retries} — {url}: {exc}")
            if attempt < retries - 1:
                time.sleep(wait)
    return None


def make_soup(response: requests.Response) -> BeautifulSoup:
    return BeautifulSoup(response.text, "lxml")


def detect_total_pages(pg: BeautifulSoup, base_url: str) -> int:
    """Return the highest page number found in pagination, or 1 if not found."""
    pagination = pg.find(["nav", "div"], class_=re.compile(r"paginat|page-nav|wp-pagenavi|nav-links", re.I))
    if not pagination:
        pagination = pg
    max_page = 1
    for a in pagination.find_all("a", href=True):
        m = re.search(r"/page/(\d+)/?", a["href"])
        if m:
            max_page = max(max_page, int(m.group(1)))
        # also check link text
        text = a.get_text(strip=True)
        if text.isdigit():
            max_page = max(max_page, int(text))
    return max_page


def save_json(data, path: str):
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    with open(path, "w", encoding="utf-8") as fh:
        json.dump(data, fh, ensure_ascii=False, indent=2)
    count = len(data) if isinstance(data, (list, dict)) else "?"
    print(f"  -> {path}  ({count} records)")


def save_csv(data: list[dict], path: str):
    if not data:
        return
    os.makedirs(os.path.dirname(path) or ".", exist_ok=True)
    all_keys: dict[str, None] = {}
    for row in data:
        all_keys.update(dict.fromkeys(row.keys()))
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=list(all_keys), extrasaction="ignore")
        writer.writeheader()
        writer.writerows(data)
    print(f"  -> {path}")


# ---------------------------------------------------------------------------
# Section scrapers
# ---------------------------------------------------------------------------

def scrape_homepage() -> dict:
    print("\n[homepage] Scraping nav + ticker …")
    r = get(BASE_URL)
    if not r:
        return {}

    pg = make_soup(r)
    result: dict = {"scraped_at": datetime.utcnow().isoformat(), "nav_links": [], "ticker_links": []}

    nav = pg.find("nav") or pg.find("div", class_=re.compile(r"nav|menu", re.I))
    if nav:
        for a in nav.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"].strip()
            if text and href:
                result["nav_links"].append({"text": text, "url": urljoin(BASE_URL, href)})

    for tag in pg.find_all(["marquee", "div"], class_=re.compile(r"ticker|scroll|marquee|news", re.I)):
        for a in tag.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"].strip()
            if text and href:
                result["ticker_links"].append({"text": text, "url": urljoin(BASE_URL, href)})

    for a in pg.find_all("a", href=re.compile(r"/\d{4}/\d{2}/\d+")):
        text = a.get_text(strip=True)
        href = a["href"].strip()
        if text:
            result["ticker_links"].append({"text": text, "url": urljoin(BASE_URL, href)})

    for key in ("nav_links", "ticker_links"):
        seen: set[str] = set()
        deduped = []
        for item in result[key]:
            if item["url"] not in seen:
                seen.add(item["url"])
                deduped.append(item)
        result[key] = deduped

    print(f"  nav: {len(result['nav_links'])}  ticker: {len(result['ticker_links'])}")
    return result


def _parse_articles(pg: BeautifulSoup, category_label: str) -> list[dict]:
    items = []
    articles = pg.find_all("article") or pg.find_all("div", class_=re.compile(r"\bpost\b|\bentry\b", re.I))
    for art in articles:
        h = art.find(["h1", "h2", "h3"])
        a_tag = h.find("a") if h else art.find("a", href=True)
        if not a_tag:
            continue
        title = a_tag.get_text(strip=True)
        url   = urljoin(BASE_URL, a_tag["href"])

        date_tag = art.find(["time", "span", "abbr"], class_=re.compile(r"date|time|published|posted", re.I))
        date_str = ""
        if date_tag:
            date_str = date_tag.get("datetime", "") or date_tag.get_text(strip=True)

        cat_tag = art.find(["span", "a"], class_=re.compile(r"\bcat\b|category", re.I))
        cat_str = cat_tag.get_text(strip=True) if cat_tag else category_label

        items.append({"title": title, "url": url, "date": date_str, "category": cat_str})
    return items


def scrape_category(name: str, base_url: str, max_pages: int, delay: float) -> list[dict]:
    print(f"\n[{name}] {base_url}")
    all_items: list[dict] = []
    seen_urls: set[str] = set()
    total_pages: int | None = None
    page_num = 0
    url = base_url

    while url:
        page_num += 1
        if max_pages and page_num > max_pages:
            print(f"  Reached --pages cap ({max_pages}), stopping.")
            break

        progress = f"{page_num}/{total_pages}" if total_pages else str(page_num)
        print(f"  page {progress}: {url}", end="", flush=True)

        r = get(url)
        if not r:
            print(" [FAILED]")
            break

        pg = make_soup(r)

        if total_pages is None:
            total_pages = detect_total_pages(pg, base_url)
            print(f"  (total pages detected: {total_pages})", end="")

        items = _parse_articles(pg, name)
        new_items = [i for i in items if i["url"] not in seen_urls]
        for i in new_items:
            seen_urls.add(i["url"])
        all_items.extend(new_items)
        print(f"  +{len(new_items)} (total {len(all_items)})")

        if not items:
            print(f"  No articles on page {page_num}, stopping.")
            break

        next_a = (
            pg.find("a", class_=re.compile(r"\bnext\b", re.I))
            or pg.find("a", string=re.compile(r"next|»|›", re.I))
        )
        if next_a and next_a.get("href"):
            url = urljoin(BASE_URL, next_a["href"])
            time.sleep(delay)
        else:
            break

    print(f"  [{name}] done — {len(all_items)} posts across {page_num} page(s)")
    return all_items


def scrape_results(delay: float) -> list[dict]:
    print("\n[results_portal] Scraping …")
    for url in ("https://results.vtu.ac.in/", "http://results.vtu.ac.in/"):
        try:
            r = requests.get(url, headers=HEADERS, timeout=8, verify=False)
            r.raise_for_status()
        except requests.RequestException as exc:
            print(f"  [WARN] {url}: {exc}")
            continue
        pg = make_soup(r)
        results, seen = [], set()
        for a in pg.find_all("a", href=True):
            text = a.get_text(strip=True)
            href = a["href"].strip()
            full = urljoin(url, href)
            if text and not href.startswith("#") and full not in seen:
                seen.add(full)
                results.append({"title": text, "url": full})
        print(f"  found {len(results)} links")
        return results
    print("  [INFO] Results portal unreachable — skipping.")
    return []


def scrape_affiliated_colleges(delay: float) -> list[dict]:
    print("\n[affiliated_colleges] Scraping by region …")
    all_colleges: list[dict] = []

    for region, url in COLLEGE_REGIONS.items():
        r = get(url)
        if not r:
            print(f"  [{region}] FAILED")
            continue
        pg = make_soup(r)
        colleges: list[dict] = []

        for table in pg.find_all("table"):
            headers = [th.get_text(strip=True) for th in table.find_all("th")]
            for row in table.find_all("tr")[1:]:
                cells = [td.get_text(strip=True) for td in row.find_all("td")]
                if not cells:
                    continue
                record = dict(zip(headers, cells)) if headers else {"data": " | ".join(cells)}
                record["region"] = region
                link = row.find("a", href=True)
                if link:
                    record["website"] = urljoin(BASE_URL, link["href"])
                colleges.append(record)

        if not colleges:
            for li in pg.find_all("li"):
                text = li.get_text(strip=True)
                a = li.find("a", href=True)
                if text and len(text) > 5:
                    colleges.append({"name": text, "region": region, "url": urljoin(BASE_URL, a["href"]) if a else ""})

        print(f"  [{region}] {len(colleges)} colleges")
        all_colleges.extend(colleges)
        time.sleep(delay)

    return all_colleges


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    parser = argparse.ArgumentParser(description="Scrape all pages of vtu.ac.in")
    parser.add_argument(
        "--pages", type=int, default=0,
        help="Max pages per category (0 = no limit, scrape all)",
    )
    parser.add_argument("--output", default="output", help="Output directory (default: ./output)")
    parser.add_argument("--delay", type=float, default=1.0, help="Seconds between requests (default: 1.0)")
    args = parser.parse_args()

    out    = args.output
    delay  = args.delay
    max_pg = args.pages or 10_000  # effectively unlimited

    os.makedirs(out, exist_ok=True)
    print(f"Output: {os.path.abspath(out)}/")
    print(f"Pages cap: {'unlimited' if args.pages == 0 else args.pages}  |  Delay: {delay}s\n")

    summary: dict[str, int] = {}

    # 1. Homepage
    homepage = scrape_homepage()
    save_json(homepage, f"{out}/homepage.json")
    summary["nav_links"]    = len(homepage.get("nav_links", []))
    summary["ticker_links"] = len(homepage.get("ticker_links", []))
    time.sleep(delay)

    # 2. All notification categories
    all_notifications: list[dict] = []
    for cat_name, cat_url in CATEGORIES.items():
        items = scrape_category(cat_name, cat_url, max_pg, delay)
        save_json(items, f"{out}/{cat_name}.json")
        save_csv(items,  f"{out}/{cat_name}.csv")
        summary[cat_name] = len(items)
        all_notifications.extend(items)
        time.sleep(delay)

    # Combined file for convenience
    save_json(all_notifications, f"{out}/all_notifications.json")
    save_csv(all_notifications,  f"{out}/all_notifications.csv")
    summary["all_notifications"] = len(all_notifications)

    # 3. Results portal
    results = scrape_results(delay)
    save_json(results, f"{out}/results_portal.json")
    if results:
        save_csv(results, f"{out}/results_portal.csv")
    summary["results_portal"] = len(results)
    time.sleep(delay)

    # 4. Affiliated colleges
    colleges = scrape_affiliated_colleges(delay)
    save_json(colleges, f"{out}/affiliated_colleges.json")
    save_csv(colleges,  f"{out}/affiliated_colleges.csv")
    summary["affiliated_colleges"] = len(colleges)

    # Summary
    print("\n" + "=" * 60)
    print("SUMMARY")
    print("=" * 60)
    for key, count in summary.items():
        print(f"  {key:<35} {count:>6}")
    print("=" * 60)
    print(f"\nAll output: {os.path.abspath(out)}/")


if __name__ == "__main__":
    main()
