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
        return {"passed": True, "feedback": "Grounded."}

    pitch = write_pitch(LEAD, QUALIFIED, page, model)
    assert pitch["mode"] == "two-depth" and pitch["angle_id"] == "specialty"
    assert pitch["runner_up"]["id"] == "site" and pitch["runner_up"]["evidence_ids"] == [
        "registry",
        "e2",
    ]
    assert "interior painting" in pitch["body"] and "set it up for you" in pitch["body"]
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
    changed_page = {**page, "html": page["html"].replace('id="brief-button"', 'id="removed"')}
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
    assert result["passed"] and not result["model_checked"]


def test_every_angle_keeps_the_specific_finding_and_a_failed_review_stays_held_on_outage():
    page = preview()
    pitch = write_pitch(LEAD, QUALIFIED, page, None, feedback="Check the issue again.")
    assert "HTTP 404 twice" in pitch["body"]
    assert all(
        "HTTP 404 twice" in a["opening"] and "e2" in a["evidence_ids"] for a in pitch["planned_angles"]
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
        "couldn’t verify" in a["opening"] and "social1" in a["evidence_ids"]
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
        "messaging isn’t enabled" in a["opening"] and "yelp" in a["evidence_ids"]
        for a in draft["planned_angles"]
    )
    assert review(LEAD, q, page, draft, None)["passed"]


def test_registry_typography_preserves_identity_and_suffixes():
    from studio import display_name

    assert display_name("A & B PAINTING LLC") == "A & B Painting LLC"
    assert display_name("McKenna Painting") == "McKenna Painting"
