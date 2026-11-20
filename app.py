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
from studio import display_name


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
html{scroll-behavior:smooth}
.stMainBlockContainer{max-width:1530px;padding:2rem 3.5rem 4rem}
[data-testid=stSidebar]{border-right:1px solid #d3ddd6}
[data-testid=stSidebar] [data-testid=stSidebarContent]{padding-top:.4rem}
button[kind=primary]{background:#185c4e;border-color:#185c4e;color:white}
button[kind=primary]:hover{background:#104a3e;border-color:#104a3e;color:white}
button[kind=primary]:focus-visible,.ss-chain a:focus-visible{outline:3px solid #ba8043}
.ss-brand{font:760 1.45rem/1.25 -apple-system,BlinkMacSystemFont,sans-serif;letter-spacing:-.055em;margin:.1rem 0 .8rem}
.ss-brand b{color:#217b61}.ss-brand span{display:block;letter-spacing:.025em;font-size:.74rem;color:#56695e;font-weight:500;margin-top:.25rem}
.ss-title,.ss-company{font:500 clamp(2rem,3vw,3.4rem)/1.02 Georgia,serif;letter-spacing:-.055em;margin:.2rem 0}
.ss-title{font-size:clamp(1.8rem,2.7vw,2.7rem)}
.ss-rule{height:1px;background:#c8d8ce;margin:.6rem 0}
.ss-metrics{display:grid;grid-template-columns:repeat(5,minmax(0,1fr));border-block:1px solid #c4d2c8;margin:.2rem 0}
.ss-metric{display:flex;gap:.6rem;align-items:center;padding:.6rem .7rem;color:#345546}
.ss-metric strong{font:500 1.7rem Georgia,serif}.ss-metric small{font-size:.77rem;line-height:1.2}
.ss-companyline{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;margin:.4rem 0 .24rem}
.ss-companyline small,.ss-runline{font-size:.82rem;color:#52695c}
.ss-timeline{display:grid;gap:.1rem}.ss-event{display:grid;grid-template-columns:4.9rem 5.2rem 1fr;align-items:baseline;gap:.3rem;padding:.48rem 0;border-bottom:1px solid #82958f38;font-size:.9rem}
.ss-event time{font:normal .75rem ui-monospace,monospace;color:#597168}
.ss-event b{font-size:.82rem}.ss-event p{margin:0;font-size:.92rem;line-height:1.4;overflow-wrap:anywhere}
.ss-event small{display:block;color:#5d7167}
.ss-note{padding:.7rem .9rem;border-left:3px solid #438b78;background:#82958f13;margin:.3rem 0;overflow-wrap:anywhere}
.ss-note p{margin:0 0 .25rem}.ss-note small{color:#597168}
.ss-outcome{display:inline-block;font-weight:650;border-radius:999px;padding:.24rem .65rem;background:#e3e8e4;color:#294034;font-size:.75rem;line-height:1.4}
.ss-outcome.ready{background:#d7ebd9;color:#245530}.ss-outcome.manual{background:#daefe8;color:#185540}
.ss-outcome.hold{background:#f7e6bd;color:#724b13}.ss-outcome.blocked,.ss-outcome.error{background:#f3d7d4;color:#883b37}
.ss-chain{display:grid;grid-template-columns:repeat(4,minmax(0,1fr));gap:.8rem;align-items:stretch;margin:.45rem 0 .9rem}
.ss-card{position:relative;min-width:0;padding:1.05rem .9rem .9rem;border:1px solid #c8d6cc;border-top:3px solid #50836a;background:#f6f8f5;border-radius:9px;color:#24382c;overflow-wrap:anywhere}
.ss-card:not(:last-child)::after{content:'→';position:absolute;right:-.78rem;top:1.2rem;color:#647d6b;background:white;font-size:.82rem;line-height:1}
.ss-card:nth-child(2){background:#f7f6ef;border-top-color:#a68d43}.ss-card:nth-child(3){background:#f3f5f9;border-top-color:#778cac}.ss-card:nth-child(4){background:#eef5ee;border-top-color:#659369}
.ss-card.pending{background:#fafbfa;border-color:#dce2dd;color:#63766a}.ss-card.active{outline:2px solid #4c896b;outline-offset:-1px}
.ss-card header{display:flex;justify-content:space-between;align-items:center;gap:.25rem;margin-bottom:.7rem}
.ss-card header b{font-size:.8rem;letter-spacing:.015em}.ss-card header span{font-size:.68rem;color:#526458}
.ss-card p{font-size:.92rem;line-height:1.43;margin:.25rem 0 .65rem}.ss-card a,.ss-card summary{color:#195840;font-weight:650;font-size:.8rem}
.ss-card summary{cursor:pointer}.ss-card details p{white-space:pre-line}.ss-card ul{padding-left:1.05rem;font-size:.83rem}
.ss-context{padding:.55rem .8rem;background:#f1f5f2;border-radius:6px;font-size:.83rem;color:#3a5141}
@media(max-width:1150px){.stMainBlockContainer{padding-inline:1.6rem}.ss-chain{grid-template-columns:repeat(2,minmax(0,1fr))}.ss-card:nth-child(2)::after{display:none}}
@media(max-width:700px){.stMainBlockContainer{padding:4.2rem 1rem 1rem}.ss-chain{grid-template-columns:minmax(0,1fr);gap:.5rem}.ss-card:not(:last-child)::after{display:none}.ss-metrics{grid-template-columns:repeat(2,minmax(0,1fr))}.ss-metric{padding:.43rem}.ss-event{grid-template-columns:4rem 4rem 1fr}}
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
        return (
            value
            if parsed.scheme in ("http", "https")
            and parsed.hostname
            and not parsed.username
            and not parsed.password
            else None
        )
    except ValueError:
        return None


ROLES = {
    "Qualify": "Researcher",
    "Build": "Website builder",
    "Write": "Pitch writer",
    "Review": "Reviewer",
}


def link(url, label):
    if not web_url(url):
        return ""
    return f'<a href="{escape(url, quote=True)}" target="_blank" rel="noopener noreferrer">{escape(label)} ↗</a>'


def saved_handoffs(lead):
    """Finished legacy runs have all artifacts but no structured handoff events."""
    q = lead.get("qualification") or {}
    page = lead.get("preview") or {}
    draft = lead.get("original_pitch") or lead.get("pitch") or {}
    review = lead.get("review") or {}
    evidence = {item["id"]: item for item in q.get("evidence", []) if item.get("id")}
    source = next((evidence[i] for i in q.get("issue_evidence_ids", []) if i in evidence), {})
    paragraphs = [p.strip() for p in draft.get("body", "").split("\n\n") if p.strip()]
    return {
        "Qualify": {
            "text": q.get("reason"),
            "source_url": source.get("url") or q.get("website") or lead.get("source_url"),
        },
        "Build": {
            "text": page.get("handoff") or ", ".join(page.get("features", [])),
            "section": page.get("section"),
        },
        "Write": {
            "text": draft.get("opening")
            or next(
                (p for p in paragraphs if not p.lower().startswith("hi ") and "http" not in p),
                draft.get("subject"),
            )
        },
        "Review": {"text": review.get("feedback")},
    }


def handoff_chain(events, lead=None, *, synthetic=False):
    """Growing runs use only the visible event prefix; the completed desk can show saved artifacts."""
    handoffs = saved_handoffs(lead) if lead else {}
    latest = {}
    verified = (
        web_url((lead.get("preview") or {}).get("url"))
        if lead and (lead.get("preview") or {}).get("published")
        else None
    )
    for event in events:
        stage = event["stage"]
        payload = event.get("handoff") or {}
        if stage == "Review" and event["status"] == "repair":
            for previous in ("Build", "Write", "Review", "Publish"):
                latest.pop(previous, None)
                if not lead:
                    handoffs.pop(previous, None)
            verified = None
        latest[stage] = event
        if payload and stage in ROLES:
            handoffs[stage] = {**handoffs.get(stage, {}), **payload}
        if stage == "Publish" and event["status"] == "completed" and web_url(payload.get("url")):
            verified = payload["url"]
    ended = "Queue" in latest or bool(lead)
    cards = []
    for stage, role in ROLES.items():
        event = latest.get(stage, {})
        detail = handoffs.get(stage, {})
        done = bool(detail.get("text"))
        status = event.get("status", "completed" if done else "")
        state = {
            "info": "Working",
            "started": "Working",
            "completed": "Done",
            "repair": "Revising",
        }.get(status, STAGE_LABELS.get(status))
        state = state or ("Not reached" if ended else "Waiting")
        body = detail.get("text") if done else event.get("message")
        if not body:
            body = "Workflow ended before this stage." if ended else "Waiting for the previous step."
        action = ""
        if stage == "Qualify":
            source = web_url(detail.get("source_url"))
            if synthetic and source:
                qualification = (lead or {}).get("qualification") or {}
                issue_ids = qualification.get("issue_evidence_ids", [])
                evidence = sorted(
                    qualification.get("evidence", []), key=lambda item: item.get("id") not in issue_ids
                )
                excerpt = next(
                    (
                        item["text"]
                        for item in evidence
                        if item.get("url") == source and item.get("text")
                    ),
                    None,
                )
                if excerpt:
                    action = f"<details><summary>Read saved source excerpt</summary><p>{escape(excerpt)}</p></details>"
                else:
                    action = "<small>Synthetic source snapshot</small>"
            elif source:
                action = link(
                    source, detail.get("source_label") or urlsplit(source).hostname.removeprefix("www.")
                )
        elif stage == "Build" and verified:
            section = detail.get("section") or ""
            section = section if re.fullmatch(r"#[\w-]+", section) else ""
            action = link(
                verified.split("#")[0] + section, "View site section" if section else "View website"
            )
        elif stage == "Write" and lead:
            draft = lead.get("original_pitch") or lead.get("pitch") or {}
            if draft.get("body"):
                action = f"<details><summary>Read original draft</summary><p>{escape(draft['body'])}</p></details>"
        elif stage == "Review" and lead:
            checks = (lead.get("review") or {}).get("checks") or {}
            if checks:
                rows = "".join(
                    f"<li>{'✓' if passed else '×'} {escape(label)}</li>"
                    for label, passed in checks.items()
                )
                action = (
                    f"<details><summary>Inspect {len(checks)} checks</summary><ul>{rows}</ul></details>"
                )
        style = (
            "active"
            if state in ("Working", "Revising")
            else ("pending" if not done and not event else "")
        )
        cards.append(
            f'<article class="ss-card {style}"><header><b>{role}</b><span>{escape(state)}</span></header><p>{escape(str(body))}</p>{action}</article>'
        )
    st.html('<section class="ss-chain" aria-label="Agent handoff">' + "".join(cards) + "</section>")
    queue = latest.get("Queue")
    if verified:
        route = queue.get("message") if queue else "Routing to operator."
        st.html(
            f'<div class="ss-context">{link(verified, "Verified public preview")} · {escape(route or "Operator queue")}</div>'
        )


def show_activity(events, *, synthetic=False):
    current = events[-1]
    # Follow one building lead when several workers emit interleaved events.
    focus = next(
        (e["lead_id"] for e in events if e["stage"] == "Build" and e.get("lead_id")),
        current.get("lead_id"),
    )
    selected = [e for e in events if e.get("lead_id") == focus] if focus else []
    if selected:
        st.subheader(display_name(selected[-1]["lead_name"]))
        handoff_chain(selected, synthetic=synthetic)
    else:
        st.subheader("Discovering businesses")
    st.caption("Latest batch events")
    timeline(events[-3:])


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
            show_activity(seen, synthetic=mode == "demo")

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
            st.html(
                f'<div class="ss-context">Manual follow-up · <a href="tel:{number}">Call {escape(str(recipient))}</a></div>'
            )
    if channel == "Social profile" and web_url(q.get("social")):
        st.link_button("Open public business profile ↗", q["social"])
    if channel != "Phone":
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
        # Fragment links in srcdoc resolve against Streamlit's URL. Keep in-page
        # navigation in this saved document, including older recorded sites.
        navigation = """<script>
        document.addEventListener('click', event => {
          const link = event.target.closest('a[href^="#"]');
          const section = link && document.getElementById(link.getAttribute('href').slice(1));
          if (section) { event.preventDefault(); section.scrollIntoView(); }
        });
        </script>"""
        html = html.replace("</body>", navigation + "</body>")
        wrapper = f'<style>*{{box-sizing:border-box}}body{{margin:0}}iframe{{width:100%;height:505px;border:0}}</style><iframe title="Business website preview" sandbox="allow-scripts allow-downloads allow-popups allow-popups-to-escape-sandbox" srcdoc="{escape(html, quote=True)}"></iframe>'
        st.iframe(wrapper, height=510, alt="Saved business website preview")
    elif not url:
        st.caption("This lead has no website preview.")


def evidence_panel(lead, *, synthetic=False):
    qualification = lead.get("qualification") or {}
    chunks = qualification.get("evidence") or []
    if qualification.get("website_status"):
        st.caption(f"Website finding · {qualification['website_status'].replace('_', ' ')}")
    if synthetic:
        st.caption("These excerpts come from synthetic source snapshots.")
    elif web_url(qualification.get("website")):
        st.link_button("Open identified business site ↗", qualification["website"])
    selected = set(qualification.get("retrieved_ids") or [])
    if selected:
        st.caption(
            f"Retrieval supplied {len(selected)} of {len(chunks)} recorded passages to the qualifier."
        )
    for item in sorted(chunks, key=lambda item: item.get("id") not in selected):
        url = web_url(item.get("url"))
        source = (
            (
                f' · <a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">'
                f"{escape(urlsplit(url).hostname)} ↗</a>"
            )
            if url and not synthetic
            else (f" · {escape(urlsplit(url).hostname)} · synthetic" if url else "")
        )
        st.html(
            f'<div class="ss-note"><p>{escape(str(item.get("text", "")))}</p><small>{escape(str(item.get("id", "")))}{" · Retrieved" if item.get("id") in selected else ""}{source}</small></div>'
        )
    if not chunks:
        st.caption("No supporting passages recorded.")


def lead_panel(lead, run_id, events, replay, *, synthetic=False):
    qualification = lead.get("qualification") or {}
    review = lead.get("review") or {}
    relevant = [
        e
        for e in events
        if e.get("lead_id") == lead["id"] or (e["stage"] == "Discover" and not e.get("lead_id"))
    ]
    status = escape(lead["status"])
    identity = " · ".join(
        str(v)
        for v in (lead.get("city"), "License " + str(lead["id"]) if lead.get("source_url") else None)
        if v
    )
    st.html(
        f'<div class="ss-companyline"><h2 class="ss-company">{escape(display_name(lead["name"]))}</h2>'
        f'<span class="ss-outcome {status}">{escape(OUTCOMES.get(status, status))}</span><small>{escape(identity)}</small></div>'
    )
    handoff_chain(relevant, lead, synthetic=synthetic)
    follow_up = qualification.get("follow_up") or {}
    if follow_up.get("reason"):
        listing = "" if synthetic else link(follow_up.get("source_url"), "Directory source")
        candidate = "" if synthetic else link(follow_up.get("url"), "Listed candidate site")
        sources = " · ".join(filter(None, (listing, candidate)))
        st.html(
            f'<div class="ss-context"><b>Manual verification</b> · {escape(follow_up["reason"])}<br>{sources}</div>'
        )
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
        evidence_panel(lead, synthetic=synthetic)
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
    synthetic = run["mode"] in ("demo", "sample")
    origin = "Synthetic businesses" if synthetic else "Real businesses"
    date = local_time(run.get("started_at"))
    heading, action = st.columns([3, 1], vertical_alignment="center")

    def open_replay():
        st.session_state["data_source"] = "replay"
        st.session_state["saved_run"] = run_id
        st.session_state["play_run"] = run_id

    with heading:
        st.caption(
            f"{'Saved replay' if replay else 'Saved result'} · {mode} · {origin} · {date} PT · {run['status'].replace('_', ' ')}"
        )
    with action:
        if events and not replay:
            st.button(
                "Replay saved run",
                on_click=open_replay,
                key=f"open_replay_{run_id}",
                width="stretch",
            )
        play = (
            replay
            and events
            and st.button(
                "▶ Play recorded events", key=f"play_{run_id}", type="primary", width="stretch"
            )
        )
    if replay and events:
        end = len(events)
        auto_play = st.session_state.pop("play_run", None) == run_id
        if play or auto_play:
            screen = st.empty()
            for index in range(1, end + 1):
                with screen.container():
                    st.caption(f"Playing saved event {index} of {end}")
                    show_activity(events[:index], synthetic=synthetic)
                time.sleep(min(0.52, 16 / end))
            screen.empty()
            st.session_state[f"cursor_{run_id}"] = end
        cursor = st.slider("Recorded event", 1, end, end, key=f"cursor_{run_id}") if end > 1 else 1
        if cursor < end:
            show_activity(events[:cursor], synthetic=synthetic)
            return
    leads = sorted(
        pipeline.leads(run_id, replay=replay), key=lambda item: QUEUE_ORDER.get(item["status"], 7)
    )
    metrics(leads)
    if leads:
        queue, business = st.columns([1, 2], gap="medium")
        with queue:
            outcomes = list(dict.fromkeys(lead["status"] for lead in leads))
            selected_status = st.selectbox(
                "Filter queue",
                ["all", *outcomes],
                format_func=lambda k: OUTCOMES.get(k, "All outcomes"),
                key=f"filter_{run_id}",
            )
        with business:
            records = {item["id"]: item for item in leads if selected_status in ("all", item["status"])}
            selected = st.selectbox(
                "Inspect business",
                list(records),
                format_func=lambda i: (
                    f"{display_name(records[i]['name'])} · {OUTCOMES.get(records[i]['status'], records[i]['status'])}"
                ),
                key=f"lead_{run_id}_{selected_status}",
            )
        lead_panel(records[selected], run_id, events, replay, synthetic=synthetic)
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
    featured = next(iter(verified), None)
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
            st.caption(
                "Recorded from real businesses. Play the saved run or choose a live source to start a fresh batch."
            )
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
            if mode == "live":
                st.caption(
                    "Search previously unseen contractor licenses and check their current contact paths."
                )
            elif mode == "demo":
                st.caption(
                    "Synthetic source records. Configured model and hosting tools can run afresh."
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
    title, start = st.columns([3, 1], vertical_alignment="center")
    with title:
        st.html('<h1 class="ss-title">Lead workspace</h1>')
    if mode == "featured" and pipeline.showcase_available():

        def launch_showcase():
            st.session_state["data_source"] = "showcase"
            st.session_state["start_showcase_now"] = True

        with start:
            st.button(
                "▶ Start live showcase", type="primary", width="stretch", on_click=launch_showcase
            )
    elif mode in ("live", "showcase", "demo"):
        labels = {
            "live": "Run live discovery",
            "showcase": "Run curated showcase",
            "demo": "Run demo fixtures",
        }
        with start:
            requested = st.session_state.pop("start_showcase_now", False) and mode == "showcase"
            launch = st.button("▶ " + labels[mode], type="primary", width="stretch") or requested
    st.html('<div class="ss-rule"></div>')
    # Clear the previous business immediately before playback or a fresh run begins.
    desk = st.empty()
    with desk.container():
        if launch:
            run_batch(mode, int(limit))
        elif chosen:
            workspace(chosen, replay=mode == "replay")
        elif mode == "replay":
            st.info("Saved runs will appear here after your first batch.")
        else:
            st.info("Start a batch to see the agents research businesses and prepare eligible leads.")


if __name__ == "__main__":
    main()
