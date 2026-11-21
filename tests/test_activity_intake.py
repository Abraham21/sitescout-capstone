"""Discovery should hold its sample boundary even when activity or license evidence is thin."""

from datetime import date

import activity_intake as intake
import research


def test_only_dated_recent_customer_activity_on_the_actual_listing_can_seed_a_lead(monkeypatch):
    listing = "https://local.yahoo.com/info-10-painter/"
    yelp = "https://www.yelp.com/biz/local-painter?hrid=recent"
    node = {
        "url": listing,
        "name": "Peterson Painting",
        "telephone": "818-555-0101",
        "address": {"addressLocality": "Burbank"},
    }
    page = {"status": 200, "url": listing, "business_nodes": [node], "links": [], "yahoo_reviews": []}
    monkeypatch.setattr(research, "directory_page", lambda url, day: page)
    today = date(2026, 11, 20)
    for date_text, link in [
        ("2025-11-19", yelp),
        ("2027-01-01", yelp),
        ("", yelp),
        ("2026-11-01", "https://www.yelp.com/biz/other"),
    ]:
        page["yahoo_reviews"].append({"date": date_text, "source_url": link})
        assert not intake.recent_profile(listing, today)
    page["yahoo_reviews"].append({"date": "2025-11-20", "source_url": yelp, "rating": 2})
    profile = intake.recent_profile(listing, today)
    assert profile["activity"]["source_url"] == yelp
    assert profile["activity"]["date"] == "2025-11-20"
    page["url"] = "https://local.yahoo.com/info-20-someone-else/"
    assert not intake.recent_profile(listing, today)


def test_activity_selection_matches_one_license_and_returns_short_batches_without_padding():
    base = {
        "id": "100",
        "name": "John Peterson Painting LLC",
        "city": "Burbank",
        "phone": "(818) 555-0101",
    }
    second = {**base, "name": "Elm Corp DBA Elm Stripe Painting", "id": "101", "phone": "(818) 555-0102"}
    third = {**base, "name": "Maple Painting", "id": "102", "phone": "(818) 555-0103"}
    signal = {
        "type": "customer_review",
        "date": "2026-11-19",
        "listing_url": "https://local.yahoo.com/info-1-company/",
    }

    def profile(lead, name, listed):
        return {**lead, "name": name, "listed_website": listed, "activity": signal}

    profiles = [
        profile(base, "Peterson Painting", True),
        profile(second, "Elm Stripe Painting", False),
        profile(third, "Maple Painting", False),
    ]
    leads = [base, second, third]
    found, stats = intake.select(profiles, leads, {third["id"]}, 10)
    assert [lead["id"] for lead in found] == ["101", "100"]
    assert stats["previously_seen"] == 1 and stats["eligible_unseen"] == 2
    assert not intake.select([profiles[0]], [base, {**base, "id": "900"}], set(), 1)[0]
    assert not intake.select([profiles[0]], [{**base, "city": "Pasadena"}], set(), 1)[0]
    assert not intake.select([profiles[0]], [{**base, "phone": "(415) 555-0199"}], set(), 1)[0]
    assert not intake.select([profile(base, "Peter Peterson Painting", False)], [base], set(), 1)[0]


def test_an_exhausted_first_window_follows_the_next_published_page(tmp_path, monkeypatch):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    lead = {"id": "101", "name": "Elm Stripe Painting", "city": "Burbank", "phone": "(818) 555-0102"}
    profile = {
        **lead,
        "listed_website": False,
        "activity": {"date": "2026-11-19", "listing_url": "https://local.yahoo.com/info-1-elm/"},
    }
    scans = []

    def profiles(today, report, starts=None):
        scans.append(starts)
        return {
            "profiles": [profile] if starts else [],
            "next_pages": {"Burbank": 7 if starts else 4},
            "counts": {
                "source_listings": 1,
                "reviewed_listings_with_phone": 1,
                "recent_profiles": int(bool(starts)),
            },
        }

    monkeypatch.setattr(intake, "public_profiles", profiles)
    monkeypatch.setattr(research, "discover", lambda *args: [lead])
    assert [row["id"] for row in intake.discover(1, set())] == [lead["id"]]
    assert scans == [None, {"Burbank": 4}]
    assert intake.discover(1, {lead["id"]}) == []
    assert scans[-1] == {"Burbank": 7}


def test_broken_listing_beats_no_link_and_a_denied_site_is_not_a_defect(monkeypatch):
    lead = {"name": "Maple Painting", "city": "Burbank", "phone": "818-555-0100", "id": "100"}
    licenses = [{**lead, "id": str(100 + i), "phone": f"818-555-010{i}"} for i in range(4)]
    profiles = []
    for i, row in enumerate(licenses):
        url = ("https://closed.example", "", "", "https://denied.example")[i]
        profiles.append({**row, "listed_website": bool(url), "website_url": url, "contact_estimates": i == 2,
                         "activity": {"date": "2026-11-10", "listing_url": f"https://local.yahoo.com/info-{i}/"}})
    monkeypatch.setattr(research, "read_public", lambda url, *_: (url, 404 if "closed" in url else 403, "", b""))
    intake.listed_site_signal.cache_clear()
    selected, counts = intake.select(profiles, licenses, set(), 4)
    assert [lead["id"] for lead in selected] == ["100", "102", "101", "103"]
    assert counts["priority_candidates"] == 3 and counts["unseen_with_missing_link"] == 1


def test_listed_launch_page_needs_sourced_work_to_get_priority(monkeypatch):
    rows = [{"id": str(i), "name": "Oak Painting", "city": "Downey", "phone": f"562-555-010{i}"} for i in (1, 2, 3)]
    profiles = [{**row, "listed_website": True, "website_url": f"https://oak{row['id']}.example/",
                 "gallery": row["id"] != "2", "activity": {"date": "2026-11-20", "listing_url": row["id"]}}
                for row in rows]
    def read(url, *args):
        body = b"<h1>Launching Soon</h1>" if "oak3" not in url else b"<h1>Our work</h1><p>New services coming soon.</p>"
        return url, 200, "text/html", body
    monkeypatch.setattr(research, "read_public", read)
    intake.listed_site_signal.cache_clear()
    selected, counts = intake.select(profiles, rows, set(), 3)
    assert selected[0]["id"] == "1"
    assert counts["priority_candidates"] == counts["unseen_with_launch_page_and_gallery"] == 1


def test_discovery_widens_even_if_listed_sites_would_fill_the_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    calls = []
    first = {"id": "1", "name": "Elm Painting", "city": "Burbank", "phone": "818-555-0101"}
    later = {**first, "id": "2", "name": "Oak Painting", "phone": "818-555-0102"}

    def window(today, report, starts=None):
        calls.append(starts)
        lead = later if starts else first
        profile = {**lead, "listed_website": not starts, "activity": {"date": "2026-11-19", "listing_url": lead["id"]}}
        return {"profiles": [profile], "next_pages": {} if starts else {"Burbank": 4},
                "counts": {"source_listings": 1, "recent_profiles": 1}}

    monkeypatch.setattr(intake, "public_profiles", window)
    monkeypatch.setattr(research, "discover", lambda *args: [first, later])
    assert intake.discover(1, set())[0]["id"] == later["id"]
    assert calls == [None, {"Burbank": 4}]


def test_partial_directory_uses_todays_earlier_evidence(monkeypatch, tmp_path):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    lead = {"name": "Elm Painting", "city": "Downey", "phone": "562-555-0101", "id": "401"}
    profile = {**lead, "listed_website": False, "activity": {"date": date.today().isoformat(), "listing_url": "elm"}}
    window = {"profiles": [profile], "counts": {"recent_profiles": 1, "source_listings": 1}, "next_pages": {}}
    monkeypatch.setattr(intake, "public_profiles", lambda *_: window)
    monkeypatch.setattr(research, "discover", lambda *_: [lead])
    assert intake.discover(1, set())[0]["id"] == "401"
    window.update(profiles=[], source_limited=True, counts={"recent_profiles": 0, "source_listings": 0})
    audit = {}
    assert intake.discover(1, set(), audit=audit)[0]["id"] == "401"
    assert audit["source_limited"] and audit["recent_profiles"] == 1
    assert intake.discover(1, {"401"}) == []
