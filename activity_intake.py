"""Start with a dated Yelp review, then verify the painter's active license."""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from tempfile import NamedTemporaryFile
from urllib.parse import urlsplit

from bs4 import BeautifulSoup

import business_profile
import research
import storage


def recent_profile(url, today):
    """Read one business's dated reviews and published contact. Ratings do not determine activity."""
    page = research.directory_page(url, today)
    if page.get("status") != 200 or page.get("url", "").rstrip("/") != url.rstrip("/"):
        return {}
    dates = [
        (review["date"], review["source_url"])
        for review in page.get("yahoo_reviews", [])
        if business_profile.recent_yelp_review(review, today)
    ]
    if not dates:
        return {}
    for node in page.get("business_nodes", []):
        address = node.get("address")
        canonical = node.get("url") or node.get("@id")
        if (
            not isinstance(address, dict)
            or not isinstance(canonical, str)
            or canonical.rstrip("/") != url.rstrip("/")
        ):
            continue
        identity = (node.get("telephone"), node.get("name"), address.get("addressLocality"))
        if not all(isinstance(field, str) and field.strip() for field in identity):
            continue
        posted, source = max(dates)
        website = next(
            (url for url in research.published_website_links(page) if not research.listing_source(url)), ""
        )
        return {
            "name": node["name"],
            "city": address["addressLocality"],
            "phone": node["telephone"],
            "activity": {
                "type": "customer_review",
                "platform": "Yelp",
                "date": posted,
                "source_url": source,
                "listing_url": url,
            },
            "listed_website": bool(website),
            "website_url": website,
            "contact_estimates": bool(research.ESTIMATE_BY_PHONE_OR_MESSAGE.search(str(node.get("description") or ""))),
            "gallery": len(page.get("listing_photos", [])) >= 2,
        }
    return {}


def public_profiles(today, report, starts=None):
    # These daily snapshots store contact identity and dates. Review text and photos
    # are read from the specific business again during qualification.
    filename = (
        "reviewed-painter-later-pages.json" if starts is not None else "reviewed-painter-profiles.json"
    )
    cache = research.CACHE.with_name(filename)
    first = starts if starts is not None else dict.fromkeys(research.CITIES, 1)
    try:
        saved = json.loads(cache.read_text())
        if saved["date"] == today.isoformat() and saved.get("version") == 3 and saved.get("starts") == first:
            return saved
    except (OSError, ValueError, KeyError):
        pass

    def city_window(city):
        rows = []
        page = first[city]
        for _ in range(3):
            cards, page = research.catalog_page(city, today, page)
            rows.extend(cards)
            if not page:
                break
        return city, rows, page

    with ThreadPoolExecutor(max_workers=6) as pool:
        windows = list(pool.map(city_window, first))
        cards = {row["url"]: row for _, rows, _ in windows for row in rows}
        urls = [url for url, row in cards.items() if row["reviewed"] and row["phone"]]
        report(
            f"Found {len(urls)} public painter listings with customer reviews and a phone. Reading dated Yelp reviews."
        )
        profiles = [
            profile for profile in pool.map(lambda url: recent_profile(url, today), urls) if profile
        ]
    limited = bool(research.directory_retry_after())
    saved = {
        "version": 3,
        "date": today.isoformat(),
        "source_limited": limited,
        "starts": first,
        "profiles": profiles,
        "next_pages": {city: page for city, _, page in windows if page},
        "counts": {
            "source_listings": len(cards),
            "reviewed_listings_with_phone": len(urls),
            "recent_profiles": len(profiles),
        },
    }
    if cards and not limited:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", dir=cache.parent, delete=False) as stream:
            json.dump(saved, stream)
        Path(stream.name).replace(cache)
    return saved


@lru_cache(maxsize=256)
def listed_site_signal(url, today):
    """A listed site can earn a closer look. The qualifier independently verifies any claim."""
    limit = 150_000
    final, status, mime, body = research.read_public(url, "text/html", limit)
    if research.host(final) != research.host(url):
        return ""
    if status in {404, 410}:
        return "missing page"
    if status == 200 and "html" in mime and not urlsplit(final).path.strip("/") and len(body) <= limit:
        if b"soon" in body.lower() and research.launch_heading(BeautifulSoup(body, "html.parser")):
            return "launch page"
    return ""


def select(profiles, licenses, seen, limit):
    by_phone = defaultdict(list)
    for lead in licenses:
        for phone in research.phones(lead["phone"]):
            by_phone[phone].append(lead)
    matched = {}
    for profile in profiles:
        candidates = {
            l["id"]: l
            for phone in research.phones(profile["phone"])
            for l in by_phone[phone]
            if research.words(l["city"]) == research.words(profile["city"])
            and research.same_trade_name(l["name"], profile["name"])
        }
        if len(candidates) != 1:
            continue
        lead = next(iter(candidates.values()))
        mark = profile["activity"]
        if lead["id"] not in matched or mark["date"] > matched[lead["id"]]["activity"]["date"]:
            matched[lead["id"]] = {
                **lead,
                "directory_url": mark["listing_url"],
                "activity": mark,
                "listed_website": profile["listed_website"],
                "website_url": profile.get("website_url", ""),
                "contact_estimates": profile.get("contact_estimates", False),
                "gallery": profile.get("gallery", False),
            }
    unseen = [l for i, l in matched.items() if i not in seen]
    # Only probe identity-matched businesses, newest first, before buying any web searches.
    unseen.sort(key=lambda lead: (-date.fromisoformat(lead["activity"]["date"]).toordinal(), lead["id"]))
    linked = [lead for lead in unseen if lead["website_url"]][:24]
    with ThreadPoolExecutor(max_workers=6) as pool:
        today = date.today()
        probes = pool.map(lambda lead: listed_site_signal(lead["website_url"], today), linked)
        for lead, signal in zip(linked, probes):
            lead["listed_site_signal"] = signal
            lead["missing_link"] = signal == "missing page"
    for lead in unseen:
        lead["priority"] = not lead["listed_website"] or lead.get("missing_link", False) or (
            lead.get("listed_site_signal") == "launch page" and lead["gallery"]
        )
    # Stable sorting preserves review recency and the license-ID tiebreak within each group.
    unseen.sort(
        key=lambda lead: (
            not lead["priority"], not lead.get("missing_link", False), lead["listed_website"],
            not lead["contact_estimates"], not lead["gallery"],
        )
    )
    return unseen[:limit], {
        "unique_active_license_matches": len(matched),
        "previously_seen": len(matched) - len(unseen),
        "eligible_unseen": len(unseen),
        "priority_candidates": sum(lead["priority"] for lead in unseen),
        "unseen_with_launch_page_and_gallery": sum(lead.get("listed_site_signal") == "launch page" and lead["gallery"] for lead in unseen),
        "unseen_with_missing_link": sum(bool(lead.get("missing_link")) for lead in unseen),
        "unseen_without_listed_website": sum(not lead["listed_website"] for lead in unseen),
    }


def discover(limit, seen, progress=None, *, audit=None):
    report = progress or (lambda message: None)
    today = date.today()
    report(
        "Finding painters with a customer review on Yelp in the past year, via public Yahoo Local listings."
    )
    storage.initialize()
    inventory = json.loads(storage.setting("activity_inventory", "{}"))
    # A later page or a temporarily unavailable directory need not displace today's earlier matches.
    profiles = inventory.get("profiles", {}) if inventory.get("date") == today.isoformat() else {}
    first = public_profiles(today, report)
    profiles.update((p["activity"]["listing_url"], p) for p in first["profiles"])
    counts = dict(first["counts"])
    counts["recent_profiles"] = len(profiles)
    limited = first.get("source_limited", False)
    report(
        f"{len(profiles)} profiles found today have a dated review in the last 365 days. Checking active C-33 licenses."
    )
    licenses = research.discover(100_000, set(), report)
    selected, checks = select(profiles.values(), licenses, seen, limit)
    if checks["priority_candidates"] < limit:
        location = json.loads(storage.setting("activity_catalog_position", "{}"))
        starts = (
            location.get("next_pages")
            if location.get("date") == today.isoformat() and location.get("markets") == len(research.CITIES)
            else first.get("next_pages")
        )
        for _ in range(2):
            if not starts or checks["priority_candidates"] >= limit:
                break
            report(
                "Reading more Yahoo Local painting listings for businesses with a missing link, a launch page or no listed website."
            )
            later = public_profiles(today, report, starts)
            limited = limited or later.get("source_limited", False)
            profiles.update((p["activity"]["listing_url"], p) for p in later["profiles"])
            counts = {key: value + later["counts"][key] for key, value in counts.items()}
            counts["recent_profiles"] = len(profiles)
            selected, checks = select(profiles.values(), licenses, seen, limit)
            starts = later["next_pages"] if later["next_pages"] != starts else {}
            if not limited and checks["priority_candidates"] <= limit and later["counts"]["source_listings"]:
                storage.set_setting(
                    "activity_catalog_position",
                    json.dumps({"date": today.isoformat(), "markets": len(research.CITIES), "next_pages": starts}),
                )
            if limited:
                break
    if limited:
        report("Yahoo Local temporarily limited requests. This batch uses the verified listings available today; coverage is partial.")
    if profiles:
        storage.set_setting("activity_inventory", json.dumps({"date": today.isoformat(), "profiles": profiles}))
    report(
        f"{checks['eligible_unseen']} unseen painters matched one active license by phone, city and trade name. Selected {len(selected)} for website research."
    )
    if audit is not None:
        audit.update(
            counts,
            **checks,
            source_limited=limited,
            selected=len(selected),
            activity_cutoff=(today - timedelta(days=365)).isoformat(),
        )
    return selected
