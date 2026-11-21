"""Discovery should hold its sample boundary even when activity or license evidence is thin."""

from datetime import date

import activity_intake as intake
import research


def test_directory_reads_only_plausible_profiles_and_leaves_identity_verification_to_the_listing(monkeypatch):
    lead = {"name": "Alder Painting Inc", "phone": "(818) 555-0100"}
    cards = [
        {"name": "Alder Studio", "phone": "818-555-0100", "url": "by-phone", "reviewed": True},
        {"name": "Alder Painting", "phone": "818-555-0120", "url": "by-name", "reviewed": True},
        {"name": "Other Painter", "phone": "818-555-0199", "url": "other", "reviewed": True},
        {"name": "Alder Painting", "phone": "818-555-0100", "url": "unreviewed", "reviewed": False},
    ]
    read = []
    monkeypatch.setattr(research, "catalog_page", lambda *args: (cards, None))
    monkeypatch.setattr(research, "directory_retry_after", lambda: 0)
    monkeypatch.setattr(intake, "recent_profile", lambda url, today: read.append(url))
    window = intake.public_profiles(date.today(), lambda msg: None, [lead], {"Burbank": 1})
    assert set(read) == {"by-phone", "by-name"}
    assert window["counts"]["profile_checks"] == 2
    assert window["profiles"] == []


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


def test_cross_city_candidate_requires_unique_phone_and_independent_proof_before_outreach():
    listing = "https://local.yahoo.com/info-108-alder-painting-pasadena/"
    customer = "https://www.yelp.com/biz/alder-painting?hrid=r1"
    today = date.today().isoformat()
    lead = {"id": "712345", "name": "Alder Painting LLC", "city": "Burbank", "phone": "818-555-0101"}
    mark = {"type": "customer_review", "date": today, "source_url": customer, "listing_url": listing}
    profile = {"name": "Alder Painting", "city": "Pasadena", "state": "CA", "phone": lead["phone"],
               "printed_licenses": [], "activity": mark, "listed_website": False}
    def select(p, others=()):
        return intake.select([p], [lead, *others], {"999999"}, 1)[0]

    [candidate] = select(profile)
    assert candidate["city"] == "Burbank" and candidate["activity"]["listing_city"] == "Pasadena"
    queries = []
    def search(query):
        queries.append(query)
        return [{"url": url, "text": "Alder Painting. 818-555-0101."}
                for url in [customer, listing]]
    _, used = research.search_business(candidate, search, lambda message: None)
    assert used == len(queries) == 3
    assert all(word in queries[-1] for word in ["Nextdoor", "Pasadena", "Burbank", "818-555-0101"])
    assert not select(profile, [{**lead, "id": "999999", "city": "Glendale"}])
    for changes in [{"printed_licenses": ["812345"]}, {"city": "Stockton"}, {"state": "TX"},
                    {"name": "Alder Brothers Painting"}]:
        assert not select({**profile, **changes})
    node = {"url": listing, "name": profile["name"], "telephone": lead["phone"],
            "address": {"addressLocality": "Pasadena", "addressRegion": "CA"}}
    page = {"url": listing, "status": 200, "business_nodes": [node], "links": [], "text": "",
            "yahoo_reviews": [{"date": today, "source_url": customer}]}
    social = "https://nextdoor.com/pages/alder-painting-pasadena/"
    pages = {listing: page, social: {"url": social, "status": 200, "business_nodes": [node]}}
    hits = []
    def qualify():
        return research.qualify(candidate, None, lambda query: hits, fetcher=lambda u: pages.get(u, {}))

    result = qualify()
    assert result["status"] == "hold" and "second business profile" in result["reason"]
    hits.append({"url": social, "text": "Alder Painting. 818-555-0101. Pasadena."})
    assert qualify()["status"] == "hold"  # The other page initially repeats the first publisher's record.
    pages[social]["business_nodes"] = [{**node, "url": social}]
    result = qualify()
    assert result["status"] == "qualified" and not result["facts"]["service_areas"]
    assert any(row["url"] == social and row["id"] in result["issue_evidence_ids"] for row in result["evidence"])
    hits.clear()
    page["text"] = "CSLB license 712345."
    result = qualify()
    assert result["status"] == "qualified" and any(
        row["url"] == listing and "CSLB license 712345" in row["text"] for row in result["evidence"]
    )
    page["text"] = "CSLB license 812345."
    assert qualify()["status"] == "hold"


def test_an_exhausted_first_window_follows_the_next_published_page(tmp_path, monkeypatch):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    lead = {"id": "101", "name": "Elm Stripe Painting", "city": "Burbank", "phone": "(818) 555-0102"}
    profile = {
        **lead,
        "listed_website": False,
        "activity": {"date": "2026-11-19", "listing_url": "https://local.yahoo.com/info-1-elm/"},
    }
    scans = []

    def profiles(today, report, licenses, starts=None):
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
    licenses = [{**lead, "id": str(100 + i), "phone": f"818-555-010{i}"} for i in range(5)]
    profiles = []
    for i, row in enumerate(licenses):
        url = ("https://closed.example", "", "", "https://denied.example", "https://maplepainting.example/")[i]
        profiles.append({**row, "listed_website": bool(url), "website_url": url, "contact_estimates": i == 2,
                         "activity": {"date": "2026-11-10", "listing_url": f"https://local.yahoo.com/info-{i}/"}})
    codes = {"closed.example": 404, "denied.example": 403, "maplepainting.example": 0}
    monkeypatch.setattr(research, "read_public", lambda url, *_: (url, codes[research.host(url)], "", b""))
    monkeypatch.setattr(research, "missing_domain_sources", lambda domain: ["Google", "Cloudflare"])
    intake.listed_site_signal.cache_clear()
    selected, counts = intake.select(profiles, licenses, set(), 5)
    assert [lead["id"] for lead in selected] == ["100", "104", "102", "101", "103"]
    assert counts["priority_candidates"] == 4 and counts["unseen_with_missing_link"] == 2


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


def test_only_first_same_site_estimate_link_can_earn_discovery_priority(monkeypatch):
    rows = [{"id": str(i), "name": "Oak Painting", "city": "Downey", "phone": f"562-555-010{i}"} for i in range(1, 6)]
    profiles = [{**row, "listed_website": True, "website_url": f"https://oak{row['id']}.example/",
                 "activity": {"date": "2026-11-20", "listing_url": row["id"]}} for row in rows]
    body = b'''<a href="https://other.example/quote">Request quote</a>
        <a href="#estimate">Estimate</a><a href="/?promo=quote">Book an estimate</a>
        <a href="/contact">Contact</a><a href="/bid">Get a free quote</a>
        <a href="/backup">Request another estimate</a>'''
    checked = []
    def read(url, accept, limit, within=""):
        painter = research.host(url)
        if url.endswith("/"):
            return url, 200, "text/html", body
        assert url.endswith("/bid") and painter == within
        checked.append(url)
        code = {"oak1.example": 404, "oak2.example": 410, "oak3.example": 403,
                "oak4.example": 200, "oak5.example": 404}[painter]
        final = "https://other.example/missing" if painter == "oak5.example" else url
        return final, code, "text/html", b""
    monkeypatch.setattr(research, "read_public", read)
    intake.listed_site_signal.cache_clear()
    selected, counts = intake.select(profiles, rows, set(), len(rows))
    assert [row["id"] for row in selected if row["priority"]] == ["1", "2"]
    assert counts["unseen_with_broken_estimate_link"] == 2
    assert len(checked) == len(profiles)


def test_discovery_widens_even_if_listed_sites_would_fill_the_batch(monkeypatch, tmp_path):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    calls = []
    first = {"id": "1", "name": "Elm Painting", "city": "Burbank", "phone": "818-555-0101"}
    later = {**first, "id": "2", "name": "Oak Painting", "phone": "818-555-0102"}

    def window(today, report, licenses, starts=None):
        calls.append(starts)
        lead = later if starts else first
        profile = {**lead, "listed_website": not starts, "activity": {"date": "2026-11-19", "listing_url": lead["id"]}}
        return {"profiles": [profile], "next_pages": {} if starts else {"Burbank": 4},
                "counts": {"source_listings": 1, "recent_profiles": 1}}

    monkeypatch.setattr(intake, "public_profiles", window)
    monkeypatch.setattr(research, "discover", lambda *args: [first, later])
    assert intake.discover(1, set())[0]["id"] == later["id"]
    assert calls == [None, {"Burbank": 4}]
    audit = {}
    assert intake.discover(10, {later["id"]}, audit=audit) == []
    assert audit["deferred_without_signal"] == 1
    assert audit["eligible_unseen"] == 1


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
