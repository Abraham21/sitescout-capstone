"""The local SiteScout operator desk. Run with streamlit run app.py."""

from collections import Counter
from datetime import datetime
from html import escape
import re
import time
from urllib.parse import urlsplit
from zoneinfo import ZoneInfo

import streamlit as st

import pipeline
from storage import usage


STAGES = ("Discover", "Qualify", "Build", "Write", "Review", "Publish", "Queue")
MODES = {
    "live": "Live discovery",
    "showcase": "Curated live showcase",
    "demo": "Demo fixtures",
    "sample": "Recorded sample (synthetic)",
    "replay": "Saved replay",
}
OUTCOMES = {
    "ready": "Email ready",
    "manual": "Manual contact",
    "skip": "Skipped",
    "hold": "On hold",
    "blocked": "Blocked",
    "dismissed": "Dismissed",
    "error": "Failed",
    "running": "In progress",
}
QUEUE_ORDER = {"ready": 0, "manual": 1, "hold": 2, "blocked": 3, "error": 4, "skip": 5, "dismissed": 6}
STAGE_LABELS = {
    "started": "Working",
    "info": "Observed",
    "completed": "Done",
    "held": "Held",
    "skipped": "Skipped",
    "blocked": "Blocked",
    "repair": "Repair",
    "failed": "Failed",
}
LOCAL_TIME = ZoneInfo("America/Los_Angeles")

st.set_page_config(page_title="SiteScout · Lead workspace", page_icon="🧭", layout="wide")
st.html("""
<style>
.stMainBlockContainer{max-width:1490px;padding-top:2.4rem}
[data-testid=stSidebar]{border-right:1px solid #82958f44}
[data-testid=stSidebar] [data-testid=stSidebarContent]{padding-top:.4rem}
button[kind=primary]{background:#185c4e;border-color:#185c4e;color:white}
button[kind=primary]:hover{background:#104a3e;border-color:#104a3e;color:white}
button[kind=primary]:focus-visible{outline:3px solid #ba8043}
.ss-brand{font:700 1.35rem/1.2 -apple-system,BlinkMacSystemFont,sans-serif;letter-spacing:-.05em;margin:.15rem 0 .7rem}
.ss-brand b{color:#25856d}.ss-brand span{letter-spacing:.01em;font-size:.74rem;opacity:.72;margin-left:.5rem;font-weight:500}
.ss-title{font:500 clamp(2.6rem,4.7vw,4.5rem)/1.02 Georgia,serif;letter-spacing:-.055em;margin:.1rem 0 .3rem}
.ss-rule{height:3px;background:linear-gradient(90deg,#217360 0 9%,#91a99f6e 9%);margin:.8rem 0 .1rem}
.ss-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border:1px solid #82958f60;border-radius:10px;margin:.2rem 0 .6rem;overflow:hidden}
.ss-metric{padding:.8rem 1rem;border-right:1px solid #82958f60}.ss-metric:last-child{border:0}
.ss-metric strong{font:500 1.65rem/1.2 Georgia,serif;display:block}.ss-metric small,.ss-step small{font-size:.79rem}
.ss-flow{display:grid;grid-template-columns:repeat(7,minmax(85px,1fr));gap:9px;overflow:auto;padding:.15rem .15rem .55rem 0}
.ss-step{position:relative;border:1px solid #82958f60;border-top:3px solid var(--mark,#a5aba7);border-radius:7px;padding:.6rem .65rem;min-width:0}
.ss-step b{display:block;font-size:.88rem}.ss-step small{opacity:.85}
.ss-step:not(:last-child)::after{content:'›';position:absolute;right:-9px;top:.55rem;font-size:1rem;opacity:.65}
.ss-step.completed{--mark:#258265}.ss-step.started,.ss-step.info{--mark:#358cb3}
.ss-step.held,.ss-step.repair{--mark:#bf7c24}.ss-step.blocked,.ss-step.failed{--mark:#bc5149}
.ss-timeline{display:grid;gap:.1rem}.ss-event{display:grid;grid-template-columns:4.9rem 5.2rem 1fr;align-items:baseline;gap:.3rem;padding:.48rem 0;border-bottom:1px solid #82958f38;font-size:.9rem}
.ss-event time{font:normal .75rem ui-monospace,monospace;opacity:.72}
.ss-event b{font-size:.82rem}.ss-event p{margin:0;font-size:.92rem;line-height:1.4;overflow-wrap:anywhere}
.ss-event small{display:block;opacity:.76}
.ss-note{padding:.8rem 1rem;border-left:3px solid #438b78;background:#82958f13;margin:.3rem 0;overflow-wrap:anywhere}
.ss-note p{margin:0 0 .25rem}.ss-note small{opacity:.78}
.ss-outcome{display:inline-block;font-weight:650;border-radius:999px;padding:.12rem .6rem;margin-right:.45rem;background:#e3e8e4;color:#294034;font-size:.78rem}
.ss-outcome.ready{background:#d3ebdf;color:#15543b}.ss-outcome.hold,.ss-outcome.manual{background:#f7e6bd;color:#724b13}.ss-outcome.blocked{background:#f3d7d4;color:#883b37}
@media(max-width:700px){.stMainBlockContainer{padding-top:1.1rem}.ss-metrics{grid-template-columns:repeat(2,1fr)}.ss-metric{padding:.55rem}.ss-event{grid-template-columns:4rem 4rem 1fr}}
</style>
""")


def local_time(stamp, fmt="%b %d · %I:%M %p"):
    if not stamp:
        return ""
    try:
        value = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
        return value.astimezone(LOCAL_TIME).strftime(fmt)
    except ValueError:
        return str(stamp)


def web_url(value):
    try:
        parsed = urlsplit(value or "")
        return value if parsed.scheme in ("http", "https") and parsed.hostname else None
    except ValueError:
        return None


def flow(events):
    latest = {event["stage"]: event["status"] for event in events}
    pending = (
        "Not run"
        if latest.get("Queue") in ("completed", "held", "skipped", "blocked", "failed")
        else "Waiting"
    )
    cards = []
    for stage in STAGES:
        status = latest.get(stage)
        label = STAGE_LABELS.get(status, pending)
        cards.append(
            f'<div class="ss-step {escape(status or "")}"><b>{stage}</b><small>{label}</small></div>'
        )
    st.html('<div class="ss-flow" aria-label="Workflow status">' + "".join(cards) + "</div>")


def timeline(events):
    if not events:
        st.caption("No recorded events in this view.")
        return
    rows = []
    for event in events:
        time = escape(local_time(event.get("timestamp"), "%H:%M:%S"))
        stage = escape(event["stage"])
        detail = escape(str(event.get("message", "")))
        actor = escape(event.get("lead_name") or "Batch")
        status = escape(str(event.get("status", "")))
        rows.append(
            f'<div class="ss-event"><time>{time}</time><b>{stage}</b><p>{detail}<small>{actor} · {status}</small></p></div>'
        )
    st.html('<div class="ss-timeline">' + "".join(rows) + "</div>")


def run_batch(mode, limit):
    activity = st.empty()
    seen = []

    def on_event(event):
        seen.append(event)
        with activity.container():
            st.subheader(event.get("lead_name") or "Batch activity")
            current = [
                e for e in seen if not e.get("lead_id") or e.get("lead_id") == event.get("lead_id")
            ]
            flow(current)
            timeline(seen[-4:])

    try:
        with st.spinner("Recording pipeline activity…", show_time=True):
            run_id = pipeline.run_batch(limit=limit, mode=mode, on_event=on_event)
    except Exception as error:
        st.error(f"The batch stopped. {error}")
        return
    st.session_state[f"selected_{mode}"] = run_id
    st.rerun()


def metrics(leads):
    counts = Counter(lead.get("status") for lead in leads)
    values = (
        ("Businesses", len(leads)),
        ("Email ready", counts["ready"]),
        ("Manual contact", counts["manual"]),
        ("Held, blocked or failed", sum(counts[k] for k in ("hold", "blocked", "error"))),
        ("Skipped or dismissed", counts["skip"] + counts["dismissed"]),
    )
    st.html(
        '<div class="ss-metrics">'
        + "".join(
            f'<div class="ss-metric"><strong>{value}</strong><small>{label}</small></div>'
            for label, value in values
        )
        + "</div>"
    )


def copy_draft(subject, body):
    # The draft is inert textarea content. Only the fixed button handler can execute.
    payload = escape(f"Subject: {subject}\n\n{body}")
    st.iframe(
        f"""
        <style>body{{margin:0;font:14px system-ui}}button{{cursor:pointer;font:600 14px system-ui;
        border:1px solid #80958c;border-radius:7px;background:#f7faf7;color:#184b40;padding:8px 16px}}
        textarea{{position:absolute;left:-9999px}}</style>
        <button type="button">Copy draft</button> <span role="status"></span>
        <textarea readonly aria-label="Draft to copy">{payload}</textarea>
        <script>document.querySelector('button').onclick=async function(){{
          const draft=document.querySelector('textarea'), result=document.querySelector('[role=status]');
          try {{ await navigator.clipboard.writeText(draft.value); result.textContent='Copied'; }}
          catch {{ draft.select(); result.textContent=document.execCommand('copy')?'Copied':'Select the draft text to copy it manually.'; }}
        }};</script>""",
        height=46,
        alt="Copy the subject and message to the clipboard",
    )


def message_panel(lead, run_id, replay):
    pitch = lead.get("pitch") or {}
    if not pitch.get("body"):
        st.caption("This lead has no outreach draft.")
        return
    key = f"{'replay_' if replay else ''}{run_id}_{lead['id']}"
    editable = not replay and lead["status"] in ("ready", "manual")
    channel = lead.get("channel") or "Manual review"
    q = lead.get("qualification") or {}
    recipient = lead.get("email") or q.get("email")
    if channel == "Phone":
        recipient = q.get("phone") or lead.get("phone")
        number = re.sub(r"[^+0-9]", "", recipient or "")
        if number:
            st.html(f'<a href="tel:{number}">Open phone dialer · {escape(str(recipient))}</a>')
    if channel == "Social profile" and web_url(q.get("social")):
        st.link_button("Open public business profile ↗", q["social"])
    st.caption(" · ".join(filter(None, [str(channel).replace("_", " ").title(), recipient])))
    subject = st.text_input(
        "Subject", value=pitch.get("subject", ""), key=f"subject_{key}", disabled=not editable
    )
    body = st.text_area(
        "Message", value=pitch["body"], height=180, key=f"body_{key}", disabled=not editable
    )
    if editable:
        link = (lead.get("preview") or {}).get("url")
        valid = bool(subject.strip() and body.strip() and (not link or link in body))
        if not valid:
            st.caption("Keep a subject, a message, and the verified preview link.")
        actions = st.columns(2)
        with actions[0]:
            if st.button("Save edits", key=f"edit_{key}", type="primary", disabled=not valid):
                try:
                    pipeline.save_edit(run_id, lead["id"], subject, body)
                except ValueError as error:
                    st.error(str(error))
                else:
                    st.toast("Draft edits saved.")
        with actions[1]:
            if valid:
                copy_draft(subject, body)
    if pitch.get("angle"):
        st.caption(f"Selected angle · {pitch['angle']}")
    angles = pitch.get("angles") or []
    if angles:
        with st.expander("Compare pitch angles"):
            for angle in angles:
                st.write(angle.get("label") or angle.get("id"))
                st.caption(
                    " · ".join(
                        str(v)
                        for v in [
                            angle.get("opening"),
                            angle.get("score"),
                            ", ".join(angle.get("evidence_ids") or []),
                        ]
                        if v
                    )
                )


def preview_panel(lead):
    preview = lead.get("preview") or {}
    html = preview.get("html")
    url = web_url(preview.get("url")) if preview.get("published") else None
    if url:
        st.link_button("Open verified public preview ↗", url)
    if html:
        st.caption(
            "Saved preview"
            + (f" · {preview['variant']}" if preview.get("variant") else "")
            + ("" if url else " · Local only")
        )
        # The palette needs scripts; the inner sandbox keeps its origin opaque and blocks form submission.
        wrapper = f'<style>*{{box-sizing:border-box}}body{{margin:0}}iframe{{width:100%;height:505px;border:0}}</style><iframe title="Business website preview" sandbox="allow-scripts" srcdoc="{escape(html, quote=True)}"></iframe>'
        st.iframe(wrapper, height=510, alt="Saved business website preview")
    elif not url:
        st.caption("This lead has no website preview.")


def evidence_panel(lead):
    qualification = lead.get("qualification") or {}
    chunks = qualification.get("evidence") or []
    if qualification.get("website_status"):
        st.caption(f"Website finding · {qualification['website_status'].replace('_', ' ')}")
    if web_url(qualification.get("website")):
        st.link_button("Open identified business site ↗", qualification["website"])
    for item in chunks:
        url = web_url(item.get("url"))
        source = (
            (
                f' · <a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">'
                f"{escape(urlsplit(url).hostname)} ↗</a>"
            )
            if url
            else ""
        )
        st.html(
            f'<div class="ss-note"><p>{escape(str(item.get("text", "")))}</p><small>{escape(str(item.get("id", "")))}{source}</small></div>'
        )
    if not chunks:
        st.caption("No supporting passages recorded.")


def lead_panel(lead, run_id, events, replay):
    qualification = lead.get("qualification") or {}
    review = lead.get("review") or {}
    relevant = [
        e
        for e in events
        if e.get("lead_id") == lead["id"] or (e["stage"] == "Discover" and not e.get("lead_id"))
    ]
    st.subheader(lead["name"])
    status = escape(lead["status"])
    st.html(
        f'<span class="ss-outcome {status}">{escape(OUTCOMES.get(status, status))}</span>{escape(lead.get("city") or "")}'
    )
    if qualification.get("reason"):
        st.html(f'<div class="ss-note">{escape(str(qualification["reason"]))}</div>')
    flow(relevant)
    draft, preview, evidence, trace = st.tabs(
        ["Outreach draft", "Website preview", "Evidence", "Agent trace"]
    )
    with draft:
        message_panel(lead, run_id, replay)
        if not replay and lead["status"] in ("ready", "manual", "hold"):
            if st.button("Dismiss lead", key=f"dismiss_{run_id}_{lead['id']}"):
                pipeline.dismiss(run_id, lead["id"])
                st.rerun()
    with preview:
        preview_panel(lead)
    with evidence:
        evidence_panel(lead)
    with trace:
        if review:
            st.write("**Reviewer decision**", "Passed" if review.get("passed") else "Held")
            if review.get("feedback"):
                st.write(review["feedback"])
            if review.get("checks"):
                with st.expander("Review checks"):
                    for name, passed in review["checks"].items():
                        st.text(f"{'✓' if passed else '×'}  {name.replace('_', ' ')}")
        timeline(relevant)


def workspace(run, replay=False):
    run_id = run["id"]
    replay = replay or run["mode"] == "sample"
    events = pipeline.events(run_id)
    mode = MODES.get(run["mode"], run["mode"])
    st.caption(
        f"{'Replay of ' if replay else ''}{mode} · {local_time(run.get('started_at'))} PT · {run['status']}"
    )

    def open_replay():
        st.session_state["data_source"] = "replay"
        st.session_state["saved_run"] = run_id
        st.session_state["play_run"] = run_id

    if events and not replay:
        st.button("▶ Play this run", on_click=open_replay, key=f"open_replay_{run_id}")
    elif replay and events:
        end = len(events)
        auto_play = st.session_state.pop("play_run", None) == run_id
        if st.button("▶ Play recorded events", key=f"play_{run_id}") or auto_play:
            screen = st.empty()
            shown = []
            for event in events:
                shown.append(event)
                with screen.container():
                    st.subheader(event.get("lead_name") or "Batch activity")
                    flow(
                        [
                            e
                            for e in shown
                            if not e.get("lead_id") or e.get("lead_id") == event.get("lead_id")
                        ]
                    )
                    timeline(shown[-3:])
                time.sleep(min(0.55, 16 / end))
            screen.empty()
            st.session_state[f"cursor_{run_id}"] = end
        cursor = st.slider("Recorded event", 1, end, end, key=f"cursor_{run_id}") if end > 1 else 1
        moment = events[cursor - 1]
        prefix = events[:cursor]
        flow([e for e in prefix if not e.get("lead_id") or e.get("lead_id") == moment.get("lead_id")])
        timeline(prefix[-5:])
        if cursor < end:
            return
    leads = sorted(
        pipeline.leads(run_id, replay=replay), key=lambda item: QUEUE_ORDER.get(item["status"], 7)
    )
    metrics(leads)
    if leads:
        queue, detail = st.columns([1, 2.6], gap="large")
        with queue:
            st.subheader("Lead queue")
            outcomes = list(dict.fromkeys(lead["status"] for lead in leads))
            selected_status = st.selectbox(
                "Filter by outcome",
                ["all", *outcomes],
                format_func=lambda k: OUTCOMES.get(k, "All outcomes"),
            )
            candidates = [lead for lead in leads if selected_status in ("all", lead["status"])]
            records = {lead["id"]: lead for lead in candidates}
            selected = st.radio(
                "Select a business",
                list(records),
                format_func=lambda i: (
                    f"{records[i]['name']} · {OUTCOMES.get(records[i]['status'], records[i]['status'])}"
                ),
                key=f"lead_{run_id}_{selected_status}",
                label_visibility="collapsed",
            )
        with detail:
            lead_panel(records[selected], run_id, events, replay)
    elif not events:
        st.info("This batch has no saved events yet.")
    with st.expander(f"Full run log · {len(events)} events", expanded=not leads):
        if len(events) > 1:
            first, last = (
                datetime.fromisoformat(e["timestamp"].replace("Z", "+00:00"))
                for e in (events[0], events[-1])
            )
            st.caption(f"Recorded span · {(last - first).total_seconds():.1f} seconds")
        ledger = usage(run_id)
        if ledger:
            for entry in ledger:
                label = "reported" if entry["state"] == "reported" else "reserved, outcome unconfirmed"
                st.caption(
                    f"{entry['service']} · {entry['calls']} calls · ${entry['dollars']:.4f} {label}"
                )
        elif run["mode"] == "sample":
            st.caption("Provider billing was not included in this recording.")
        else:
            st.caption("No metered provider calls recorded.")
        stage = st.selectbox("Inspect stage", ["All stages", *STAGES], key=f"stage_{run_id}")
        timeline([e for e in events if stage == "All stages" or e["stage"] == stage])


def main():
    runs = sorted(pipeline.list_runs(), key=lambda item: item.get("started_at", ""), reverse=True)
    verified = [
        r
        for r in runs
        if r["mode"] in ("live", "showcase")
        and r["status"] in ("complete", "complete_with_errors")
        and r.get("published_count")
    ]
    featured = max(verified, key=lambda r: (bool(r.get("ready_count")), r["started_at"]), default=None)
    with st.sidebar:
        st.html('<div class="ss-brand">Site<b>Scout</b><span>Operator desk</span></div>')
        st.subheader("Run controls")
        sources = {k: v for k, v in MODES.items() if k != "sample"}
        if not getattr(pipeline, "showcase_available", lambda: False)():
            sources.pop("showcase")
        if featured:
            sources = {"featured": "Featured live run", **sources}
        default_source = "demo"
        if featured:
            default_source = "featured"
        elif any(r["mode"] == "live" for r in runs):
            default_source = "live"
        elif any(r["mode"] == "sample" for r in runs):
            default_source = "replay"
        mode = st.radio(
            "Data source",
            list(sources),
            format_func=sources.get,
            key="data_source",
            index=list(sources).index(default_source),
        )
        chosen = None
        launch = False
        if mode == "featured":
            chosen = featured
            st.caption("A completed live run with a verified website preview.")
        elif mode == "replay":
            if runs:
                choices = {r["id"]: r for r in runs}
                saved = st.selectbox(
                    "Saved run",
                    list(choices),
                    key="saved_run",
                    format_func=lambda i: (
                        f"{local_time(choices[i]['started_at'])} · {MODES.get(choices[i]['mode'], choices[i]['mode'])} · {str(i)[-6:]}"
                    ),
                )
                chosen = choices[saved]
        else:
            limit = st.number_input("Businesses per batch", min_value=1, max_value=10, value=3)
            if mode == "showcase":
                st.caption("Fresh research on a preselected list of actual contractors.")
            launch = st.button(
                {
                    "live": "Run live discovery",
                    "demo": "Run demo fixtures",
                    "showcase": "Run curated showcase",
                }[mode],
                type="primary",
                width="stretch",
            )
            by_mode = [r for r in runs if r["mode"] == mode and r["status"] != "superseded"]
            chosen = next(
                (r for r in by_mode if r["id"] == st.session_state.get(f"selected_{mode}")),
                next(iter(by_mode), None),
            )
        st.divider()
        schedule = pipeline.autopilot_status()
        enabled = bool(schedule["enabled"])
        toggle = st.toggle("Daily autopilot", enabled, help="Schedule live discovery on this Mac.")
        if toggle != enabled:
            try:
                pipeline.set_autopilot(toggle)
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Schedule change failed. {error}")
            else:
                st.rerun()
        st.caption(
            f"{'Batch running' if schedule['running'] else ('Scheduled' if enabled else 'Off')} · 9:00 AM on this Mac · {schedule['limit']} businesses"
        )
        st.button("Refresh runs", width="stretch")
    st.html('<h1 class="ss-title">Lead workspace</h1><div class="ss-rule"></div>')
    if launch:
        run_batch(mode, int(limit))
    elif chosen:
        workspace(chosen, replay=mode == "replay")
    else:
        flow([])
        if mode == "replay":
            st.info("Saved runs will appear here after your first batch.")
        else:
            st.subheader(
                "Start a discovery run"
                if mode == "live"
                else ("Run the live showcase" if mode == "showcase" else "Try the fixture walkthrough")
            )
            st.write(
                "Choose the batch size and start a run in the sidebar."
                if mode != "demo"
                else "Run the curated cases to inspect how evidence changes the route, website preview, and outreach draft."
            )


if __name__ == "__main__":
    main()
