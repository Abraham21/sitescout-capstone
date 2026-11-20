"""Graph boundaries that could otherwise let an unreviewed draft enter the queue."""

from concurrent.futures import ThreadPoolExecutor

import pytest

import pipeline
import storage


@pytest.fixture
def isolated(monkeypatch, tmp_path):
    monkeypatch.setenv("SITESCOUT_DATA_DIR", str(tmp_path))
    storage.initialize()


def run_graph(monkeypatch, verdicts, domain="demo.test"):
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
    result = pipeline.run_lead(
        "test", lead, domain, lambda stage, action, message, lead: trace.append((stage, action))
    )
    return result, checked, published, trace


def test_reviewer_repairs_then_publishes_once(isolated, monkeypatch):
    fail = {"passed": False, "feedback": "Recover the cited observation.", "model_checked": True}
    yes = {"passed": True, "feedback": "Checked", "model_checked": True}
    lead, checks, published, trace = run_graph(monkeypatch, [fail, yes])
    assert lead["status"] in {"ready", "manual"} and len(checks) == 2 and len(published) == 1
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
