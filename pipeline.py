"""Read this file first: the graph for one lead, followed by the batch runner."""

import argparse
import fcntl
import json
import os
import queue
import threading
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime
from typing import TypedDict

from langgraph.graph import END, START, StateGraph

import activity_intake
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


def showcase_entries():
    path = storage.ROOT / "examples/showcase-licenses.json"
    return json.loads(path.read_text()) if path.is_file() else []


def showcase_available():
    return bool(showcase_entries() and os.getenv("GOOGLE_API_KEY") and os.getenv("EXA_API_KEY"))


def sample_tools(case):
    def search(_query):
        return case["results"]

    def fetch(url):
        return case["pages"].get(
            url, {"url": url, "status": 0, "text": "", "links": [], "has_form": False}
        )

    return search, fetch


def qualify_fixture(case, model=None, progress=None, retrieval="fts"):
    """Run the production qualifier against local search, page and media snapshots."""
    search, fetch = sample_tools(case)
    options = {}
    if case.get("image_files"):
        from business_profile import fixture_image

        options["image_reader"] = lambda url, kind: fixture_image(case["image_files"], url, kind)
    return research.qualify(
        case["lead"], model, search, progress, fetcher=fetch, retrieval=retrieval, **options
    )


def run_lead(run_id, lead, domain, emit, case=None):
    """Graph nodes share this lead's state; the batch runner handles concurrency."""
    lid = lead["id"]
    alias, url = publication.preview_address(run_id, lid, domain) if domain else ("", "")
    model = (
        (lambda prompt: services.gemini(prompt, run_id, lid)) if os.getenv("GOOGLE_API_KEY") else None
    )
    search = lambda query: services.exa(query, run_id, lid)

    def note(stage, status, message, handoff=None):
        emit(stage, status, message, lead, handoff=handoff)

    def qualify(state):
        note("Qualify", "started", "Checking the business identity, official website and contact path.")
        progress = lambda msg: note("Qualify", "info", msg)
        if case:
            progress("Applying the evidence rules to a frozen synthetic case.")
            result = qualify_fixture(case, progress=progress)
        else:

            def review_photos(prompt, photos):
                return services.gemini(prompt, run_id, lid, photos=photos)

            result = research.qualify(lead, model, search, progress, photo_model=review_photos)
        finding = {"text": result["reason"]}
        by_id = {entry["id"]: entry for entry in result.get("evidence", [])}
        source = next((by_id[i] for i in result.get("issue_evidence_ids", []) if i in by_id), {})
        source_url = (
            source.get("url")
            or (result.get("follow_up") or {}).get("source_url")
            or result.get("website")
        )
        if source_url:
            finding.update(source_url=source_url, source_label="Open finding source")
        note("Qualify", "completed", result["reason"], finding)
        return {"qualification": result, "status": result["status"]}

    def build(state):
        note("Build", "started", "Rendering the painter concept from sourced business facts.")
        result = studio.build_preview(lead, state["qualification"])
        result.update(url=url, published=False)
        message = result.get("handoff") or (result["variant"] + " concept rendered.")
        note("Build", "completed", message, {"text": message, "section": result.get("section", "")})
        return {"preview": result}

    def write(state):
        note("Write", "started", "Preparing an opening and a setup invitation.")
        prior = state.get("review", {}).get("feedback", "")
        # A revision uses the reviewer's feedback in a single writing call. This leaves
        # room for both repairs and their reviews within the eight-call lead budget.
        draft = studio.write_pitch(
            lead,
            state["qualification"],
            state["preview"],
            model,
            prior,
            branch=state.get("repairs", 0) == 0,
        )
        paragraphs = draft["body"].split("\n\n")
        opening = draft.get("opening") or next((p for p in paragraphs[1:] if p.strip()), paragraphs[0])
        note("Write", "completed", draft["angle"], {"text": opening})
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
        feedback = verdict.get("feedback") or "Checks complete."
        note(
            "Review",
            "completed" if passed else "held",
            feedback,
            {"text": feedback, "checks": verdict.get("checks", {})},
        )
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
        message = "Verified the content at the shareable address."
        note("Publish", "completed", message, {"text": message, "url": preview["url"]})
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
            {"outcome": status},
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
        note("Queue", "failed", record["error"], {"outcome": "error"})
    return record


def run_batch(limit=3, mode="live", on_event=None, *, recheck=None, licenses=()):
    if mode not in {"live", "showcase", "demo"} or not 1 <= int(limit) <= 30:
        raise ValueError("Choose live, showcase or demo and a batch size between 1 and 30.")
    storage.initialize()
    listings = {}
    if licenses and not recheck:
        raise ValueError("Choose a previous real run to recheck specific licenses.")
    if recheck:
        with storage.connect() as db:
            rows = db.execute(
                """SELECT leads.id, json_extract(leads.data, '$.directory_url'), json_extract(leads.data, '$.activity')
                   FROM leads JOIN runs ON runs.id=leads.run_id
                   WHERE runs.id=? AND runs.mode IN ('live','showcase','recheck')
                   AND runs.status IN ('complete','complete_with_errors') ORDER BY leads.rowid""",
                (recheck,),
            )
            listings = {license_id: (url, activity) for license_id, url, activity in rows}
        if not listings:
            raise ValueError("Choose a completed run of real businesses to recheck.")
        if licenses:
            if not set(licenses).issubset(listings):
                raise ValueError("Each selected license must belong to the recorded batch.")
            listings = {key: listings[key] for key in licenses}
        mode = "recheck"
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

        def emit(stage, status, message, lead=None, *, handoff=None):
            entry = {
                "run_id": run_id,
                "lead_id": (lead or {}).get("id", ""),
                "lead_name": (lead or {}).get("name", ""),
                "stage": stage,
                "status": status,
                "message": str(message),
                "timestamp": storage.now(),
            }
            if handoff:
                entry["handoff"] = handoff
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
            "recheck": f"Rechecking the licenses from run {recheck} against current sources.",
        }
        emit("Discover", "started", descriptions[mode])
        cases = {}
        intake = {}
        if mode in {"live", "showcase", "recheck"}:
            if not os.getenv("GOOGLE_API_KEY") or not os.getenv("EXA_API_KEY"):
                raise ValueError("Live mode requires the Gemini and Exa keys in .env.")
            progress = lambda msg: emit("Discover", "info", msg)
            if mode == "live":
                candidates = activity_intake.discover(int(limit), storage.seen_live_ids(), progress, audit=intake)
            else:
                active = {row["id"]: row for row in research.discover(100_000, set())}
                by_phone = activity_intake.registry_phone_index(active.values())
                dismissed = storage.dismissed_ids()
                candidates = []
                ids = [entry["id"] for entry in showcase_entries()] if mode == "showcase" else listings
                for license_id in ids:
                    if license_id in active and license_id not in dismissed:
                        lead = active[license_id]
                        # Revisit the public listing discovery originally found. The qualifier
                        # reads it anew and matches its current business name, locality and phone.
                        source, activity = listings.get(license_id, (None, None))
                        if source:
                            canonical = research.gallery_pages([source])
                            if canonical:
                                lead = {**lead, "directory_url": canonical[0]}
                                if activity:
                                    lead["activity"] = json.loads(activity)
                                    if lead["activity"].get("listing_city"):
                                        current = activity_intake.recent_profile(canonical[0], date.today())
                                        match = activity_intake.match_profile(current, by_phone) if current else None
                                        if not match or match["id"] != license_id:
                                            lead.pop("directory_url")
                                        else:
                                            lead["activity"] = current["activity"].copy()
                                            if research.words(current["city"]) != research.words(lead["city"]):
                                                lead["activity"]["listing_city"] = current["city"].strip().title()
                        candidates.append(lead)
                candidates = candidates[: int(limit)]
                progress(
                    "Selected active licenses. Research uses the same tools as ordinary discovery."
                )

        else:
            rows = scenarios()
            winners = [r for r in rows if r["expected"]["status"] == "qualified"]
            others = [r for r in rows if r["expected"]["status"] != "qualified"]
            ordered = winners[:2] + others[:1] + winners[2:] + others[1:]
            # Two evaluation scenarios can describe the same business. A batch processes it once.
            for row in ordered:
                cases.setdefault(row["lead"]["id"], row)
            candidates = [row["lead"] for row in list(cases.values())[: int(limit)]]
        emit(
            "Discover",
            "completed",
            f"{len(candidates)} businesses selected.",
            handoff={
                "candidates": [{"id": lead["id"], "name": lead["name"]} for lead in candidates],
                **({"intake": intake} if intake else {}),
            },
        )
        domain = (
            publication.ensure_project()
            if candidates and os.environ.get("CLOUDFLARE_API_TOKEN")
            else ""
        )
        if candidates and mode != "demo" and not domain:
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
    storage.set_contact_action(run_id, lead_id, "dismissed")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Run SiteScout without the dashboard.")
    parser.add_argument("--mode", choices=["demo", "showcase", "live"], default="demo")
    parser.add_argument(
        "--limit", type=int, help="Maximum businesses (default 3, or the full batch when rechecking)."
    )
    parser.add_argument(
        "--recheck",
        metavar="RUN_ID",
        help="Research a real batch again using its licenses and any public intake listings.",
    )
    parser.add_argument(
        "--daily", action="store_true", help="Research up to ten unseen painters with an opportunity signal once today."
    )
    parser.add_argument("--licenses", nargs="+", default=(), help="Limit a recheck to these licenses from that run.")
    args = parser.parse_args()
    if args.recheck and args.daily:
        parser.error("Use --daily for fresh discovery or --recheck for a recorded batch.")
    storage.initialize()
    today = datetime.now().date().isoformat()
    if args.daily and storage.setting("last_daily_date") == today:
        print("Today's scheduled batch already ran.")
    else:
        run = run_batch(
            10
            if args.daily
            else (args.limit if args.limit is not None else (30 if args.recheck else 3)),
            "live" if args.daily else args.mode,
            lambda event: print(event["stage"], event["message"], flush=True),
            recheck=args.recheck, licenses=args.licenses,
        )
        if args.daily:
            storage.set_setting("last_daily_date", today)
        print("Run", run)
