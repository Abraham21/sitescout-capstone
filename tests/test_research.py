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
    assert "Exterior painting" in actual["facts"]["services"]
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
    assert not run(social, model=lambda _: {"service_area": {"value": "Pasadena", "cite": "registry"}})[
        "facts"
    ]["service_area"]
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
    lead = {**CASES[0]["lead"], "source_hints": ["https://www.houzz.com/professionals/painters/demo"]}
    domain = "https://copperfinch.example"
    profile = lead["source_hints"][0]
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
    q = run(case, results=[base, case["results"][1]])
    assert q["profile"]["work_links"][0]["label"] == "9 photos on Yelp"
    assert q["profile"]["review_links"][0]["url"] == base["url"] and not q["profile"]["media"]
    other = {**base, "text": base["text"].replace("(818) 555-0101", "(510) 555-0101")}
    assert not any(run(case, results=[other, case["results"][1]])["profile"].values())


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
        <section id="gallery"><img alt="Exterior restoration in Glendale" src="/projects/job.jpg"></section>
        <header><a href="/"><img alt="Studio logo" src="/logos/self.png"></a></header>""",
        "html.parser",
    )
    images = business_profile.page_images(soup, "https://owner.example/")
    assert [i["asset_url"] for i in images] == [
        "https://owner.example/projects/job.jpg",
        "https://owner.example/logos/self.png",
    ]


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
