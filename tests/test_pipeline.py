"""Graph boundaries that could otherwise let an unreviewed draft enter the queue."""

from concurrent.futures import ThreadPoolExecutor
import base64
from io import BytesIO

import pytest
from PIL import Image

import pipeline
import storage


def test_visual_qualifier_uses_one_numbered_sheet_and_preserves_photo_sources():
    from business_profile import small_raster
    from research import apply_photo_choices
    from services import contact_sheet

    photos = [
        {
            "url": small_raster((storage.ROOT / "assets" / name).read_bytes(), "project"),
            "alt": alt,
            "evidence_id": f"e{index}",
        }
        for index, (name, alt) in enumerate(
            [("green-room.jpg", "Published caption"), ("white-stucco.jpg", "Painter gallery photo 2")],
            1,
        )
    ]
    with Image.open(BytesIO(base64.b64decode(contact_sheet(photos)))) as sheet:
        assert sheet.size == (480, 180)
    # The large second read must preserve both ends of a wide wordmark.
    wide = Image.new("RGB", (1000, 180), "blue")
    wide.paste("red", (0, 0, 100, 180))
    wide.paste("green", (900, 0, 1000, 180))
    data = BytesIO()
    wide.save(data, format="PNG")
    encoded = "data:image/png;base64," + base64.b64encode(data.getvalue()).decode()
    with Image.open(BytesIO(base64.b64decode(contact_sheet([{"url": encoded}])))) as enlarged:
        assert enlarged.getpixel((24, 30))[0] > 225
        assert enlarged.getpixel((620, 30))[1] > 100
    profile = {"media": photos.copy()}
    verdict = {
        "hero_photo": 2,
        "photo_captions": [
            {"photo": 1, "caption": "Overriding a publisher supplied caption"},
            {"photo": 2, "caption": "White stucco walls beneath a clear sky"},
        ],
    }
    apply_photo_choices(profile, photos, verdict)
    assert [p["evidence_id"] for p in profile["media"]] == ["e2", "e1"]
    assert photos[0]["alt"] == "Published caption"
    assert (
        photos[1]["alt"].startswith("White stucco")
        and photos[1]["source_alt"] == "Painter gallery photo 2"
    )
    apply_photo_choices(profile, photos, {"hero_photo": 9, "photo_captions": "invalid"})
    assert profile["cover_evidence_id"] == "e2"


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    storage.initialize()


def run_graph(monkeypatch, verdicts, domain="demo.test", events=None):
    case = next(row for row in pipeline.scenarios() if row["expected"]["status"] == "qualified")
    lead = case["lead"]
    checked = []
    published = []
    trace = []
    # Frozen search snapshots establish the qualification, then use the real
    # renderer, writer, router and persistence. Control only the external decisions.
    search, fetch = pipeline.sample_tools(case)
    q = pipeline.research.qualify(lead, None, search, fetcher=fetch)
    monkeypatch.setattr(pipeline.research, "qualify", lambda *a, **kw: q)
    monkeypatch.delenv("GOOGLE_API_KEY", raising=False)

    def review(*args):
        response = verdicts[min(len(checked), len(verdicts) - 1)]
        checked.append(response)
        return dict(response)

    def publish(preview, alias):
        published.append(alias)
        return {"published": True, "content_verified": True}

    monkeypatch.setattr(pipeline.studio, "review", review)
    monkeypatch.setattr(pipeline.publication, "publish", publish)

    def emit(stage, action, message, lead, *, handoff=None):
        trace.append((stage, action))
        if events is not None and handoff:
            events[stage] = handoff

    result = pipeline.run_lead("test", lead, domain, emit)
    return result, checked, published, trace


def test_reviewer_repairs_then_publishes_once(isolated, monkeypatch):
    fail = {"passed": False, "feedback": "Recover the cited observation.", "model_checked": True}
    yes = {"passed": True, "feedback": "Checked", "model_checked": True}
    writer = pipeline.studio.write_pitch
    attempts = []

    def track_writing(*args, branch):
        attempts.append(branch)
        return writer(*args, branch=branch)

    monkeypatch.setattr(pipeline.studio, "write_pitch", track_writing)
    lead, checks, published, trace = run_graph(monkeypatch, [fail, fail, yes])
    assert lead["status"] in {"ready", "manual"} and len(checks) == 3 and len(published) == 1
    assert attempts == [True, False, False]
    assert ("Review", "repair") in trace
    assert lead["pitch"]["revision"] and storage.leads("test")[0]["preview"]["content_verified"]


@pytest.mark.parametrize(
    "verdict,reviews",
    [
        ({"passed": False, "feedback": "Rejected", "model_checked": True}, 3),
        ({"passed": True, "feedback": "Only local checks ran", "model_checked": False}, 1),
    ],
)
def test_unreviewed_or_repeatedly_rejected_live_lead_never_publishes(
    isolated, monkeypatch, verdict, reviews
):
    lead, checked, published, trace = run_graph(monkeypatch, [verdict])
    assert lead["status"] == "hold" and not published and len(checked) == reviews
    assert trace[-1] == ("Queue", "held")


def test_missing_host_keeps_an_inline_concept_without_creating_an_unusable_pitch(isolated, monkeypatch):
    lead, checked, published, _ = run_graph(monkeypatch, [], domain="")
    assert lead["preview"]["html"] and not lead["preview"]["url"] and lead["status"] == "hold"
    assert not checked and not published and "pitch" not in lead


def test_simultaneous_budget_reservations_cannot_cross_the_limit(isolated):
    def reserve(_):
        try:
            storage.reserve("test", "business", "test provider", 0.01, 0.01, 10)
            return True
        except RuntimeError:
            return False

    with ThreadPoolExecutor(max_workers=4) as workers:
        assert sum(workers.map(reserve, range(4))) == 1
    assert storage.usage()[0]["dollars"] == 0.01


def test_recheck_refetches_current_registry_and_public_intake_listing(isolated, monkeypatch):
    listing = "https://local.yahoo.com/info-123-grove-painting-burbank/"
    for mode in ("live", "demo"):
        storage.new_run(mode, mode)
        storage.save_lead(
            mode,
            {"id": "101", "name": "Archived name", "status": "hold",
             "directory_url": listing, "website": "https://old.test"},
        )
        storage.finish_run(mode, "complete")
    with pytest.raises(ValueError, match="real businesses"):
        pipeline.run_batch(recheck="demo")
    current = {"id": "101", "name": "Current registry name"}
    monkeypatch.setenv("GOOGLE_API_KEY", "unused")
    monkeypatch.setenv("EXA_API_KEY", "unused")
    monkeypatch.delenv("CLOUDFLARE_API_TOKEN", raising=False)
    monkeypatch.setattr(pipeline.research, "discover", lambda *args: [current])
    received = []

    def run_lead(run_id, lead, domain, emit, case=None):
        received.append(lead)
        return {"status": "hold"}

    monkeypatch.setattr(pipeline, "run_lead", run_lead)
    with pytest.raises(ValueError, match="must belong"):
        pipeline.run_batch(recheck="live", licenses=["999"])
    new_id = pipeline.run_batch(recheck="live", licenses=["101"])
    assert received == [{**current, "directory_url": listing}]
    assert next(r for r in storage.list_runs() if r["id"] == new_id)["mode"] == "recheck"


def test_handoff_records_use_real_artifacts_and_only_publish_exposes_the_hosted_url(
    isolated, monkeypatch
):
    events = {}
    passed = {"passed": True, "feedback": "Reviewed.", "model_checked": True}
    lead, _, _, _ = run_graph(monkeypatch, [passed], events=events)
    assert events["Qualify"]["text"] == lead["qualification"]["reason"]
    assert events["Qualify"]["source_url"] in {
        item["url"] for item in lead["qualification"]["evidence"]
    }
    assert events["Build"]["text"] == lead["preview"]["handoff"]
    assert events["Build"]["section"].lstrip("#") in lead["preview"]["html"]
    assert events["Write"]["text"] in lead["pitch"]["body"]
    assert events["Review"]["text"] == lead["review"]["feedback"]
    assert events["Publish"]["url"] == lead["preview"]["url"]
    assert all("url" not in payload for stage, payload in events.items() if stage != "Publish")


def test_fresh_selection_remembers_research_outside_dashboard_batches(isolated):
    storage.reserve("early-public-research", "123456", "Exa", 0.014, 6, 3)
    storage.new_run("recoverable-run", "live")
    storage.reserve("recoverable-run", "654321", "Exa", 0.014, 6, 3)
    assert "123456" in storage.seen_live_ids()
    assert "654321" not in storage.seen_live_ids()
