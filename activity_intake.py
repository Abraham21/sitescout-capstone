"""Start with a dated Yelp review, then verify the painter's active license."""

import json
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from pathlib import Path
from tempfile import NamedTemporaryFile

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
            "listed_website": bool(research.published_website_links(page)),
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
        if saved["date"] == today.isoformat() and saved.get("starts", first) == first:
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
    saved = {
        "date": today.isoformat(),
        "starts": first,
        "profiles": profiles,
        "next_pages": {city: page for city, _, page in windows if page},
        "counts": {
            "source_listings": len(cards),
            "reviewed_listings_with_phone": len(urls),
            "recent_profiles": len(profiles),
        },
    }
    if cards:
        cache.parent.mkdir(parents=True, exist_ok=True)
        with NamedTemporaryFile("w", dir=cache.parent, delete=False) as stream:
            json.dump(saved, stream)
        Path(stream.name).replace(cache)
    return saved


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
            }
    unseen = [l for i, l in matched.items() if i not in seen]
    # Every selection decision uses discovery's sources. Website inspection happens in the graph.
    unseen.sort(
        key=lambda lead: (
            lead["listed_website"],
            -date.fromisoformat(lead["activity"]["date"]).toordinal(),
            lead["id"],
        )
    )
    return unseen[:limit], {
        "unique_active_license_matches": len(matched),
        "previously_seen": len(matched) - len(unseen),
        "eligible_unseen": len(unseen),
        "unseen_without_listed_website": sum(not lead["listed_website"] for lead in unseen),
    }


def discover(limit, seen, progress=None, *, audit=None):
    report = progress or (lambda message: None)
    today = date.today()
    report(
        "Finding painters with a customer review on Yelp in the past year, via public Yahoo Local listings."
    )
    first = public_profiles(today, report)
    profiles = first["profiles"]
    counts = dict(first["counts"])
    report(
        f"{counts['recent_profiles']} profiles have a dated review in the last 365 days. Checking active C-33 licenses."
    )
    licenses = research.discover(100_000, set(), report)
    selected, checks = select(profiles, licenses, seen, limit)
    if len(selected) < limit:
        storage.initialize()
        location = json.loads(storage.setting("activity_catalog_position", "{}"))
        starts = (
            location.get("next_pages")
            if location.get("date") == today.isoformat()
            else first.get("next_pages")
        )
        if starts:
            report(
                "The first source window has too few unseen licenses. Reading the next public directory pages."
            )
            later = public_profiles(today, report, starts)
            profiles = {p["activity"]["listing_url"]: p for p in profiles + later["profiles"]}
            counts = {key: value + later["counts"][key] for key, value in counts.items()}
            counts["recent_profiles"] = len(profiles)
            selected, checks = select(profiles.values(), licenses, seen, limit)
            if checks["eligible_unseen"] <= limit and later["counts"]["source_listings"]:
                storage.set_setting(
                    "activity_catalog_position",
                    json.dumps({"date": today.isoformat(), "next_pages": later["next_pages"]}),
                )
    report(
        f"{checks['eligible_unseen']} unseen painters matched one active license by phone, city and trade name. Selected {len(selected)} for website research."
    )
    if audit is not None:
        audit.update(
            counts,
            **checks,
            selected=len(selected),
            activity_cutoff=(today - timedelta(days=365)).isoformat(),
        )
    return selected
