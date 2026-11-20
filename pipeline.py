"""Read this file first: the graph for one lead, followed by the batch runner."""

import argparse
import fcntl
import json
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import publication
import research
import services
import storage
import studio
from autopilot import autopilot_status, set_autopilot
from storage import events, list_runs


class LeadState(TypedDict, total=False):
    lead: dict
    qualification: dict
    preview: dict
    pitch: dict
    review: dict
    repairs: int
    status: str
    channel: str


def scenarios():
    return json.loads((storage.ROOT / "examples/scenarios.json").read_text())


def showcase_ids():
    path = storage.ROOT / "examples/showcase-licenses.json"
    return json.loads(path.read_text()) if path.is_file() else []


def showcase_available():
    return bool(showcase_ids() and os.getenv("GOOGLE_API_KEY") and os.getenv("EXA_API_KEY"))


def sample_tools(case):
    def search(_query):
        return case["results"]

    def fetch(url):
        return case["pages"].get(
            url, {"url": url, "status": 0, "text": "", "links": [], "has_form": False}
        )

    return search, fetch


def run_lead(run_id, lead, domain, emit, case=None):
    """Graph nodes share this lead's state; the batch runner handles concurrency."""
    lid = lead["id"]
    alias, url = publication.preview_address(run_id, lid, domain) if domain else ("", "")
    model = (
        (lambda prompt: services.gemini(prompt, run_id, lid)) if os.getenv("GOOGLE_API_KEY") else None
    )
    search = lambda query: services.exa(query, run_id, lid)
    fetcher = None
    if case:
        search, fetcher = sample_tools(case)

    def note(stage, status, message):
        emit(stage, status, message, lead)

    def qualify(state):
        note("Qualify", "started", "Checking the business identity, official website and contact path.")
        options = {"fetcher": fetcher} if fetcher else {}
        # A fixture walkthrough fixes the qualification inputs and route so the
        # operator can rehearse it. Live discovery always uses the real model.
        if case:
            note("Qualify", "info", "Applying the evidence rules to a frozen synthetic case.")
        result = research.qualify(
            lead, None if case else model, search, lambda msg: note("Qualify", "info", msg), **options
        )
        note("Qualify", "completed", result["reason"])
        return {"qualification": result, "status": result["status"]}

    def build(state):
        note("Build", "started", "Rendering the painter concept from sourced business facts.")
        result = studio.build_preview(lead, state["qualification"])
        result.update(url=url, published=False)
        note("Build", "completed", result["variant"] + " concept rendered.")
        return {"preview": result}

    def write(state):
        note("Write", "started", "Preparing an opening and a setup invitation.")
        prior = state.get("review", {}).get("feedback", "")
        draft = studio.write_pitch(lead, state["qualification"], state["preview"], model, prior)
        note("Write", "completed", draft.get("angle") or "Draft prepared.")
        return {"pitch": draft}

    def review(state):
        note(
            "Review", "started", "Checking the business, cited claims, preview features and invitation."
        )
        verdict = studio.review(lead, state["qualification"], state["preview"], state["pitch"], model)
        # An unavailable reviewer must never release an artifact through a real uploader.
        rules_passed = all(
            passed
            for name, passed in verdict.get("checks", {}).items()
            if not name.startswith("Independent model")
        )
        if domain and rules_passed and not verdict.get("model_checked"):
            verdict.update(
                passed=False,
                unavailable=True,
                feedback="Independent review was unavailable. The concept is on hold.",
            )
        passed = verdict["passed"]
        note("Review", "completed" if passed else "held", verdict.get("feedback") or "Checks complete.")
        return {"review": verdict, "status": "reviewed" if passed else "hold"}

    def after_review(state):
        if state["review"]["passed"]:
            return "publish"
        if state["review"].get("unavailable"):
            return "queue"
        return "repair" if state.get("repairs", 0) < 2 else "queue"

    def repair(state):
        count = state.get("repairs", 0) + 1
        note("Review", "repair", f"Revision {count} of 2. " + state["review"]["feedback"])
        return {"repairs": count}

    def publish(state):
        preview = dict(state["preview"])
        note("Publish", "started", "Uploading only this reviewed preview and checking the served HTML.")
        preview.update(publication.publish(preview, alias))
        note("Publish", "completed", "Verified the content at the shareable address.")
        return {"preview": preview}

    def route(state):
        q = state["qualification"]
        status = state["status"]
        channel = ""
        if status == "qualified" and not domain:
            status = "hold"
            note(
                "Publish", "held", "Local preview generated. Hosting is required for a shareable draft."
            )
        if state.get("preview", {}).get("published") and state["review"]["passed"]:
            if q.get("email"):
                status, channel = "ready", "Email"
            elif q.get("social") or (q.get("phone") or lead.get("phone")):
                status, channel = "manual", ("Social profile" if q.get("social") else "Phone")
            else:
                status, channel = "hold", "Contact unavailable"
        message = {
            "ready": "Editable email ready for Abraham to copy.",
            "manual": f"Manual contact queue using {channel.lower()}.",
            "hold": "Held for human review.",
            "skip": "Skipped. No outreach.",
            "blocked": "Blocked. No outreach.",
        }.get(status, status)
        note(
            "Queue",
            {"hold": "held", "skip": "skipped", "blocked": "blocked"}.get(status, "completed"),
            message,
        )
        return {"status": status, "channel": channel}

    graph = StateGraph(LeadState)
    for name, node in [
        ("qualify", qualify),
        ("build", build),
        ("write", write),
        ("review", review),
        ("repair", repair),
        ("publish", publish),
        ("queue", route),
    ]:
        graph.add_node(name, node)
    graph.add_edge(START, "qualify")
    graph.add_conditional_edges("qualify", lambda s: "build" if s["status"] == "qualified" else "queue")
    graph.add_conditional_edges("build", lambda s: "write" if s["preview"]["url"] else "queue")
    graph.add_edge("write", "review")
    graph.add_conditional_edges("review", after_review)
    graph.add_edge("repair", "build")
    graph.add_edge("publish", "queue")
    graph.add_edge("queue", END)

    record = {**lead, "status": "running"}
    storage.save_lead(run_id, record)
    try:
        for update in graph.compile().stream({"lead": lead, "repairs": 0}, stream_mode="updates"):
            for changes in update.values():
                record.update(changes)
                storage.save_lead(run_id, record)
    except Exception as exc:
        # Never queue a draft as ready after a tool failure.
        record.update(status="error", error=str(exc)[:700])
        storage.save_lead(run_id, record)
        note("Queue", "failed", record["error"])
    return record


def run_batch(limit=3, mode="live", on_event=None):
    if mode not in {"live", "showcase", "demo"} or not 1 <= int(limit) <= 30:
        raise ValueError("Choose live, showcase or demo and a batch size between 1 and 30.")
    storage.initialize()
    lock = (storage.runtime_dir() / "batch.lock").open("a")
    try:
        fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        lock.close()
        raise RuntimeError("Another batch is running. Its results will appear in history.")
    run_id = uuid.uuid4().hex[:12]
    updates = queue.Queue()
    try:
        with storage.connect() as db:
            db.execute("UPDATE runs SET status='interrupted' WHERE status='running'")
        storage.new_run(run_id, mode)

        def emit(stage, status, message, lead=None):
            entry = {
                "run_id": run_id,
                "lead_id": (lead or {}).get("id", ""),
                "lead_name": (lead or {}).get("name", ""),
                "stage": stage,
                "status": status,
                "message": str(message),
                "timestamp": storage.now(),
            }
            storage.add_event(entry)
            if on_event:
                if threading.current_thread() is caller:
                    on_event(entry)
                else:
                    updates.put(entry)

        caller = threading.current_thread()
        descriptions = {
            "live": "Reading fresh candidates.",
            "demo": "Loading labeled synthetic source snapshots.",
            "showcase": "Looking up preselected actual licenses for fresh research.",
        }
        emit("Discover", "started", descriptions[mode])
        cases = {}
        if mode in {"live", "showcase"}:
            if not os.getenv("GOOGLE_API_KEY") or not os.getenv("EXA_API_KEY"):
                raise ValueError("Live mode requires the Gemini and Exa keys in .env.")
            progress = lambda msg: emit("Discover", "info", msg)
            if mode == "live":
                candidates = research.discover(int(limit), storage.seen_live_ids(), progress)
            else:
                active = {row["id"]: row for row in research.discover(100_000, set())}
                dismissed = storage.dismissed_ids()
                candidates = [active[i] for i in showcase_ids() if i in active and i not in dismissed][
                    : int(limit)
                ]
                progress("Selected registered businesses from the curated showcase list.")
        else:
            rows = scenarios()
            winners = [r for r in rows if r["expected"]["status"] == "qualified"]
            others = [r for r in rows if r["expected"]["status"] != "qualified"]
            ordered = winners[:2] + others[:1] + winners[2:] + others[1:]
            cases = {row["lead"]["id"]: row for row in ordered}
            candidates = [row["lead"] for row in ordered[: int(limit)]]
        emit("Discover", "completed", f"{len(candidates)} businesses selected.")
        domain = (
            publication.ensure_project()
            if candidates and os.environ.get("CLOUDFLARE_API_TOKEN")
            else ""
        )
        if mode in {"live", "showcase"} and not domain:
            emit("Publish", "held", "Hosting credentials unavailable. Any previews stay local.")
        with ThreadPoolExecutor(max_workers=3) as pool:
            futures = [
                pool.submit(run_lead, run_id, lead, domain, emit, cases.get(lead["id"]))
                for lead in candidates
            ]
            while not all(f.done() for f in futures) or not updates.empty():
                try:
                    entry = updates.get(timeout=0.15)
                    if on_event:
                        on_event(entry)
                except queue.Empty:
                    continue
            results = [f.result() for f in futures]
        status = "complete_with_errors" if any(x["status"] == "error" for x in results) else "complete"
        storage.finish_run(run_id, status)
    except Exception as exc:
        storage.finish_run(run_id, "failed")
        storage.add_event(
            {
                "run_id": run_id,
                "lead_id": "",
                "lead_name": "",
                "stage": "Discover",
                "status": "failed",
                "message": str(exc)[:700],
                "timestamp": storage.now(),
            }
        )
        raise
    finally:
        lock.close()
    return run_id


def leads(run_id, replay=False):
    records = storage.leads(run_id)
    if replay:
        for record in records:
            if record.get("original_pitch"):
                record["pitch"] = record["original_pitch"]
            record["status"] = record.get("original_status", record["status"])
    return records


def save_edit(run_id, lead_id, subject, body):
    def change(record):
        if record["status"] not in {"ready", "manual"}:
            raise ValueError("Only reviewed drafts in a contact queue can be edited.")
        if not subject.strip() or not body.strip() or record["preview"]["url"] not in body:
            raise ValueError("Keep a subject, a message and the verified preview link.")
        record.setdefault("original_pitch", dict(record["pitch"]))
        record["pitch"].update(subject=subject.strip(), body=body.strip(), edited_by="operator")

    storage.edit_lead(run_id, lead_id, change)


def dismiss(run_id, lead_id):
    def change(record):
        record.setdefault("original_status", record["status"])
        record["status"] = "dismissed"

    storage.edit_lead(run_id, lead_id, change)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SiteScout without the dashboard.")
    parser.add_argument("--mode", choices=["demo", "showcase", "live"], default="demo")
    parser.add_argument("--limit", type=int, default=3)
    parser.add_argument(
        "--daily", action="store_true", help="Process ten unseen live leads once today."
    )
    args = parser.parse_args()
    storage.initialize()
    today = datetime.now().date().isoformat()
    if args.daily and storage.setting("last_daily_date") == today:
        print("Today's scheduled batch already ran.")
    else:
        run = run_batch(
            10 if args.daily else args.limit,
            "live" if args.daily else args.mode,
            lambda event: print(event["stage"], event["message"], flush=True),
        )
        if args.daily:
            storage.set_setting("last_daily_date", today)
        print("Run", run)
