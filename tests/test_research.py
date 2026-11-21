import json
import socket
import sqlite3
from pathlib import Path

import pytest

import research

CASES = json.loads((Path(__file__).resolve().parents[1] / "examples/scenarios.json").read_text())


def run(case, *, model=None, mode="fts", results=None):
    def fetch(url):
        assert url in case["pages"], "Unexpected URL in offline fixture"
        return case["pages"][url]

    return research.qualify(
        case["lead"], model, lambda _: results or case["results"], fetcher=fetch, retrieval=mode
    )


@pytest.mark.parametrize("case", CASES, ids=[c["id"] for c in CASES])
def test_offline_gate_and_evidence(case):
    result = run(case)
    assert result["status"] == case["expected"]["status"]
    assert result["website_status"] == case["expected"]["website_status"]
    assert set(result["issue_evidence_ids"]) <= {e["id"] for e in result["evidence"]}
    if result["status"] == "qualified":
        assert result["issue_evidence_ids"]
    expected = case["expected"].get("evidence_contains")
    if expected:
        assert any(
            expected in e["text"] and e["url"] == case["expected"]["evidence_url"]
            for e in result["evidence"]
        )


def test_retrieval_keeps_decisive_passage_and_exact_services():
    social, long_case = CASES[:2]
    actual = run(long_case)
    assert len(actual["retrieved_ids"]) < actual["corpus_size"]
    assert set(actual["issue_evidence_ids"]) <= set(actual["retrieved_ids"])
    assert {"Exterior painting", "Cabinet refinishing"} <= set(actual["facts"]["services"])
    locations = actual["facts"]["service_areas"]
    assert locations == ["Pasadena", "Altadena", "San Marino"]
    for place in locations:
        ref = actual["facts"]["citations"][place]
        assert any(e["id"] == ref and place in e["text"] for e in actual["evidence"])
    social_result = run(social)
    assert "Interior painting" in social_result["facts"]["services"]
    assert social_result["email"] == "estimates@copperfinch.example"
    assert social_result["model_mode"] == "deterministic replay"
    assert (
        "Cabinet refinishing"
        not in run(
            social, model=lambda _: {"services": [{"value": "Cabinet refinishing", "cite": "e1"}]}
        )["facts"]["services"]
    )
    facts = run(social, model=lambda _: {"service_area": {"value": "Pasadena", "cite": "registry"}})[
        "facts"
    ]
    assert facts["service_areas"] == ["Glendale"] and facts["citations"]["Glendale"] != "registry"
    assert run(social, model=lambda _: {"concern": "Possible identity mismatch."})["status"] == "hold"


def test_cached_index_keeps_sources_isolated(tmp_path):
    db = tmp_path / "research.sqlite"
    first, second = [run(c, mode="full")["evidence"] for c in CASES[:2]]
    for _ in range(2):
        for case, evidence in zip(CASES, (first, second)):
            selected = research.ranking(case["lead"], evidence, "fts", index=db)
            assert all(x in evidence for x in selected)
    with sqlite3.connect(db) as c:
        assert c.execute("select count(*) from passages").fetchone()[0] == len(first) + len(second)


def test_no_private_socket_and_no_mailing_address(monkeypatch):
    monkeypatch.setattr(
        socket,
        "getaddrinfo",
        lambda *args, **kw: [(socket.AF_INET, socket.SOCK_STREAM, 6, "", ("127.0.0.1", 80))],
    )
    with pytest.raises(OSError):
        research.public_socket(("safe.example", 80), 0.1)
    assert not research.safe_url("https://reader:password@site.example/private")
    assert "1037" not in research.clean_text("Address: 1037 Sunset Boulevard, Glendale, CA")


def test_social_subdomains_count_as_one_independent_source():
    case = CASES[0]
    row = case["results"][0]
    result = run(case, results=[row, {**row, "url": "https://m.instagram.com/site_scout_demo_900001/"}])
    assert result["status"] == "hold"


def test_registry_filter_and_city_rotation(tmp_path, monkeypatch):
    row = dict(
        zip(
            [
                "City",
                "State",
                "LicenseNo",
                "PrimaryStatus",
                "SecondaryStatus",
                "Classifications(s)",
                "ExpirationDate",
                "BusinessName",
                "BusinessPhone",
                "MailingAddress",
            ],
            [
                "Glendale",
                "CA",
                "123456",
                "CLEAR",
                "",
                "C33",
                "12/31/2099",
                "Brush Demo",
                "818-555-0101",
                "123 Elm St",
            ],
        )
    )
    second = {**row, "City": "Burbank", "LicenseNo": "123457"}
    eligible = research.registry_leads(
        [
            row,
            second,
            {**row, "PrimaryStatus": "EXPIRED"},
            {**row, "Classifications(s)": "C333"},
            {**row, "ExpirationDate": "01/01/2000"},
        ]
    )
    assert len(eligible) == 2 and "MailingAddress" not in eligible[0]
    cache = tmp_path / "leads.json"
    cache.write_text(json.dumps(eligible))
    monkeypatch.setattr(research, "CACHE", cache)
    first = research.discover(1, set())[0]
    assert first["city"] == "Glendale"
    assert research.discover(1, {first["id"]})[0]["city"] == "Burbank"
    painter = {**eligible[0], "id": "123450", "name": "Pinturas del Valle"}
    cache.write_text(json.dumps(eligible + [painter]))
    assert research.discover(1, set())[0]["id"] == painter["id"]
    assert {item["id"] for item in research.discover(10, set())} == {
        item["id"] for item in eligible + [painter]
    }


def test_disabled_listing_inbox_is_specific_need_and_syndication_is_one_source():
    case = CASES[0]
    body = "Copper Finch Painting, Glendale, (818) 555-0101. This business has not enabled messaging."
    first = {"url": "https://www.yelp.com/biz/fixture", "title": case["lead"]["name"], "text": body}
    result = run(case, results=[first, case["results"][1]])
    assert result["status"] == "qualified" and "yelp.com" in result["issue"]
    assert (
        result["status"]
        != run(
            case,
            results=[
                {**first, "url": "https://angi.com/biz/fixture"},
                {**first, "url": "https://homeadvisor.com/biz/fixture"},
            ],
        )["status"]
    )


def test_initials_can_identify_an_owned_trade_domain_but_never_an_unrelated_registry():
    lead = {
        "name": "AB Painting and Design LLC",
        "id": "911112",
        "city": "Glendale",
        "phone": "(818) 555-0190",
    }
    assert research.branded(lead, "https://abpaintinginc.example/")
    assert not research.branded(lead, "https://trade-records.example/business/ab-painting")
    assert research.identity(lead, "AB Painting and Design LLC in Fresno. Phone (559) 555-0111.") == 0
    assert (
        research.identity(lead, "AB Painting and Design LLC. (818) 555-0190. License (#911113).") == 0
    )


def test_a_bounded_search_cannot_claim_absence_when_a_candidate_remains():
    case = CASES[0]
    candidates = [f"https://source{n}.example/biz" for n in range(4)]
    extras = [
        {"title": case["lead"]["name"], "url": url, "text": "Glendale, CA. (818) 555-0101."}
        for url in candidates
    ]
    result = research.qualify(
        case["lead"],
        None,
        lambda _: case["results"] + extras,
        fetcher=lambda u: {
            "url": u,
            "status": 200,
            "text": "Claim this listing",
            "links": [],
            "has_form": False,
        },
    )
    assert result["status"] == "hold"
    assert "unverified" in result["reason"]


def test_a_named_houzz_profile_with_conflicting_phone_prompts_manual_domain_check():
    lead = CASES[0]["lead"]
    domain = "https://copperfinch.example"
    profile = "https://www.houzz.com/professionals/painters/demo"
    calls, search_calls = [], []

    def fetch(url):
        calls.append(url)
        if url == profile:
            return {
                "url": url,
                "status": 200,
                "text": "Business Details Business Name Copper Finch Painting Phone Number (310) 555-0123 Website copperfinch.example Address Pasadena, CA",
            }
        assert url == domain
        return {"url": url, "status": 0, "text": ""}

    def search(query):
        search_calls.append(query)
        if len(search_calls) == 3:
            return [
                {"url": profile, "title": lead["name"], "text": "Contractor profile in California."}
            ]
        return []

    q = research.qualify(lead, None, search, fetcher=fetch)
    assert len(search_calls) == 3 and calls.count(domain) == 2
    assert q["status"] == "hold" and q["website_status"] == "unsure" and q["follow_up"]["url"] == domain
    assert q["follow_up"]["source_url"] == profile and "identity" in q["follow_up"]["reason"]
    refs = {row["id"]: row for row in q["evidence"]}
    assert [refs[ref]["kind"] for ref in q["follow_up"]["evidence_ids"]] == [
        "source",
        "observation",
        "observation",
    ]
    assert not any(q["profile"].values())


def test_only_observed_listing_media_is_linked_and_only_for_verified_identity():
    case = CASES[0]
    base = {
        "title": case["lead"]["name"],
        "url": "https://www.yelp.com/biz/example",
        "text": "Copper Finch Painting, Glendale, (818) 555-0101. Photos & videos. See all 9 photos. 3 reviews. Messaging is disabled.",
    }
    listing = {
        "url": base["url"],
        "status": 200,
        "business_nodes": [
            {
                "name": case["lead"]["name"],
                "telephone": case["lead"]["phone"],
                "review": {
                    "@type": "Review",
                    "author": {"name": "D. Lane"},
                    "reviewBody": "The trim turned out beautifully.",
                },
            }
        ],
    }
    case = {**case, "pages": {**case["pages"], base["url"]: listing}}
    q = run(case, results=[base, case["results"][1]])
    assert q["profile"]["work_links"][0]["label"] == "9 photos on Yelp"
    one = {**base, "text": base["text"].replace("9 photos", "1 photo")}
    assert run(case, results=[one, case["results"][1]])["profile"]["work_links"][0]["label"] == "1 photo on Yelp"
    assert q["profile"]["review_links"][0]["url"] == base["url"] and not q["profile"]["media"]
    assert [(x["author"], x["source_url"]) for x in q["profile"]["testimonials"]] == [
        ("D. Lane", base["url"])
    ]
    other = {**base, "text": base["text"].replace("(818) 555-0101", "(510) 555-0101")}
    assert not any(run(case, results=[other, case["results"][1]])["profile"].values())


def test_instagram_uses_the_verified_accounts_own_photo_posts():
    from bs4 import BeautifulSoup
    from business_profile import instagram_profile

    url = "https://www.instagram.com/painter/"
    soup = BeautifulSoup(
        """
        <meta property="og:url" content="https://www.instagram.com/painter/">
        <header>License 1234567 <a href="https://l.instagram.com/?u=https%3A%2F%2Fbusiness.example">Website</a></header>
        <a href="/painter/"><img alt="Profile" src="https://images.cdninstagram.com/avatar.jpg"></a>
        <a href="/painter/p/JOB1/"><img src="https://images.cdninstagram.com/job.jpg"></a>
        <a href="/other/p/JOB2/"><img src="https://images.cdninstagram.com/other.jpg"></a>
        <a href="/painter/p/JOB3/"><img src="https://unrelated.example/photo.jpg"></a>
        <footer><a href="https://meta.com">Meta</a></footer>
    """,
        "html.parser",
    )
    info = instagram_profile(soup, url)
    assert "1234567" in info["social_bio"] and info["bio_links"] == ["https://business.example"]
    assert [im["post_url"] for im in info["social_photos"]] == [url + "p/JOB1/"]
    assert not instagram_profile(soup, "https://www.instagram.com/other/")
    photos = [
        dict(info["social_photos"][0], evidence_id="e1"),
        dict(info["social_photos"][0], evidence_id="e2"),
    ]
    profile = {"media": photos.copy()}
    research.apply_photo_choices(profile, photos, {"portfolio_photos": [2], "hero_photo": 1})
    assert profile["media"] == [photos[1]] and not profile.get("cover_evidence_id")


def test_listing_qualifies_only_a_phone_matched_painter_with_verified_work():
    url = "https://local.yahoo.com/info-123-copper-finch-painting-glendale/"
    case = CASES[0]
    lead = case["lead"]
    photos = [f"https://s.yimg.com/bj/aa/{i}.jpg" for i in (1, 2)]
    node = {
        "url": url,
        "name": lead["name"],
        "telephone": lead["phone"],
        "address": {"addressLocality": lead["city"]},
        "image": photos[:1],
        "description": "Interior painting. Call us today for an estimate.",
    }
    page = {
        "url": url,
        "status": 200,
        "text": node["description"],
        "links": [],
        "business_nodes": [node],
        "listing_photos": photos,
    }

    def run_listing():
        return research.qualify(
            {**lead, "directory_url": url}, None, lambda _: [], fetcher=lambda _: page
        )

    assert run_listing()["status"] == "qualified"
    node["telephone"] = "(415) 555-0000"
    assert run_listing()["status"] == "hold"
    node["telephone"] = lead["phone"]
    node["description"] = "Interior painting."
    assert run_listing()["status"] == "hold"
    node["description"] = "We do interior painting and refinish kitchen cabinets."
    page["listing_photos"] = []
    assert run_listing()["status"] == "hold"

    from bs4 import BeautifulSoup
    from business_profile import yahoo_reviews

    reviews = BeautifulSoup(
        '<section id="local-user-reviews"><article>'
        f'<div><span>C. Guest</span><span>{research.date.today():%m/%d/%y}</span></div>'
        '<div><p>The new cabinet paint looks wonderful.</p>'
        '<a href="https://www.yelp.com/biz/example?hrid=r1">More</a></div>'
        '<div><span>Unrelated</span><span>11/20/25</span></div>'
        '<div><p>This has no source for its review.</p>'
        '<a href="https://www.yelp.com/biz/example">More</a></div></article></section>',
        "html.parser",
    )
    page["yahoo_reviews"] = yahoo_reviews(reviews)
    assert len(page["yahoo_reviews"]) == 1
    result = run_listing()
    assert result["status"] == "qualified"
    testimonial = result["profile"]["testimonials"][0]
    assert (testimonial["author"], testimonial["quote"], testimonial["source_url"]) == (
        "C. Guest", "The new cabinet paint looks wonderful.", url
    )

    # A dated public review can establish customer activity even with no company description.
    node["description"] = ""
    review = page["yahoo_reviews"][0]
    activity = {"type": "customer_review", "date": review["date"], "source_url": review["source_url"], "listing_url": url}
    intake = {**lead, "directory_url": url, "activity": activity}
    recent = research.qualify(intake, None, lambda _: [], fetcher=lambda _: page)
    ref = recent["activity"]["evidence_id"]
    assert recent["status"] == "qualified" and ref in recent["issue_evidence_ids"] and ref in recent["retrieved_ids"]
    page["yahoo_reviews"] = []
    assert research.qualify(intake, None, lambda _: [], fetcher=lambda _: page)["status"] == "hold"


def test_unique_local_trading_name_reaches_preview_pitch_and_reviewer():
    from activity_intake import select
    from bs4 import BeautifulSoup
    from studio import build_preview, review, write_pitch

    licensee = {**CASES[0]["lead"], "name": "MARTIN LARK-SOTO"}
    url = "https://local.yahoo.com/info-123-lark-painting-glendale/"
    review_url = "https://www.yelp.com/biz/lark-painting?hrid=review1"
    today = research.date.today().isoformat()
    activity = {"type": "customer_review", "date": today, "source_url": review_url, "listing_url": url}
    profile = {"name": "Lark Painting", "city": licensee["city"], "phone": licensee["phone"],
               "activity": activity, "listed_website": False}
    node = {"name": profile["name"], "url": url, "address": {"addressLocality": profile["city"]},
            "telephone": profile["phone"]}
    page = {"url": url, "status": 200, "text": "", "links": [], "business_nodes": [node],
            "yahoo_reviews": [{"date": today, "source_url": review_url, "author": "J. Guest",
                               "quote": "The cabinet paint looks wonderful.", "rating": 5}]}
    [lead], _ = select([profile], [licensee], set(), 1)
    qualified = research.qualify(lead, None, lambda _: [], fetcher=lambda _: page)
    preview = {**build_preview(lead, qualified), "url": "/preview"}
    pitch = write_pitch(lead, qualified, preview, None)
    assert BeautifulSoup(preview["html"], "html.parser").h1.get_text(" ", strip=True) == "Lark Painting"
    assert pitch["subject"].endswith("Lark Painting") and pitch["body"].startswith("Hi Lark Painting team,")
    assert qualified["facts"]["citations"]["Lark Painting"] in pitch["evidence_ids"]
    assert review(lead, qualified, preview, pitch, None)["passed"]

    # A changed/ambiguous source or a second matching license cannot lend its brand.
    page["business_nodes"].append({**node, "name": "Lark and Sons Painting"})
    ambiguous = research.qualify(lead, None, lambda _: [], fetcher=lambda _: page)
    assert "public_name" not in ambiguous["facts"]
    assert "Martin Lark-Soto" in build_preview(lead, ambiguous)["html"]
    assert not select([profile], [licensee, {**licensee, "id": "other"}], set(), 1)[0]


def test_verified_first_party_gallery_supports_a_real_self_contained_raster():
    import io

    from bs4 import BeautifulSoup
    from PIL import Image

    import business_profile

    image_url = "https://linenleaf.example/media/entry.jpg"
    gallery = "https://linenleaf.example/gallery"
    soup = BeautifulSoup(
        '<figure><img src="/media/entry.jpg" alt="Repainted entry in a completed painting project"></figure>',
        "html.parser",
    )
    rows = business_profile.page_images(soup, gallery)
    assert rows == [
        {
            "asset_url": image_url,
            "alt": "Repainted entry in a completed painting project",
            "kind": "project",
        }
    ]
    case = CASES[1]
    pages = dict(case["pages"])
    homepage = "https://linenleaf.example/"
    pages[homepage] = {
        **pages[homepage],
        "links": [{"url": gallery, "text": "Portfolio"}, *pages[homepage]["links"]],
    }
    pages[gallery] = {"url": gallery, "status": 200, "text": "Our painting projects", "images": rows}
    photo = io.BytesIO()
    Image.new("RGB", (300, 200), "#eeddbb").save(photo, format="PNG")
    q = research.qualify(
        case["lead"],
        None,
        lambda _: case["results"],
        fetcher=pages.__getitem__,
        image_reader=lambda u, k: business_profile.small_raster(photo.getvalue(), k),
    )
    assert q["status"] == "qualified" and q["profile"]["work_links"][0]["url"] == gallery
    item = q["profile"]["media"][0]
    assert (
        item["asset_url"] == image_url
        and item["source_url"] == gallery
        and item["url"].startswith("data:image/jpeg;base64,")
    )
    assert item["evidence_id"] in {e["id"] for e in q["evidence"] if e["url"] == gallery}
    assert business_profile.small_raster(b"<html>not a raster</html>", "project") == ""


def test_gallery_cover_uses_image_quality_and_can_select_later_photos():
    import io

    from PIL import Image

    import business_profile

    profile = {"media": []}
    source = "https://painter.example/gallery"
    candidates, rasters = [], {}
    for i in range(9):
        url = f"https://painter.example/media/{i}.jpg"
        candidates.append(
            (source, {"asset_url": url, "kind": "project", "alt": f"Gallery photo {i + 1}"})
        )
        raw = io.BytesIO()
        # The last image is wide, correctly exposed and spreads across a landscape cover.
        size = (1200, 800) if i == 8 else (320, 600)
        Image.new("RGB", size, (90 + i * 10, 60, 120)).save(raw, "JPEG")
        rasters[url] = business_profile.small_raster(raw.getvalue(), "project")

    research.embed_photos(candidates, profile, lambda *_: "e1", lambda u, _: rasters[u])
    assert len(profile["media"]) == 9  # The visual qualifier chooses the final six.
    assert profile["media"][0]["asset_url"] == candidates[-1][1]["asset_url"]


def test_third_search_follows_a_discovered_directory_website_without_a_manual_hint():
    case = CASES[0]
    profile = "https://www.houzz.com/professionals/painters/copper-finch"
    domain = "https://copperfinch.example"
    identity = "Copper Finch Painting, Glendale. (818) 555-0101."
    queries = []

    def search(query):
        queries.append(query)
        if len(queries) == 3:
            return [
                {"url": profile, "title": case["lead"]["name"], "text": identity + " Project gallery."}
            ]
        return []

    def fetch(url):
        if url == profile:
            return {
                "url": profile,
                "status": 200,
                "text": identity + " We serve homeowners.",
                "links": [{"text": "Visit Website", "url": domain}],
            }
        assert url == domain
        return {"url": url, "status": 200, "text": identity + " Call us for an estimate.", "links": []}

    q = research.qualify(case["lead"], None, search, fetcher=fetch)
    assert len(queries) == 3 and q["status"] == "skip"
    assert q["website"] == domain and q["website_status"] == "has_site" and not q.get("follow_up")


def test_nonportfolio_illustrations_and_partner_logos_do_not_become_painter_work():
    from bs4 import BeautifulSoup

    import business_profile

    soup = BeautifulSoup(
        """
        <a href="https://supplier.example"><img alt="Partner logo" src="/logos/partner.png"></a>
        <img alt="Freshly painted kitchen" src="/assets/sample.jpg">
        <section id="gallery"><img alt="Exterior restoration in Glendale" src="/projects/job.jpg">
        <img src="/projects/finished-room.jpg"></section>
        <header><a href="/"><img alt="Studio logo" src="/logos/self.png"></a></header>""",
        "html.parser",
    )
    images = business_profile.page_images(soup, "https://owner.example/")
    assert [i["asset_url"] for i in images] == [
        "https://owner.example/projects/job.jpg",
        "https://owner.example/projects/finished-room.jpg",
        "https://owner.example/logos/self.png",
    ]
    assert images[1]["alt"] == "Company gallery photo 4"


def test_bundled_fictional_gallery_is_labeled_and_has_no_external_media_dependency():
    import business_profile

    case = CASES[1]
    q = research.qualify(
        case["lead"],
        None,
        lambda _: case["results"],
        fetcher=case["pages"].__getitem__,
        image_reader=lambda url, kind: business_profile.fixture_image(case["image_files"], url, kind),
    )
    item = q["profile"]["media"][0]
    assert item["kind"] == "project" and "Staged" in item["alt"] and "Pexels" in item["credit"]
    assert item["url"].startswith("data:image/jpeg;base64,")
    assert business_profile.fixture_image({"x": "../.env"}, "x", "project") == ""


@pytest.mark.parametrize("phone,expected", [("(818) 555-0101", 2), ("(213) 555-0177", 0)])
def test_qualified_lead_discovers_and_matches_a_gallery_before_importing_it(
    monkeypatch, phone, expected
):
    """A normal discovery run can find real gallery media without operator-provided asset URLs."""
    case = CASES[0]
    lead = case["lead"]
    page_url = "https://local.yahoo.com/info-123-copper-finch-glendale/"
    photos = ["https://s.yimg.com/bj/aaaa/aaaa01.jpg", "https://s.yimg.com/bj/bbbb/bbbb02.jpg"]
    quote = "The freshly painted trim looks wonderful."
    node = {
        "@type": "HousePainter",
        "name": lead["name"],
        "url": page_url,
        "telephone": phone,
        "address": {"addressLocality": lead["city"]},
        "image": photos[:1],
        "review": {"@type": "Review", "author": {"name": "A. R."}, "reviewBody": quote},
    }
    html = (
        '<div id="business-photo-grid"></div>'
        + "".join(
            f'<meta property="og:image" content="{photo}">'
            for photo in photos + ["https://other.example/ad.jpg"]
        )
        + f'<script type="application/ld+json">{json.dumps(node)}</script>'
    )
    monkeypatch.setattr(
        research, "read_public", lambda *args: (page_url, 200, "text/html", html.encode())
    )
    queries = []

    def search(query):
        queries.append(query)
        if "site:local.yahoo.com" in query:
            return [{"url": page_url, "title": "Paint gallery"}]
        return [
            {
                **case["results"][0],
                "url": "https://www.yelp.com/biz/copper-finch",
                "text": lead["name"] + " " + lead["phone"] + ". Messaging is disabled. 9 photos.",
            },
            case["results"][1],
        ]

    q = research.qualify(
        lead, None, search, fetcher=research.fetch_page, image_reader=lambda url, kind: url
    )
    assert len(queries) == 3 and q["status"] == "qualified"
    assert [item["asset_url"] for item in q["profile"]["media"]] == photos[:expected]
    reviews = q["profile"]["testimonials"]
    assert len(reviews) == int(bool(expected))
    if expected:
        evidence = next(item for item in q["evidence"] if item["id"] == reviews[0]["evidence_id"])
        assert evidence["url"] == page_url and quote in evidence["text"] and "A. R." in evidence["text"]


def test_first_party_work_and_customer_quotes_keep_their_sources():
    import business_profile
    from bs4 import BeautifulSoup

    html = '<section id="testimonials"><blockquote>Lovely new door.<cite>F. Lee</cite></blockquote></section><blockquote>An unrelated quotation.</blockquote>'
    assert business_profile.quoted_customers(BeautifulSoup(html, "html.parser")) == [
        {"author": "F. Lee", "quote": "Lovely new door."}
    ]
    case = CASES[1]
    q = research.qualify(
        case["lead"],
        None,
        lambda _: case["results"],
        fetcher=case["pages"].__getitem__,
        image_reader=lambda url, kind: business_profile.fixture_image(case["image_files"], url, kind),
    )
    assert len(q["profile"]["media"]) == 3
    for quote in q["profile"]["testimonials"]:
        assert "fictional customer" in quote["author"]
        source = next(e for e in q["evidence"] if e["id"] == quote["evidence_id"])
        assert source["url"] == quote["source_url"] and quote["quote"] in source["text"]


def test_abbreviated_domain_with_distinct_business_switchboard_and_published_license():
    lead = {
        "id": "986754",
        "name": "Patterson-JCH Construction & Consulting",
        "city": "Burbank",
        "phone": "818-555-0119",
    }
    url = "https://pat-jch.example/"
    name = lead["name"]
    screen = {
        "url": url,
        "status": 200,
        "title": f"Home | {name}",
        "text": f"{name}. We offer exterior work. California Contractor’s License #986754. Contact (800) 555-0150.",
        "links": [
            {"text": "Portfolio", "url": url + "portfolio"},
            {"text": "Contact", "url": url + "contact"},
        ],
    }

    def check(page):
        return research.qualify(
            lead,
            None,
            lambda _: [{"url": url, "title": name, "text": "Call (800) 555-0150"}],
            fetcher=lambda _: page,
        )

    assert check(screen)["status"] == "skip"
    assert check({**screen, "text": screen["text"].replace("986754", "986755")})["status"] == "hold"


def test_social_probe_requires_a_matched_bio_and_checks_its_website(monkeypatch, tmp_path):
    lead = {"id": "987654", "name": "Avery Gomez Painting", "city": "Pasadena", "phone": "626-555-0150"}
    handle = "https://www.instagram.com/averygpainting/"
    website = "https://averygomezpainting.example/"
    other = {
        "url": "https://www.yelp.com/biz/avery-gomez",
        "title": lead["name"],
        "text": "Pasadena. (626) 555-0150.",
    }
    profile = {
        "url": handle,
        "status": 200,
        "social_bio": "Avery Gomez. License 987654 estimates@example.org 10 posts.",
        "social_photos": [{}],
        "bio_links": [],
    }
    links = []

    def fetch(url):
        links.append(url)
        if url == handle:
            return profile
        if url == website:
            return {
                "url": website,
                "status": 200,
                "text": lead["name"] + " License 987654. Call (626) 555-0150.",
                "links": [],
            }
        return {"url": url, "status": 404, "text": "", "links": []}

    monkeypatch.setattr(research, "fetch_page", fetch)
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    monkeypatch.setattr(research, "fetch_image", lambda *args: "")

    # A previous index may know only this one directory. Licenses establish ownership of proposed handles.
    def check():
        return research.qualify(lead, None, lambda _: [other])

    first = check()
    assert research.qualify(lead, None, lambda _: [])["status"] == "qualified"
    assert (
        first["status"] == "qualified"
        and first["social"] == handle
        and first["email"] == "estimates@example.org"
    )
    profile["bio_links"] = [website]
    assert check()["status"] == "skip" and website in links
    profile["social_bio"] = "Someone Else. License 987654. estimates@example.org."
    assert check()["status"] == "hold"
    profile["social_bio"] = "Avery Gomez. License 987655. estimates@example.org."
    assert check()["status"] == "hold"


def test_syndicated_facebook_page_must_confirm_registered_phone_before_use():
    import business_profile
    from bs4 import BeautifulSoup

    lead = CASES[0]["lead"]
    source = "https://www.glonstruct.com/US/Glendale/123456789012345/Copper-Finch"
    url = "https://www.facebook.com/CopperFinch/"

    def card(phone):
        def contact(owner, value):
            return {
                "node": {
                    "timeline_context_item": {
                        "renderer": {
                            "associated_page_id": owner,
                            "context_item": {"title": {"text": value}},
                        }
                    }
                }
            }

        items = [
            {
                "profile_tile_items": {
                    "nodes": [
                        contact("123456789012345", "House Painting"),
                        contact(None, phone),
                        contact(None, "estimates@example.com"),
                    ]
                }
            },
            {"profile_tile_items": {"nodes": [contact("999", "someoneelse@example.com")]}},
        ]
        html = f'<meta property="og:url" content="{url}"><meta property="og:description" content="Copper Finch Painting, Glendale. Interior painting."><script type="application/json">{json.dumps(items)}</script>'
        return {
            "url": url,
            "status": 200,
            **business_profile.facebook_profile(
                BeautifulSoup(html, "html.parser"), url, "123456789012345"
            ),
        }

    for phone, matched in [(lead["phone"], True), ("(213) 555-0181", False)]:
        records = {source: f"{lead['name']}, {lead['phone']}. Website facebook.com"}
        research.read_directory_links(lead, records, lambda _: card(phone), lambda _: None)
        assert (url in records) == matched
        if matched:
            assert (
                "estimates@example.com" in records[url]
                and "someoneelse@example.com" not in records[url]
            )


def test_state_license_record_does_not_veto_independently_confirmed_portfolio():
    case = CASES[0]
    lead = case["lead"]
    path = "/record/CA/" + lead["id"]
    text = f"{lead['name']} in {lead['city']}. California contractor license {lead['id']}. CSLB master license list."
    url = "https://public-records.example" + path
    entry = {"title": "Contractor record", "text": text, "url": url}
    result = run(case, results=case["results"] + [entry])
    assert result["status"] == "qualified"
    assert any(e["url"] == url and e["kind"] == "assessment" for e in result["evidence"])
    assert not research.registry_mirror("https://copperfinchpainting.example" + path, text, lead)
    assert not research.registry_mirror("https://workshop.example" + path, lead["name"], lead)


def test_unverified_directory_phone_matters_only_if_it_identifies_a_plausible_website():
    case = CASES[0]
    lead = case["lead"]
    directory = "https://trade-cards.example/b/painting-4"
    conflict = {"title": lead["name"], "url": directory, "text": f"{lead['city']} (408) 555-0147."}
    assert run(case, results=case["results"] + [conflict])["status"] == "qualified"
    possible_website = "https://copperfinchpainting.example"
    marked = {**conflict, "text": conflict["text"] + " " + possible_website}
    calls = []
    def fetch(url):
        calls.append(url)
        assert url == possible_website
        return {"url": url, "status": 0, "text": ""}
    result = research.qualify(lead, None, lambda _: case["results"] + [marked], fetcher=fetch)
    follow_up = result["follow_up"]
    assert result["status"] == "hold" and calls.count(possible_website) == 2
    assert follow_up["source_url"] == directory and follow_up["url"] == possible_website
    assert set(follow_up["evidence_ids"]) <= set(result["retrieved_ids"])


def test_an_unknown_marketplace_can_propose_a_real_website_but_cannot_veto_as_owner():
    case = CASES[0]
    lead = case["lead"]
    url = "https://find-pros.example/painters-in/california/glendale/copper-finch?pid=123"
    hit = {"url": url, "title": lead["name"], "text": f"{lead['city']} {lead['phone']}. Find a local painter. Pro login."}
    result = run(case, results=[hit, *case["results"]])
    assert result["status"] == "qualified"
    assert any(e["url"] == url and "marketplace" in e["text"] for e in result["evidence"])
    # A business-owned site hosted on a shared platform can be the actual official website.
    site = "https://sites.google.com/view/finch-studio"
    hit["text"] += " Website: " + site
    pages = {site: {"url": site, "status": 200, "title": lead["name"], "text": f"{lead['name']}. Call us {lead['phone']} for an estimate.", "links": []}}
    linked = run({**case, "pages": pages}, results=[hit, *case["results"]])
    assert linked["status"] == "skip" and linked["website"] == site
    add = {"url": "https://unknown.example/ca/glendale/painting/", "title": lead["name"], "text": f"42 painters on this list. {lead['name']} {lead['phone']}. Another company. Website: {site}"}
    assert run(case, results=[add, *case["results"]])["status"] == "qualified"
    assert not research.directory_publisher(lead, url.replace("find-pros.example", "copperfinchpainting.example"), hit["text"])
    zip_page = "https://trade-directory.example/find-a-pro/painting/90304"
    assert research.directory_publisher(lead, zip_page, "10 providers listed in Inglewood")
    assert not research.directory_publisher(lead, zip_page, "Copper Finch Painting serves Inglewood.")

def test_verified_directory_profile_paths_do_not_hide_a_possible_owner_site():
    case = CASES[0]
    lead = case["lead"]
    urls = [
        "https://uslocaldir.com/copper-finch-painting-glendale-ca-91201.xhtml",
        "https://prismpaintingcompany.com/business/copper-finch-painting-ca-1122/",
        "https://a1cleaningrestoration.com/business/copper-finch-painting-ca-3344/",
    ]
    def hit(url):
        return {"url": url, "title": lead["name"], "text": f"{lead['city']} {lead['phone']}."}
    hits = [hit(url) for url in urls] + case["results"]
    result = run(case, results=hits)
    assert result["status"] == "qualified"
    assert set(urls) <= {e["url"] for e in result["evidence"] if e["kind"] == "assessment"}

    owner = "https://finch-studio.example/business/copper-finch-painting-ca-1122/"
    private_page = {"url": owner, "status": 403, "text": ""}
    assert run({**case, "pages": {owner: private_page}}, results=[*hits, hit(owner)])["status"] == "hold"
    assert not research.directory_publisher(lead, "https://prismpaintingcompany.com/contact", "")
    assert not research.directory_publisher({"name": "Prism Painting Company", "city": "Glendale"}, urls[1], "")

def test_license_query_and_owner_site_retry_after_timeout():
    # A business can publish a sales line that differs from the registry phone.
    lead = CASES[0]["lead"]
    website = "https://copperfinchpainting.example/"
    called, queries = [], []

    def search(query):
        queries.append(query)
        if lead["id"] in query:
            return [{"url": website, "title": lead["name"], "text": f"License #{lead['id']}. {lead['city']}."}]
        return []

    def fetch(url):
        assert url == website
        called.append(url)
        if len(called) == 1:
            return {"url": url, "status": 0}
        return {
            "url": url, "status": 200, "title": lead["name"],
            "text": f"{lead['name']} · CSLB #{lead['id']}. Call us for an estimate on 310-555-0102.",
            "links": [],
        }

    result = research.qualify(lead, None, search, fetcher=fetch)
    assert len(called) == 2 and len(queries) <= 3
    assert result["website"] == website and result["status"] == "skip"


@pytest.mark.parametrize("contact_status, expected", [(200, "skip"), (403, "hold")])
def test_restricted_service_page_checks_home_and_another_search_result(contact_status, expected):
    lead = CASES[0]["lead"]
    home = "https://copperfinchpainting.example/"
    service, contact = home + "service-areas/glendale", home + "contact"
    hits = [{"url": u, "title": lead["name"], "text": lead["city"]} for u in (service, contact)]
    calls = []

    def fetch(url):
        calls.append(url)
        if url == contact and contact_status == 200:
            return {"url": url, "status": 200, "text": f"{lead['name']}. Call us on {lead['phone']}.", "links": []}
        return {"url": url, "status": 403, "text": ""}

    q = research.qualify(lead, None, lambda query: hits, fetcher=fetch)
    assert calls == [service, home, contact]
    assert q["status"] == expected
    if expected == "skip":
        assert q["website"] == contact
    else:
        assert q["follow_up"]["url"] == service
        assert "HTTP 403" in q["reason"] and q["website_status"] == "unsure"


def test_disconnected_homepage_requires_registered_phone_and_repeat_host_error(monkeypatch):
    lead = CASES[0]["lead"]
    domain, listing = "https://copperfinchpainting.example", "https://www.contractorsup.com/123/copper-finch"
    hits = [{"url": listing, "title": lead["name"], "text": lead["city"]}]
    markup = b"<html><title>ConnectYourDomain Error | Wix.com</title></html>"
    monkeypatch.setattr(research, "read_public", lambda url, *_: (url, 404, "text/html", markup))
    error_page = research.fetch_page(domain)
    assert error_page["disconnected_domain"]
    for phone, first_code, second_code, wix, redirect, expected in [
        (lead["phone"], 404, 404, True, "", "listed_homepage_disconnected"),
        (lead["phone"], 404, 404, False, "", "listed_homepage_missing"),
        (lead["phone"], 410, 410, False, "", "listed_homepage_missing"),
        (lead["phone"], 404, 403, False, "", "hold"),
        (lead["phone"], 0, 0, False, "", "hold"),
        (lead["phone"], 404, 404, False, "/different-page", "hold"),
        ("310-555-0101", 404, 404, True, "", "hold"),
    ]:
        calls = []

        def fetch(url):
            if url == listing:
                return {"url": listing, "status": 200, "text": f"{lead['name']}. {lead['city']}.",
                        "links": [{"text": "Call", "url": f"tel:{phone}"}, {"text": "Website", "url": domain}]}
            assert url == domain
            calls.append(url)
            return {**error_page, "url": url + redirect,
                    "status": first_code if len(calls) == 1 else second_code, "disconnected_domain": wix}

        q = research.qualify(lead, None, lambda query: hits, fetcher=fetch)
        assert calls == [domain, domain]
        if expected == "hold":
            assert q["status"] == "hold"
        else:
            assert q["status"] == "qualified" and q["site_issue"] == expected
            assert len(set(q["issue_evidence_ids"])) == 3
            assert set(q["issue_evidence_ids"]) <= set(q["retrieved_ids"])
            from studio import build_preview, angles_for
            first = angles_for(lead, q, build_preview(lead, q))[0]
            assert "website link on your business listing twice" in first["opening"]
            assert set(q["issue_evidence_ids"]) <= set(first["evidence_ids"])

    # A healthy replacement wins even when the officially listed link was broken.
    replacement = "https://copperfinchpaintingnew.example/"
    def competing(url):
        if url == replacement:
            return {"url": url, "status": 200, "title": lead["name"], "has_form": True,
                    "links": [], "text": f"{lead['name']}. Call us at {lead['phone']} for a quote."}
        if url == listing:
            return {"url": url, "status": 200, "text": f"{lead['name']}. {lead['city']}.",
                    "links": [{"text": "Call", "url": "tel:" + lead["phone"]}, {"text": "Website", "url": domain}]}
        assert url == domain
        return {**error_page, "disconnected_domain": False}
    q = research.qualify(lead, None, lambda query: hits + [
        {"url": replacement, "title": lead["name"], "text": f"{lead['city']} {lead['phone']}"}
    ], fetcher=competing)
    assert q["status"] == "skip" and q["website"] == replacement


def test_procurement_pdf_is_evidence_and_never_the_business_website():
    case = CASES[0]
    lead = case["lead"]
    pdf = "https://bidders.example/2026/award-notice.PDF"
    item = {"url": pdf, "title": "Procurement award register", "text": f"{lead['name']}, {lead['city']}: {lead['phone']}."}
    # run() will also reject an attempt to fetch the document as an owner page.
    q = run(case, results=[*case["results"], item])
    assert q["status"] == "qualified" and any(r["url"] == pdf for r in q["evidence"])
    owner = "https://copperfinchpainting.example"
    pages = {**case["pages"], owner: {"url": owner, "status": 200, "links": [], "has_form": True,
                                      "text": f"We are {lead['name']}. Call us at {lead['phone']}."}}
    q = run({**case, "pages": pages}, results=[*case["results"], {**item, "text": item["text"]+" Website: "+owner}])
    assert q["status"] == "skip" and q["website"] == owner



def test_trade_name_paint_or_painting_requires_the_same_registered_phone():
    lead = {**CASES[0]["lead"], "name": "Copper Finch Paint"}
    for phone, expected in [(lead["phone"], 2), ("310-555-0123", 0)]:
        assert research.identity(lead, f"Copper Finch Painting serves Glendale. Call {phone}.") == expected


def test_namesake_needs_a_declared_base_and_two_foreign_cities_to_dismiss():
    lead = CASES[0]["lead"]
    website = "https://copperfinch.example/"
    text = "Other Construction Co. Our projects in Paterson NJ and Elmwood NJ. Call 201-555-0123."
    notes = []

    def check(body):
        page = {"url": website, "status": 200, "text": body, "links": []}
        return research.inspect_site(lead, website, set(), {}, {"email": ""}, lambda url: page,
                                     lambda url, note, kind: notes.append(note))

    assert check(text)[0] == "hold"
    assert check(text + " Family-owned and operated in North Jersey.") is None
    assert check(text + " Based in New Jersey. Now serving California.")[0] == "hold"
    assert research.registry_mirror("https://directory.example/c/123", "Is this your company? Claim it free.", lead)
    thin = {"email": ""}
    research.inspect_site(lead, website, set(), {}, thin,
                          lambda u: {"url": u, "status": 200, "text": "Copper Finch Painting", "links": []},
                          lambda *args: "e1")
    assert thin["follow_up"]["url"] == website and "Open it in a browser" in thin["follow_up"]["reason"]


def test_shell_uses_remaining_search_for_verified_same_domain_contact():
    lead = CASES[0]["lead"]
    home = "https://copperfinchpainting.example/"
    contact = "https://residential.copperfinchpainting.example/"
    unrelated = "https://copperfinchpainting.example.other.test/contact"
    identity = f"{lead['name']} in {lead['city']}. Call {lead['phone']} for an estimate."

    for has_phone, used_all_searches, expected in [(True, False, "skip"), (False, False, "hold"), (True, True, "hold")]:
        calls, fetched = [], []

        def search(query):
            calls.append(query)
            if query.startswith("site:"):
                return [{"url": u, "text": identity} for u in (unrelated, contact)]
            return [{"url": home, "text": lead["name"] if used_all_searches else identity}]

        def fetch(url):
            fetched.append(url)
            assert url in {home, contact}
            body = identity if url == contact and has_phone else lead["name"]
            return {"url": url, "status": 200, "text": body, "links": []}

        result = research.qualify(lead, None, search, fetcher=fetch)
        assert len(calls) == (3 if used_all_searches else 2)
        assert result["status"] == expected
        if expected == "skip":
            assert result["website"] == contact and contact in fetched
        else:
            assert result["follow_up"]["url"] == home and result["website_status"] == "unsure"


def test_matching_logo_and_printed_domain_are_verified_before_the_build():
    import business_profile

    case = CASES[0]
    site = "https://copperfinchpainting.example"
    pages = {**case["pages"], site: {
        "url": site, "status": 200, "text": "Copper Finch Painting. Call us at (818) 555-0101 for an estimate.",
        "has_form": True, "links": [], "title": "Copper Finch Painting",
    }}
    vision_calls = []

    def vision(prompt, photos):
        vision_calls.append(len(photos))
        return {"logo_photo": 1, "logo_name": "Copper Finch Painting", "domain_photo": 1,
                "printed_domain": "copperfinchpainting.example", "portfolio_photos": [2], "hero_photo": 2}

    result = research.qualify(case["lead"], lambda _: {}, lambda _: case["results"],
                              fetcher=lambda u: pages[u], photo_model=vision,
                              image_reader=lambda u, kind: business_profile.fixture_image(case["image_files"], u, kind))
    assert vision_calls == [2, 1]
    assert result["status"] == "skip" and result["website"] == site
    logo = next(m for m in result["profile"]["media"] if m["kind"] == "logo")
    assert logo["evidence_id"] == result["printed_website"]["evidence_id"]
    assert any("Visual reading" in row["text"] for row in result["evidence"])
