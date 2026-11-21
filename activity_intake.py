"""Start with a dated Yelp review, then verify the painter's active license."""

import json
import re
from collections import defaultdict
from concurrent.futures import ThreadPoolExecutor
from datetime import date, timedelta
from functools import lru_cache
from urllib.parse import urljoin, urlsplit

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
            "state": address.get("addressRegion", ""),
            "printed_licenses": sorted(research.stated_licenses(page.get("text", ""))),
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


def public_profiles(today, report, licenses, starts=None):
    first = starts if starts is not None else dict.fromkeys(research.CITIES, 1)
    registered_phones = {phone for lead in licenses for phone in research.phones(lead["phone"])}
    registered_names = {research.trade_name(lead["name"]) for lead in licenses}

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
        reviewed = [row for row in cards.values() if row["reviewed"] and row["phone"]]
        # The card is a cheap shortlist. Verify the phone, locality, trade name and review on the listing.
        urls = [row["url"] for row in reviewed if registered_phones.intersection(research.phones(row["phone"]))
                or research.trade_name(row["name"]) in registered_names]
        report(
            f"{len(reviewed)} directory cards have reviews and a phone. Checking the {len(urls)} with a registry phone or trade name for dated Yelp reviews."
        )
        profiles = [
            profile for profile in pool.map(lambda url: recent_profile(url, today), urls) if profile
        ]
    return {
        "source_limited": bool(research.directory_retry_after()),
        "profiles": profiles,
        "next_pages": {city: page for city, _, page in windows if page},
        "counts": {
            "source_listings": len(cards),
            "reviewed_listings_with_phone": len(reviewed),
            "profile_checks": len(urls),
            "recent_profiles": len(profiles),
        },
    }


@lru_cache(maxsize=256)
def listed_site_signal(url, today, business_name):
    """A listed site can earn a closer look. The qualifier independently verifies any claim."""
    limit = 150_000
    final, status, mime, body = research.read_public(url, "text/html", limit)
    if research.host(final) != research.host(url):
        return ""
    if status in {404, 410}:
        return "missing page"
    if status == 0 and final == url and research.exact_brand_homepage(business_name, url):
        retry_url, retry_status, _, _ = research.read_public(url, "text/html", limit)
        if retry_status == 0 and retry_url == url and research.missing_domain_sources(research.host(url)):
            return "unresolved domain"
    if status == 200 and "html" in mime and not urlsplit(final).path.strip("/"):
        soup = BeautifulSoup(body[:limit], "html.parser")
        if len(body) <= limit and b"soon" in body.lower() and research.launch_heading(soup):
            return "launch page"
        for link in soup.select("a[href]"):
            href = link["href"].strip()
            if not href or "#" in href or "?" in href:
                continue
            try:
                target = urljoin(final, href)
            except ValueError:
                continue
            if not research.safe_url(target) or research.host(target) != research.host(final):
                continue
            path = urlsplit(target).path.strip("/")
            label = link.get_text(" ", strip=True) + " " + path.replace("-", " ")
            if path and re.search(r"\b(?:estimate|quote|booking|appointment)\b", label, re.I):
                dest, code, _, _ = research.read_public(target, "text/html", 0, within=research.host(final))
                return "broken estimate link" if code in {404, 410} and research.host(dest) == research.host(final) else ""
    return ""


def registry_phone_index(licenses):
    by_phone = defaultdict(list)
    for lead in licenses:
        for phone in research.phones(lead.get("phone", "")):
            by_phone[phone].append(lead)
    return by_phone


def match_profile(profile, by_phone):
    owners = {l["id"]: l for phone in research.phones(profile["phone"]) for l in by_phone.get(phone, ())}
    local = [l for l in owners.values() if research.words(l["city"]) == research.words(profile["city"])
             and research.same_trade_name(l["name"], profile["name"])]
    if local:
        return local[0] if len(local) == 1 else None
    if len(owners) != 1 or profile["city"].strip().title() not in research.CITIES:
        return None
    lead = next(iter(owners.values()))
    if (lead["city"] not in research.CITIES
            or len(research.phones(profile["phone"])) != 1 or research.phones(profile["phone"]) != research.phones(lead["phone"])
            or research.trade_name(lead["name"]) != research.trade_name(profile["name"])):
        return None
    # Older inventory lacks the listing's state and printed licenses. Read its canonical card once.
    if "printed_licenses" not in profile:
        current = recent_profile(profile["activity"]["listing_url"], date.today())
        if not current:
            return None
        profile.update(current)
        return match_profile(profile, by_phone)
    if (research.words(profile.get("state", "")) not in {"ca", "california"}
            or set(profile["printed_licenses"]) - {lead["id"]}):
        return None
    return lead


def select(profiles, licenses, seen, limit):
    by_phone = registry_phone_index(licenses)
    matched = {}
    for profile in profiles:
        lead = match_profile(profile, by_phone)
        if lead is None:
            continue
        mark = profile["activity"]
        if research.words(lead["city"]) != research.words(profile["city"]):
            mark = {**mark, "listing_city": profile["city"].strip().title()}
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
        probes = pool.map(lambda lead: listed_site_signal(lead["website_url"], today, lead["name"]), linked)
        for lead, signal in zip(linked, probes):
            lead["listed_site_signal"] = signal
            lead["missing_link"] = signal in {"missing page", "unresolved domain"}
    for lead in unseen:
        lead["priority"] = (
            not lead["listed_website"] or lead.get("missing_link", False)
            or lead.get("listed_site_signal") == "broken estimate link"
            or (lead.get("listed_site_signal") == "launch page" and lead["gallery"])
        )
    # Stable sorting preserves review recency and the license-ID tiebreak within each group.
    unseen.sort(
        key=lambda lead: (
            not lead["priority"],
            not (lead.get("missing_link", False) or lead.get("listed_site_signal") == "broken estimate link"),
            lead["listed_website"],
            not lead["contact_estimates"], not lead["gallery"],
        )
    )
    return unseen[:limit], {
        "unique_active_license_matches": len(matched),
        "previously_seen": len(matched) - len(unseen),
        "eligible_unseen": len(unseen),
        "priority_candidates": sum(lead["priority"] for lead in unseen),
        "unseen_with_broken_estimate_link": sum(lead.get("listed_site_signal") == "broken estimate link" for lead in unseen),
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
    licenses = research.discover(100_000, set(), report)
    first = public_profiles(today, report, licenses)
    profiles.update((p["activity"]["listing_url"], p) for p in first["profiles"])
    counts = dict(first["counts"])
    counts["recent_profiles"] = len(profiles)
    limited = first.get("source_limited", False)
    report(
        f"{len(profiles)} profiles found today have a dated review in the last 365 days. Checking active C-33 licenses."
    )
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
            later = public_profiles(today, report, licenses, starts)
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
    selected = [lead for lead in selected if lead["priority"]]
    deferred = checks["eligible_unseen"] - checks["priority_candidates"]
    report(
        f"{checks['eligible_unseen']} unseen painters matched an active license. Selected {len(selected)} with a website opportunity signal. "
        f"{deferred} had a listed site with no observed issue and remain eligible for future scans."
    )
    if audit is not None:
        audit.update(
            counts,
            **checks,
            deferred_without_signal=deferred,
            source_limited=limited,
            selected=len(selected),
            activity_cutoff=(today - timedelta(days=365)).isoformat(),
        )
    return selected
