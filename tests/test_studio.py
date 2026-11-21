import pytest

from studio import build_preview, review, write_pitch

LEAD = {"id": "fixture-6", "name": "Tamarind Painting", "city": "Pasadena"}
QUALIFIED = {
    "status": "qualified",
    "website_status": "has_site",
    "issue": "The linked contact page returned HTTP 404 twice.",
    "issue_evidence_ids": ["e2"],
    "evidence": [
        {
            "id": "registry",
            "url": "https://example.org/registry",
            "text": "Tamarind Painting. Pasadena. Painting and decorating.",
        },
        {
            "id": "e1",
            "url": "https://example.org/services",
            "text": "Interior painting. Serving Pasadena.",
        },
        {
            "id": "e2",
            "url": "https://example.org/contact",
            "text": "The linked contact page returned HTTP 404 twice.",
        },
    ],
    "facts": {
        "services": ["Painting and decorating", "Interior painting", "Cabinet refinishing"],
        "service_area": "Pasadena",
        "citations": {
            "Painting and decorating": "registry",
            "Interior painting": "e1",
            "Cabinet refinishing": "e1",
            "Pasadena": "e1",
        },
    },
}


def preview():
    return {**build_preview(LEAD, QUALIFIED), "url": "https://example.org/p/a"}


def test_preview_uses_only_grounded_claims_and_portable_content():
    page = preview()
    assert page["variant"] == "Atelier"
    assert "Interior painting" in page["html"] and "Cabinet refinishing" not in page["html"]
    assert "data:image/jpeg;base64," in page["html"] and "noindex" in page["html"]
    assert "form-action 'none'" in page["html"] and "Stock photograph" in page["html"]
    assert {"text": "Pasadena", "evidence_id": "e1", "kind": "area"} in page["claims"]


@pytest.mark.parametrize(
    "change",
    [
        {"status": "hold"},
        {"website_status": "unsure"},
        {"evidence": []},
        {"evidence": [{"id": "registry", "text": "Imposter. Pasadena."}]},
    ],
)
def test_only_eligible_matched_business_gets_a_preview(change):
    with pytest.raises(ValueError):
        build_preview(LEAD, {**QUALIFIED, **change})


def test_two_depth_retains_competing_observations_then_changes_the_winner():
    page = preview()
    calls = []

    def model(prompt):
        calls.append(prompt.splitlines()[0])
        if prompt.startswith("STUDIO_PLAN"):
            return {
                "angles": [
                    {"id": "site", "score": 8.5},
                    {"id": "specialty", "score": 8.9},
                    {"id": "local", "score": 5},
                ]
            }
        if prompt.startswith("STUDIO_WRITE"):
            return {
                "drafts": [
                    {"id": "site", "score": 7},
                    {"id": "specialty", "score": 9, "invitation": "feedback"},
                ]
            }
        import json

        evidence = json.loads(prompt.splitlines()[-1])["evidence"]
        assert all(entry.get("source_url") and entry.get("passage") for entry in evidence.values())
        return {"passed": True, "feedback": "Grounded."}

    pitch = write_pitch(LEAD, QUALIFIED, page, model)
    assert pitch["mode"] == "two-depth" and pitch["angle_id"] == "specialty"
    assert pitch["runner_up"]["id"] == "site" and pitch["runner_up"]["evidence_ids"] == [
        "registry",
        "e2",
    ]
    assert "interior painting" in pitch["body"] and "What would you change" in pitch["body"]
    result = review(LEAD, QUALIFIED, page, pitch, model)
    assert result["passed"] and result["model_checked"]
    assert calls == ["STUDIO_PLAN", "STUDIO_WRITE", "STUDIO_REVIEW"]
    calls.clear()
    write_pitch(LEAD, QUALIFIED, page, model, branch=False)
    assert calls == ["STUDIO_WRITE"]


def test_fallback_label_and_prepublication_review():
    page = preview()
    pitch = write_pitch(LEAD, QUALIFIED, page, None)
    assert pitch["mode"] == "template" and pitch["angle_id"] == "site"
    assert review(LEAD, QUALIFIED, page, pitch, None)["passed"]
    for changed in [
        {**pitch, "body": pitch["body"] + " We guarantee 2x sales."},
        {**pitch, "evidence_ids": ["other"]},
    ]:
        assert not review(LEAD, QUALIFIED, page, changed, None)["passed"]
    changed_page = {**page, "html": page["html"].replace('id="request-button"', 'id="removed"')}
    assert not review(LEAD, QUALIFIED, changed_page, pitch, None)["passed"]
    assert not review(
        LEAD, QUALIFIED, page, pitch, lambda _: {"passed": False, "feedback": "Recheck."}
    )["passed"]


def test_thin_evidence_skips_branching_and_model_failure_is_labeled():
    q = {
        **QUALIFIED,
        "website_status": "no_site_found",
        "issue": "",
        "issue_evidence_ids": [],
        "facts": {},
    }
    page = {**build_preview(LEAD, q), "url": "/p/a"}
    calls = []

    def offline(prompt):
        calls.append(prompt.splitlines()[0])
        raise OSError("Unavailable")

    pitch = write_pitch(LEAD, q, page, offline)
    assert calls == ["STUDIO_WRITE"] and pitch["mode"] == "template"
    assert "no website" not in pitch["body"].lower()
    result = review(LEAD, q, page, pitch, offline)
    assert not result["passed"] and not result["model_checked"]
    assert not result["checks"][
        "The concept has a business-specific contact, service or portfolio source"
    ]


def test_every_angle_keeps_the_specific_finding_and_a_failed_review_stays_held_on_outage():
    page = preview()
    pitch = write_pitch(LEAD, QUALIFIED, page, None, feedback="Check the issue again.")
    assert "missing page both times" in pitch["body"]
    assert all(
        "missing page both times" in a["opening"] and "e2" in a["evidence_ids"]
        for a in pitch["planned_angles"]
    )
    assert not review(LEAD, QUALIFIED, page, pitch, None)["passed"]
    url = "https://www.instagram.com/painter/"
    q = {
        **QUALIFIED,
        "website_status": "no_site_found",
        "social": url,
        "evidence": [
            *QUALIFIED["evidence"],
            {"id": "social1", "url": url, "text": "Tamarind Painting business profile."},
        ],
    }
    pitch = write_pitch(LEAD, q, {**build_preview(LEAD, q), "url": "/p/a"}, None)
    assert all(
        "your Instagram page" in a["opening"]
        and "turned off" not in a["opening"]
        and "social1" in a["evidence_ids"]
        for a in pitch["planned_angles"]
    )


def test_directory_only_with_disabled_messaging_preserves_the_finding_in_every_draft():
    first = {
        "id": "yelp",
        "url": "https://www.yelp.com/biz/example",
        "text": "This business has not enabled messaging.",
    }
    second = {
        "id": "listing",
        "url": "https://example.org/painter",
        "text": "Tamarind Painting in Pasadena.",
    }
    q = {
        **QUALIFIED,
        "website_status": "no_site_found",
        "social": "",
        "issue": "Two searches found no owned estimate page.",
        "issue_evidence_ids": ["yelp", "listing"],
        "evidence": [QUALIFIED["evidence"][0], first, second],
    }
    page = {**build_preview(LEAD, q), "url": "/p/a"}
    draft = write_pitch(LEAD, q, page, None)
    assert draft["angle_id"] == "profile"
    assert all(
        "your Yelp page" in a["opening"] and "yelp" in a["evidence_ids"]
        for a in draft["planned_angles"]
    )
    # A registry-only concept without a contact, specialty or real gallery stays held.
    assert not review(LEAD, q, page, draft, None)["passed"]


def test_registry_typography_preserves_identity_and_suffixes():
    from studio import display_name

    assert display_name("A & B PAINTING LLC") == "A & B Painting LLC"
    assert display_name("McKenna Painting") == "McKenna Painting"
    assert display_name("ARAY'S HOUSE PAINTING") == "Aray's House Painting"


def test_business_phone_and_gallery_reach_the_artifact_and_pitch():
    lead = {**LEAD, "phone": "(818) 555-0101"}
    url = "https://www.yelp.com/biz/fixture"
    q = {
        **QUALIFIED,
        "website_status": "no_site_found",
        "phone": lead["phone"],
        "issue_evidence_ids": ["e3", "e1"],
        "profile": {"work_links": [{"url": url, "label": "9 photos on Yelp", "evidence_id": "e3"}]},
        "evidence": [
            {**QUALIFIED["evidence"][0], "text": "Tamarind Painting. Pasadena. (818) 555-0101."},
            *QUALIFIED["evidence"][1:],
            {"id": "e3", "url": url, "text": "Tamarind Painting. See all 9 photos."},
        ],
    }
    page = {**build_preview(lead, q), "url": "/p/a"}
    from bs4 import BeautifulSoup

    soup = BeautifulSoup(page["html"], "html.parser")
    assert not soup.select_one("#project-type")
    assert soup.select_one("footer a[href*=yelp] svg") and soup.select_one(".concept-mark")
    assert page["html"].count('href="tel:+18185550101"') == 2
    assert 'href="https://www.yelp.com/biz/fixture"' in page["html"]
    assert (
        page["section"] == "#estimate"
        and "9 photos" in page["handoff"]
        and "e3" in page["evidence_ids"]
    )
    pitch = write_pitch(lead, q, page, None)
    assert pitch["opening"] in pitch["body"] and "links to your gallery" in pitch["opening"]
    assert review(lead, q, page, pitch, None)["passed"]
    wrong = {**page, "handoff": "Imported their photos."}
    assert not review(lead, q, wrong, pitch, None)["passed"]


def test_uncorroborated_phone_logo_or_unrelated_portfolio_does_not_render():
    q = {
        **QUALIFIED,
        "phone": "(818) 555-0101",
        "profile": {
            "media": [
                {
                    "kind": "logo",
                    "evidence_id": "e1",
                    "source_url": "https://unrelated.org",
                    "url": "data:image/png;base64,YQ==",
                }
            ],
            "work_links": [
                {"evidence_id": "e2", "url": "https://unrelated.org", "label": "Foreign work"}
            ],
        },
    }
    page = build_preview(LEAD, q)
    assert 'href="tel:' not in page["html"] and "unrelated.org" not in page["html"]
    assert "Business logo" not in page["features"]


def test_synthetic_portfolio_keeps_photo_visible_and_its_gallery_link_local():
    import pipeline
    from bs4 import BeautifulSoup

    case = next(c for c in pipeline.scenarios() if c["id"] == "broken-estimate")
    q = pipeline.qualify_fixture(case)
    page = build_preview(case["lead"], q)
    soup = BeautifulSoup(page["html"], "html.parser")
    assert soup.select_one(".hero-art img")["src"].startswith("data:image/jpeg;base64,")
    assert "staged" in soup.select_one(".hero-credit .source").get_text().lower()
    assert soup.select_one(".hero-proof")["href"] == "#work" and soup.select_one("#work img")
    assert not any(".example/" in a.get("href", "") for a in soup.select("a"))

    assert len(soup.select("#work [data-slide]")) == 3
    assert len(soup.select("#reviews [data-slide]")) == 2
    assert "fictional customer" in soup.select_one("#reviews").get_text()
    assert not soup.select_one("[data-color], #color-reference")
    assert soup.select_one("#reviews [data-rotate]")
    assert "Send request" in soup.select_one("#request-button").get_text()
    assert "Preview estimate request form" in page["features"]
    cards = soup.select("a[data-service]")
    assert cards and cards[0].select_one("svg")
    choices = [option.text for option in soup.select("#project-type option")]
    assert all(card["data-service"] in choices for card in cards) if len(cards) > 1 else not choices


def test_testimonial_requires_quote_and_reviewer_in_the_original_source():
    source = "https://example.org/services"
    item = {
        "author": "Alex",
        "quote": "We loved the results.",
        "evidence_id": "e1",
        "source_url": source,
    }
    q = {**QUALIFIED, "profile": {"testimonials": [item]}}
    assert "Attributed customer reviews" not in build_preview(LEAD, q)["features"]
    q["evidence"] = [
        {**src, "text": "Alex: We loved the results."} if src["id"] == "e1" else src
        for src in QUALIFIED["evidence"]
    ]
    assert "Attributed customer reviews" in build_preview(LEAD, q)["features"]
    q["profile"]["testimonials"][0] = {**item, "author": "Someone else"}
    assert "Attributed customer reviews" not in build_preview(LEAD, q)["features"]


def test_testimonial_selection_checks_full_feedback_and_preserves_individual_stars():
    import research
    from bs4 import BeautifulSoup

    evidence = list(QUALIFIED["evidence"])
    profile = {"testimonials": []}
    url = "https://reviews.example/painter"

    def add(source, text, kind):
        eid = f"review-{len(evidence)}"
        evidence.append({"id": eid, "url": source, "text": text})
        return eid

    research.attach_testimonials(
        [
            {"author": "One", "rating": 2, "quote": "Unprofessional. Doesn't respond to messages."},
            {
                "author": "Two",
                "rating": 5,
                "quote": "Great ceiling work. However, they damaged our trim.",
            },
            {"author": "Three", "rating": 5, "quote": "The ceiling looks wonderful.", "date": "2024-10-08"},
            {"author": "Four", "quote": "The room looks beautiful."},
        ],
        url,
        "Review source",
        profile,
        add,
    )
    q = {**QUALIFIED, "evidence": evidence, "profile": profile}
    page = BeautifulSoup(build_preview(LEAD, q)["html"], "html.parser")
    cards = page.select("#reviews figure")
    assert len(cards) == 2
    assert page.select_one("#reviews-title").text == "Customer reviews"
    assert cards[0].select_one('[aria-label="5 out of 5 stars"]')
    assert cards[0].select_one('time[datetime="2024-10-08"]').text == "October 2024"
    assert not cards[1].select_one(".review-stars")
    profile["testimonials"][0]["date"] = "2025-10-08"
    assert "<time " not in build_preview(LEAD, q)["html"]
    profile["testimonials"][1]["rating"] = 5
    assert (
        len(BeautifulSoup(build_preview(LEAD, q)["html"], "html.parser").select("#reviews figure")) == 1
    )


def test_syndicated_praise_keeps_context_and_an_empty_review_section_is_omitted():
    from bs4 import BeautifulSoup
    import research

    source = "https://local.yahoo.com/info-1-elm/"
    yelp = "https://www.yelp.com/biz/elm-painting?hrid=abc"
    published = "The crew painted our front door last month. Their work was phenomenal. Not only was the color perfect, the finish was beautiful..."
    evidence = list(QUALIFIED["evidence"])
    profile = {"testimonials": [], "review_links": []}

    def add(url, text, kind):
        evidence.append({"id": "review", "url": url, "text": text})
        return "review"

    row = {"quote": published, "author": "J. Doe", "rating": 5, "source_url": yelp}
    research.attach_testimonials([row], source, "Yelp via Yahoo Local", profile, add)
    q = {**QUALIFIED, "evidence": evidence, "profile": profile}
    page = BeautifulSoup(build_preview(LEAD, q)["html"], "html.parser")
    assert published in evidence[-1]["text"]
    assert profile["testimonials"][0]["quote"] == "Their work was phenomenal."
    assert page.select_one(".review-source")["href"] == yelp.split("?")[0]
    assert page.select_one(".review-card a")["href"] == yelp

    profile["testimonials"] = []
    profile["review_links"] = [{"label": "Read reviews on Yahoo Local", "url": source, "evidence_id": "review"}]
    research.attach_testimonials([{**row, "quote": "Not only was the price great, they also damaged the floor."}],
                                source, "Yelp via Yahoo Local", profile, add)
    page = BeautifulSoup(build_preview(LEAD, q)["html"], "html.parser")
    assert not page.select_one('#reviews, a[href="#reviews"], .review-source')
    assert "Read reviews on Yahoo Local" not in page.get_text()


def test_five_star_endorsements_need_explicit_praise_without_a_contrary_remark():
    from business_profile import endorses_work

    assert endorses_work("They went above and beyond.", 5)
    assert endorses_work("I can't say enough good things about these painters!", 5)
    assert endorses_work("They are true professionals.", 5)
    assert not endorses_work("The finish was far from stellar.", 5)
    assert not endorses_work("Top-notch painting, but the crew never finished.", 5)
    assert not endorses_work("I cannot recommend this painter.", 5)


def test_instagram_portfolio_in_rehearsal_and_when_a_real_profile_hides_its_photos():
    import pipeline
    from bs4 import BeautifulSoup

    case = next(c for c in pipeline.scenarios() if c["id"] == "social-estimates")
    q = pipeline.qualify_fixture(case)
    preview = build_preview(case["lead"], q)
    soup = BeautifulSoup(preview["html"], "html.parser")
    assert len(soup.select("#work [data-slide]")) == 2
    assert soup.select_one("footer a[href='#work'] svg")
    assert "Sample Instagram gallery" in soup.select_one("#work").get_text()
    assert not soup.select('a[href*="instagram.com"]')
    assert any("Instagram" in note for note in preview["customizations"])

    profile = "https://www.instagram.com/a_verified_painter/"
    source = {"id": "profile", "url": profile, "text": "Identity-matched Instagram profile."}
    q = {**QUALIFIED, "social": profile, "evidence": [*QUALIFIED["evidence"], source]}
    page = build_preview(LEAD, q)
    soup = BeautifulSoup(page["html"], "html.parser")
    assert soup.select_one("#work a")["href"] == profile
    assert soup.select_one("footer a")["href"] == profile and "profile" in page["evidence_ids"]
    assert not soup.select("#work img")


def test_multiple_services_and_places_only_use_cited_facts():
    from bs4 import BeautifulSoup

    q = {
        **QUALIFIED,
        "facts": {
            **QUALIFIED["facts"],
            "service_areas": ["Pasadena", "Glendale", "Hollywood"],
            "citations": {**QUALIFIED["facts"]["citations"], "Glendale": "e1", "Hollywood": "e1"},
        },
        "evidence": [
            QUALIFIED["evidence"][0],
            {
                "id": "e1",
                "url": "https://example.org/services",
                "text": "Interior painting and cabinet refinishing. Serving Pasadena and Glendale.",
            },
            QUALIFIED["evidence"][2],
        ],
    }
    page = build_preview(LEAD, q)
    soup = BeautifulSoup(page["html"], "html.parser")
    assert {o.get_text() for o in soup.select("#project-type option")} >= {
        "Interior painting",
        "Cabinet refinishing",
    }
    assert [o.get_text() for o in soup.select(".area-list li")] == ["Pasadena", "Glendale"]


def test_small_gold_lettering_supplies_a_brand_accent_with_accessible_contrast():
    from PIL import Image
    import base64
    from io import BytesIO
    from studio import site_palette, luminance

    mark = Image.new("RGB", (96, 96), "black")
    mark.paste((178, 139, 64), (41, 34, 47, 42))
    data = BytesIO()
    mark.save(data, format="PNG")
    css = site_palette("data:image/png;base64," + base64.b64encode(data.getvalue()).decode(), True)
    colors = dict(rule.split(":") for rule in css.split(";"))
    def rgb(token):
        return tuple(bytes.fromhex(colors[token][1:]))
    r, g, b = rgb("--brand")
    assert r > g > b
    assert (luminance(rgb("--accent")) + .05) / (luminance(rgb("--deep")) + .05) >= 4.5
