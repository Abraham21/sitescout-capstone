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
            expected in e["text"] and e["url"] == case["expected"]["evidence_url"] for e in result["evidence"]
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
        not in run(social, model=lambda _: {"services": [{"value": "Cabinet refinishing", "cite": "e1"}]})[
            "facts"
        ]["services"]
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
    lead = {"name": "AB Painting and Design LLC", "id": "911112", "city": "Glendale", "phone": "(818) 555-0190"}
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
