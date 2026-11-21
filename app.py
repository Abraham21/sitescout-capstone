"""The local SiteScout operator desk. Run with streamlit run app.py."""

from datetime import datetime
from html import escape
import os
import re
from concurrent.futures import Future
from threading import Thread
import time
from urllib.parse import urlencode, urlsplit
from zoneinfo import ZoneInfo

import streamlit as st

import pipeline
import desk_views
import services
import storage
from storage import usage
from studio import display_name


STAGES = ("Discover", "Qualify", "Build", "Write", "Review", "Publish", "Queue")
MODES = {
    "live": "Live discovery",
    "recheck": "Rechecked businesses",
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
CONTACT_GROUPS = {"ready": "Ready to contact", "contacted": "Contacted", "dismissed": "Dismissed"}
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
button[kind=primary]{background:#185c4e;border-color:#185c4e;color:white}
button[kind=primary]:hover{background:#104a3e;border-color:#104a3e;color:white}
button[kind=primary]:focus-visible,.ss-chain a:focus-visible{outline:3px solid #ba8043}
.ss-title,.ss-company{font:500 clamp(2rem,3vw,3.4rem)/1.02 Georgia,serif;letter-spacing:-.055em;margin:.2rem 0}
.ss-title{font-size:clamp(1.8rem,2.7vw,2.7rem)}
.ss-rule{height:1px;background:#c8d8ce;margin:.6rem 0}
.ss-companyline{display:flex;align-items:center;gap:.8rem;flex-wrap:wrap;margin:.4rem 0 .24rem}
.ss-companyline small{font-size:.82rem;color:#52695c}
.ss-timeline{display:grid;gap:.1rem}.ss-event{display:grid;grid-template-columns:4.9rem 5.2rem 1fr;align-items:baseline;gap:.3rem;padding:.48rem 0;border-bottom:1px solid #82958f38;font-size:.9rem}
.ss-event time{font:normal .75rem ui-monospace,monospace;color:#597168}
.ss-event b{font-size:.82rem}.ss-event p{margin:0;font-size:.92rem;line-height:1.4;overflow-wrap:anywhere}
.ss-event small{display:block;color:#5d7167}
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
.ss-batch{margin:.6rem 0 1.1rem;color:#20372a}.ss-batch header{display:flex;align-items:baseline;justify-content:space-between;gap:1rem;flex-wrap:wrap}
.ss-batch h2{font:500 1.4rem Georgia,serif;margin:.3rem 0}.ss-batch header small{font-size:.8rem;color:#476152}
.ss-batch table{width:100%;border-collapse:collapse;font-size:.84rem}.ss-batch th,.ss-batch td{padding:.35rem .45rem;text-align:center;border-bottom:1px solid #dce4dd}
.ss-batch thead th{font-size:.72rem;font-weight:600;color:#506458;background:#f1f5f1}.ss-batch tr>:first-child,.ss-batch tr>:last-child{text-align:left}
.ss-batch th[scope=row]{width:40%;font-weight:620;overflow-wrap:anywhere}.ss-batch td:last-child{width:18%;font-size:.78rem;font-weight:600}
.ss-step{display:inline-grid;place-items:center;width:1.12rem;height:1.12rem;border-radius:50%;font:700 .74rem/1 sans-serif;color:#7c8a7e}
.ss-step.done{color:#146649;background:#dfefe4}.ss-step.working{color:#195849;background:#d9eee4;outline:2px solid #508d71;outline-offset:1px}
.ss-step.stopped{background:#f8ebd1;color:#81560c}.ss-batch tbody tr:has(.working){background:#f5faf6}
.ss-status{display:inline-flex;align-items:center;gap:.4rem;line-height:1.3}.ss-status.active::before{content:'';border:2px solid #a5c8b7;border-top-color:#195849;width:.75rem;height:.75rem;box-sizing:border-box;border-radius:50%;animation:ss-spin 1s linear infinite}
@keyframes ss-spin{to{transform:rotate(360deg)}}@media(prefers-reduced-motion:reduce){.ss-status.active::before{animation:none}}
.ss-batch tr:has(.ss-following) th[scope=row]{box-shadow:inset 3px 0 #27704d;padding-left:.7rem}
.ss-following{display:inline-block;margin-left:.5rem;color:#286147;font-size:.72rem;font-weight:650}
@media(max-width:830px){.ss-batch .stage-col{display:none}.ss-batch th[scope=row]{width:62%}.ss-batch td:last-child{width:auto}.ss-batch header{gap:.25rem}}
@media(max-width:1150px){.stMainBlockContainer{padding-inline:1.6rem}.ss-chain{grid-template-columns:repeat(2,minmax(0,1fr))}.ss-card:nth-child(2)::after{display:none}}
@media(max-width:700px){.stMainBlockContainer{padding:4.2rem 1rem 1rem}.ss-chain{grid-template-columns:minmax(0,1fr);gap:.5rem}.ss-card:not(:last-child)::after{display:none}.ss-event{grid-template-columns:4rem 4rem 1fr}}

.ss-queue{border-top:1px solid #cbd8cf}.ss-lead{display:grid;grid-template-columns:minmax(170px,1.2fr) minmax(220px,1.3fr) auto;align-items:center;gap:1.5rem;padding:1.05rem .35rem;border-bottom:1px solid #d9e3dc}
.ss-lead h3{font:600 1.12rem system-ui;margin:0 0 .2rem;color:#213c2d}.ss-lead small{color:#597164}.ss-contacts{display:flex;align-items:center;flex-wrap:wrap;gap:.4rem 1.1rem;font-size:.93rem}.ss-contacts a{max-width:100%;overflow-wrap:anywhere}
.ss-queue a,.ss-inspect{color:#175c45}.ss-draft-link{font-weight:650}.ss-inspect{text-decoration:none}.ss-inspect:hover,.ss-inspect:focus-visible{text-decoration:underline}
.ss-draft{padding:0 0 .4rem;user-select:text}.ss-draft p{color:#263f30;white-space:pre-wrap;overflow-wrap:anywhere;line-height:1.65;font-size:1rem;margin:1rem 0}.ss-draft strong{display:block;font-size:.85rem;color:#52675b}
@media(max-width:700px){.ss-lead{grid-template-columns:1fr auto;gap:.45rem 1rem}.ss-contacts{grid-column:1/-1;grid-row:2}.ss-draft-link{grid-column:2;grid-row:1}}
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


def batch_board(events, leads=(), *, focused_id=None, interrupted=False, route, cursor=None):
    """Build each row from events already observed. Stored results are only passed after completion."""
    names = {}
    activity = {}
    for event in events:
        if event["stage"] == "Discover":
            for candidate in event.get("handoff", {}).get("candidates", []):
                names[candidate["id"]] = candidate["name"]
        lead_id = event.get("lead_id")
        if not lead_id:
            continue
        names[lead_id] = event["lead_name"]
        steps = activity.setdefault(lead_id, {})
        if event["stage"] == "Review" and event["status"] == "repair":
            for step in ("Build", "Write", "Review", "Publish"):
                steps.pop(step, None)
        steps[event["stage"]] = event
        steps["latest"] = event
    records = {lead["id"]: lead for lead in leads}
    names.update({lead_id: lead["name"] for lead_id, lead in records.items()})
    if not names:
        return
    stages = {
        "Qualify": "Research",
        "Build": "Build",
        "Write": "Write",
        "Review": "Review",
        "Publish": "Publish",
    }
    verbs = {
        "Qualify": "Researching",
        "Build": "Building site",
        "Write": "Writing pitch",
        "Review": "Reviewing",
        "Publish": "Publishing",
    }
    rows = []
    done = working = 0
    for lead_id, name in names.items():
        steps = activity.get(lead_id, {})
        latest = steps.get("latest", {})
        stage = latest.get("stage")
        terminal = steps.get("Queue", {})
        lead = records.get(lead_id, {})
        outcome = lead.get("status") or terminal.get("handoff", {}).get("outcome")
        finished = bool(terminal) or outcome in QUEUE_ORDER
        active = bool(stage and not finished and not interrupted)
        working += int(active)
        done += int(finished)
        state = OUTCOMES.get(outcome) if finished else None
        state = state or STAGE_LABELS.get(terminal.get("status"))
        if not state:
            state = "Interrupted" if interrupted else verbs.get(stage, "Waiting")
            if active and latest.get("status") == "repair":
                state = "Revising"
        marker = ' <small class="ss-following">Shown below</small>' if lead_id == focused_id else ""
        query = {**route, "business": lead_id}
        if cursor is not None:
            query["at"] = cursor
        inspect_url = escape("?" + urlencode(query) + "#business-detail", quote=True)
        business = f'<a class="ss-inspect" href="{inspect_url}" target="_self">{escape(display_name(name))}</a>{marker}'
        cells = []
        for step, label in stages.items():
            status = steps.get(step, {}).get("status", "")
            if status == "completed":
                kind, mark, title = "done", "✓", "Completed"
            elif status in ("started", "info", "repair") and active and step == stage:
                kind, mark, title = "working", "•", "In progress"
            elif status in ("held", "failed", "blocked"):
                kind, mark, title = "stopped", "!", STAGE_LABELS[status]
            else:
                kind, mark, title = "", "–", "Not reached"
            cells.append(
                f'<td class="stage-col" aria-label="{label}: {title}"><span class="ss-step {kind}" title="{label}: {title}">{mark}</span></td>'
            )
        rows.append(
            f'<tr><th scope="row">{business}</th>{"".join(cells)}<td><span class="ss-status {"active" if active else ""}">{escape(state)}</span></td></tr>'
        )
    waiting = len(names) - working - done
    summary = f"{done} of {len(names)} finished"
    if interrupted and waiting:
        summary += f" · {waiting} interrupted"
    elif working or waiting:
        summary += f" · {working} working · {waiting} waiting"
    elif records:
        contactable = sum(lead["status"] in {"ready", "manual"} for lead in records.values())
        summary += f" · {contactable} ready to contact"
    headings = "".join(f'<th scope="col" class="stage-col">{name}</th>' for name in stages.values())
    run_id = next(iter(route.values()))
    board_key = f"batch_table_{run_id}"

    def inspect_row():
        business = st.session_state[board_key].business
        if business in names:
            for target in ("follow", "inspect"):
                st.session_state[f"{target}_{run_id}"] = business
            st.session_state.pop("playing", None)
            select_business(run_id, f"inspect_{run_id}", "replay" in route, cursor)

    markup = (
        f'<section class="ss-batch" aria-label="Batch activity"><header><h2>Batch activity</h2><small role="status">{summary}</small></header>'
        f'<table aria-label="Business progress by stage"><thead><tr><th scope="col">Business</th>{headings}<th scope="col">Status</th></tr></thead><tbody>{"".join(rows)}</tbody></table></section>'
    )
    desk_views.business_links(key=board_key, on_business_change=inspect_row, data=markup)


def show_activity(events, run_id, *, replay=False, synthetic=False, cursor=None):
    # Only the visible prefix can disclose a business, stage, or link.
    names = {}
    for event in events:
        for item in (event.get("handoff") or {}).get("candidates", []):
            names[item["id"]] = item["name"]
        if event.get("lead_id"):
            names[event["lead_id"]] = event["lead_name"]
    ended = {e["lead_id"] for e in events if e["stage"] == "Queue" and e.get("lead_id")}
    active = [e for e in events if e.get("lead_id") and e["lead_id"] not in ended]
    automatic = next(
        (e["lead_id"] for e in active if e["stage"] == "Build"),
        active[-1]["lead_id"] if active else (events[-1].get("lead_id") if events else None),
    )
    key = f"follow_{run_id}"
    if st.session_state.get(key) not in names:
        st.session_state[key] = ""
    focus = st.session_state[key] or automatic
    batch_board(events, focused_id=focus, route={"replay" if replay else "run": run_id}, cursor=cursor)
    if names:
        st.selectbox(
            "Inspect business",
            ["", *names],
            format_func=lambda i: display_name(names[i]) if i else "Follow active business",
            key=key,
            on_change=select_business,
            args=(run_id, key, replay, cursor),
        )
    if focus in names:
        st.html(
            f'<div id="business-detail" class="ss-companyline"><h2 class="ss-company">{escape(display_name(names[focus]))}</h2></div>'
        )
        selected = [e for e in events if e.get("lead_id") == focus]
        if selected:
            handoff_chain(selected, synthetic=synthetic)
        else:
            st.caption("Waiting to start research on this business.")
    elif not names:
        st.caption("Discovering businesses…")
    with st.expander("Latest activity across the batch"):
        timeline(events[-4:])


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


def start_batch(mode, limit):
    """Detach the graph from Streamlit reruns so navigation cannot interrupt discovery."""
    started = Future()

    def announce(event):
        if not started.done():
            started.set_result(event["run_id"])

    def work():
        try:
            pipeline.run_batch(limit=limit, mode=mode, on_event=announce)
        except Exception as error:
            # The pipeline records errors after startup in the run's own activity.
            if not started.done():
                started.set_exception(error)

    Thread(target=work, daemon=True).start()
    return started.result(timeout=10)


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


def message_panel(lead, run_id, replay, *, synthetic=False):
    pitch = lead.get("pitch") or {}
    if not pitch.get("body"):
        st.caption("This lead has no outreach draft.")
        return
    key = f"{'replay_' if replay else ''}{run_id}_{lead['id']}"
    editable = not replay and lead["status"] in ("ready", "manual")
    q = lead.get("qualification") or {}
    contacts = contact_links(
        {
            "email": lead.get("email") or q.get("email"),
            "social": q.get("social"),
            "phone": q.get("phone") or lead.get("phone"),
        }
    )
    if synthetic:
        st.caption("Rehearsal contact · " + (lead.get("channel") or "Manual follow-up"))
    elif contacts:
        st.html('<div class="ss-context">Contact · ' + " · ".join(contacts) + "</div>")
    if editable:
        subject = st.text_input("Subject", value=pitch.get("subject", ""), key=f"subject_{key}")
        body = st.text_area("Message", value=pitch["body"], height=225, key=f"body_{key}")
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
    else:
        # A recording stays immutable. Ordinary document text supports selection and Cmd+C.
        st.html(
            f'<article class="ss-draft"><p><strong>Subject</strong>{escape(pitch.get("subject", ""))}</p><p><strong>Message</strong>{escape(pitch["body"])}</p></article>'
        )
        copy_draft(pitch.get("subject", ""), pitch["body"])
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
        wrapper = f'<style>*{{box-sizing:border-box}}body{{margin:0}}iframe{{width:100%;height:505px;border:0}}</style><iframe title="Business website preview" sandbox="allow-scripts allow-forms allow-downloads allow-popups allow-popups-to-escape-sandbox" srcdoc="{escape(html, quote=True)}"></iframe>'
        st.iframe(wrapper, height=510, alt="Saved business website preview")
    elif not url:
        st.caption("This lead has no website preview.")


def evidence_panel(lead, *, synthetic=False):
    qualification = lead.get("qualification") or {}
    if qualification.get("website_status"):
        st.caption(f"Website finding · {qualification['website_status'].replace('_', ' ')}")
    if synthetic:
        st.caption("These excerpts come from synthetic source snapshots.")
    elif web_url(qualification.get("website")):
        st.link_button("Open identified business site ↗", qualification["website"])
    desk_views.retrieval_view(lead, synthetic=synthetic)


def record_contact_action(run_id, lead_id, action):
    try:
        storage.set_contact_action(run_id, lead_id, action)
    except ValueError as error:
        st.toast(str(error))
        return
    if st.session_state.get("view") == "Contact queue":
        return_to_queue()
    notice = {
        "contacted": "Marked contacted. You can find this business under Contacted.",
        "dismissed": "Moved to Dismissed. You can restore it there.",
        "restore": "Restored. The latest research determines whether this business is ready to contact.",
    }
    st.toast(notice[action])


def lead_panel(lead, run_id, events, replay, *, synthetic=False, from_queue=False):
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
        f'<div id="business-detail" class="ss-companyline"><h2 class="ss-company">{escape(display_name(lead["name"]))}</h2>'
        f'<span class="ss-outcome {status}">{escape(OUTCOMES.get(status, status))}</span><small>{escape(identity)}</small></div>'
    )
    desk_views.decision_notes(lead)
    if not from_queue:
        handoff_chain(relevant, lead, synthetic=synthetic)
    follow_up = qualification.get("follow_up") or {}
    if not follow_up and lead["status"] == "hold":
        reason = qualification.get("reason")
        candidate = next((p.get("url") for p in reversed(qualification.get("evidence") or [])
                          if p.get("kind") in {"assessment", "observation"} and p.get("text") == reason), "")
        if candidate:
            follow_up = {"url": candidate, "reason": "Compare this page’s business name, phone and license with the registry record."}
        elif (qualification.get("activity") or {}).get("listing_city"):
            follow_up = {"source_url": lead.get("directory_url"),
                         "reason": "Look for the same business phone or license on a second business profile."}
    if follow_up.get("reason"):
        listing = "" if synthetic else link(follow_up.get("source_url"), "Directory source")
        candidate = "" if synthetic else link(follow_up.get("url"), "Open candidate site")
        sources = " · ".join(filter(None, (listing, candidate)))
        st.html(
            f'<div class="ss-context"><b>Manual verification</b> · {escape(follow_up["reason"])}<br>{sources}</div>'
        )
    draft, preview, evidence, trace = st.tabs(
        ["Outreach draft", "Website preview", "Research & RAG", "Agent trace"]
    )
    with draft:
        action = storage.contact_action(lead["id"]) if not replay and not synthetic else {}
        message_panel(lead, run_id, replay or bool(action), synthetic=synthetic)
        if action:
            stamp = local_time(action["changed_at"]) if action["changed_at"] else ""
            st.caption(" · ".join(filter(None, (CONTACT_GROUPS[action["state"]], stamp))))
            st.button("Restore lead", key=f"restore_{run_id}_{lead['id']}", on_click=record_contact_action,
                      args=(action["run_id"], lead["id"], "restore"),
                      help="Make the business eligible for the queue again if its latest draft is approved.")
        elif not replay and lead["status"] in ("ready", "manual", "hold"):
            with st.container(horizontal=True):
                if not synthetic and lead["status"] in ("ready", "manual"):
                    st.button("Mark contacted", key=f"contacted_{run_id}_{lead['id']}", on_click=record_contact_action,
                              args=(run_id, lead["id"], "contacted"),
                              help="Use this after emailing, calling, or messaging the business yourself.")
                if synthetic:
                    st.button("Dismiss rehearsal lead", key=f"dismiss_{run_id}_{lead['id']}",
                              on_click=pipeline.dismiss, args=(run_id, lead["id"]))
                else:
                    st.button("Dismiss lead", key=f"dismiss_{run_id}_{lead['id']}", on_click=record_contact_action,
                              args=(run_id, lead["id"], "dismissed"),
                              help="Move this business to Dismissed and skip future rechecks until you restore it.")
    with preview:
        desk_views.customization_notes(lead)
        preview_panel(lead)
    with evidence:
        evidence_panel(lead, synthetic=synthetic)
    with trace:
        if from_queue:
            handoff_chain(relevant, lead, synthetic=synthetic)
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
    synthetic = run["mode"] in ("demo", "sample")
    events = pipeline.events(run_id)
    st.caption(
        f"{MODES.get(run['mode'], run['mode'])} · {'Synthetic' if synthetic else 'Real'} businesses · {local_time(run['started_at'])} PT"
    )
    if run["status"] == "running":
        st.markdown("**Discovery is running**")
        show_activity(events, run_id, synthetic=synthetic)
        return
    if events:
        cursor_key = f"cursor_{run_id}"
        playing = st.session_state.get("playing")
        if playing and playing[0] == run_id:
            cursor = min(
                len(events),
                playing[1] + int((time.monotonic() - playing[2]) * max(2, len(events) / 16)),
            )
            st.session_state[cursor_key] = cursor
            if cursor == len(events) or st.button("Pause replay", key=f"pause_{run_id}"):
                st.session_state.pop("playing", None)
                st.rerun()
            st.progress(cursor / len(events), text=f"Playing recorded event {cursor} of {len(events)}")
            show_activity(events[:cursor], run_id, replay=True, synthetic=synthetic, cursor=cursor)
            return
        start = st.button("Play recorded run" if not replay else "Play replay", key=f"play_{run_id}")
        if start:
            old = st.session_state.get(cursor_key, len(events))
            first = 1 if old == len(events) else old
            st.session_state.update(replaying=run_id, playing=(run_id, first, time.monotonic()))
            st.rerun()
        if replay:
            end = len(events)
            cursor = st.slider("Recorded event", 1, end, end, key=cursor_key) if end > 1 else 1
            if cursor < end:
                show_activity(events[:cursor], run_id, replay=True, synthetic=synthetic, cursor=cursor)
                return
    leads = sorted(pipeline.leads(run_id, replay=replay), key=lambda l: QUEUE_ORDER.get(l["status"], 7))
    intake = next(
        (event["handoff"]["intake"] for event in events if event.get("handoff", {}).get("intake")),
        None,
    )
    if intake:
        if intake.get("selected") == 0 and "deferred_without_signal" in intake:
            st.info("No new painters showed a website opportunity in this scan. SiteScout finished without starting paid research.")
        with st.expander(f"How discovery chose {intake['selected']} businesses"):
            if intake.get("source_limited"):
                st.caption("Yahoo Local temporarily limited requests. These counts cover the listings available that day.")
            st.write(
                f"SiteScout searched Yahoo Local’s city painting directories and found {intake['recent_profiles']} "
                f"profiles with a dated Yelp review in the past year. Of those, {intake['unique_active_license_matches']} matched one "
                f"active painting license. {intake['eligible_unseen']} were new."
            )
            if "priority_candidates" in intake:
                signals = [
                    f"{intake['unseen_without_listed_website']} without a website link",
                    f"{intake['unseen_with_missing_link']} with a missing listed website",
                ]
                if intake.get("unseen_with_broken_estimate_link"):
                    signals.append(f"{intake['unseen_with_broken_estimate_link']} with a broken estimate link")
                if "unseen_with_launch_page_and_gallery" in intake:
                    signals.append(f"{intake['unseen_with_launch_page_and_gallery']} with a launch page and project photos")
                st.caption("New matches included " + ", ".join(signals) + ".")
                if intake.get("deferred_without_signal"):
                    st.caption(f"{intake['deferred_without_signal']} new painters had a listed website with no observed issue. They can be checked in a later scan.")
                elif intake["priority_candidates"] < intake["selected"]:
                    st.caption("To fill this batch, SiteScout also researched painters who already listed a website.")
            else:
                st.caption("Profiles without a listed website came first, followed by the most recent review.")
    elif run["mode"] == "live" and any(
        event["stage"] == "Discover" and "public listings." in event["message"] for event in events
    ):
        matches = sum(bool(lead.get("directory_url")) for lead in leads)
        st.caption(f"Discovery sources · {matches} directory matches · {len(leads) - matches} registry-only leads")
    records = {lead["id"]: lead for lead in leads}
    selected_key = f"inspect_{run_id}"
    if records and st.session_state.get(selected_key) not in records:
        followed = st.session_state.get(f"follow_{run_id}")
        st.session_state[selected_key] = followed if followed in records else next(iter(records))
    batch_board(
        events,
        leads,
        focused_id=st.session_state.get(selected_key),
        route={"replay" if replay else "run": run_id},
        cursor=len(events) if replay and events else None,
        interrupted=run["status"] in ("failed", "interrupted"),
    )
    if records:
        selected = st.selectbox(
            "Inspect business",
            list(records),
            format_func=lambda i: display_name(records[i]["name"]),
            key=selected_key,
            on_change=select_business,
            args=(run_id, selected_key, replay, len(events)),
        )
        lead_panel(records[selected], run_id, events, replay, synthetic=synthetic)
    elif not events:
        st.caption("This batch has no saved activity.")
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


def return_to_queue():
    st.session_state.pop("contact", None)
    set_location(group=st.session_state.queue_group)


def show_contacts(runs):
    st.header("Contact queue")
    group = st.segmented_control("Business status", list(CONTACT_GROUPS), format_func=CONTACT_GROUPS.get,
                                 default="ready", key="queue_group", required=True, on_change=return_to_queue)
    queue = storage.contact_queue(group)
    selected = next((row for row in queue if row["id"] == st.session_state.get("contact")), None)
    if selected:
        run_id = selected["run_id"]
        record = next((row for row in pipeline.leads(run_id) if row["id"] == selected["id"]), None)
        if record:
            st.button("← Back to queue", on_click=return_to_queue)
            lead_panel(record, run_id, pipeline.events(run_id), False, from_queue=True)
            return
    if not queue:
        if group != "ready":
            st.info("Businesses you mark contacted will appear here." if group == "contacted"
                    else "Businesses you dismiss will appear here.")
            return
        st.info(
            "Reviewed businesses and their websites will appear here after discovery finds a contact opportunity."
        )
        sample = next(
            (r for r in runs if r["mode"] in ("demo", "sample") and r.get("published_count")), None
        )
        if sample:
            url = escape(
                "?" + urlencode({"replay": sample["id"], "at": len(pipeline.events(sample["id"]))}),
                quote=True,
            )
            st.html(f'<a href="{url}" target="_self">Open saved rehearsal</a>')
        return
    cards = []
    for lead in queue:
        url = "?" + urlencode({"contact": lead["id"], "group": group})
        when = local_time(lead["changed_at"]) if lead["changed_at"] else ""
        detail = " · ".join(filter(None, (lead["city"], when)))
        open_label = "Open draft" if lead["preview_url"] else "Open business"
        name = escape(display_name(lead["name"]))
        destinations = [link(lead["preview_url"], "Website preview")]
        if web_url(lead.get("website")):
            destinations.append(link(lead["website"], "Current site"))
        links = "".join([*destinations, *contact_links(lead)])
        cards.append(
            f'<article class="ss-lead"><div><h3>{name}</h3><small>{escape(detail)}</small></div>'
            f'<div class="ss-contacts">{links}</div><a class="ss-draft-link" href="{escape(url, quote=True)}" target="_self">{open_label} →</a></article>'
        )
    def open_draft():
        lead_id = st.session_state.queue_links.business
        if any(row["id"] == lead_id for row in queue):
            st.session_state.contact = lead_id
            set_location(contact=lead_id, group=group)

    markup = f'<section class="ss-queue" aria-label="{CONTACT_GROUPS[group]}">' + "".join(cards) + "</section>"
    desk_views.business_links(key="queue_links", on_business_change=open_draft, data=markup)


def contact_links(lead):
    links = []
    if lead.get("email"):
        address = str(lead["email"])
        if re.fullmatch(r"[\w.+%-]+@[\w.-]+\.[A-Za-z]{2,}", address):
            links.append(f'<a href="mailto:{escape(address, quote=True)}">{escape(address)}</a>')
    if url := web_url(lead.get("social")):
        host = (urlsplit(url).hostname or "").removeprefix("www.")
        links.append(link(url, "Open Instagram" if host == "instagram.com" else "Social profile"))
    number = re.sub(r"[^+0-9]", "", lead.get("phone") or "")
    if number:
        links.append(f'<a href="tel:{number}">Call {escape(lead["phone"])}</a>')
    return links


def set_location(**query):
    # Header controls and row links should agree on where a browser reload will land.
    st.query_params.from_dict(query)
    st.session_state.opened_location = query


def select_business(run_id, key, replay, cursor):
    selected = st.session_state[key]
    if key == f"follow_{run_id}":
        st.session_state[f"inspect_{run_id}"] = selected
    query = {"replay" if replay else "run": run_id}
    if selected:
        query["business"] = selected
    if replay and cursor:
        query["at"] = str(cursor)
    set_location(**query)


def read_location(runs):
    """A business row is a bookmarkable link to this exact run and, in replay, event."""
    query = st.query_params.to_dict()
    if st.session_state.get("opened_location") == query:
        return
    st.session_state.opened_location = query
    if not query:
        st.session_state.pop("contact", None)
        st.session_state.update(view="Contact queue", queue_group="ready")
        return
    if query.get("view") == "runs":
        st.session_state.view = "Runs"
        return
    if query.get("view") == "workflow":
        st.session_state.view = "How it works"
        return
    if "contact" in query or "group" in query:
        contact = query.get("contact")
        action = storage.contact_action(contact) if contact else {}
        group = action.get("state") or query.get("group", "ready")
        st.session_state.update(view="Contact queue", contact=contact,
                                queue_group=group if group in CONTACT_GROUPS else "ready")
        return
    run_id = query.get("replay") or query.get("run")
    if run_id not in {r["id"] for r in runs}:
        return
    st.session_state.update(
        view="Runs", saved_run=run_id, replaying=run_id if "replay" in query else None
    )
    if "business" in query:
        st.session_state[f"inspect_{run_id}"] = query["business"]
        st.session_state[f"follow_{run_id}"] = query["business"]
    st.session_state.pop("playing", None)
    if "at" in query and query["at"].isdigit():
        end = len(pipeline.events(run_id))
        st.session_state[f"cursor_{run_id}"] = max(1, min(end, int(query["at"])))
    elif "replay" in query and "business" not in query:
        st.session_state["playing"] = (run_id, 1, time.monotonic())


def main():
    runs = pipeline.list_runs()
    read_location(runs)
    sources = {r["id"]: r for r in runs}
    schedule = pipeline.autopilot_status()
    active = next((r for r in runs if schedule["running"] and r["status"] == "running"), None)
    capable = bool(os.getenv("GOOGLE_API_KEY") and os.getenv("EXA_API_KEY"))
    title, start = st.columns([3, 1], vertical_alignment="center")
    with title:
        st.html('<h1 class="ss-title">SiteScout</h1>')
        latest = next((r for r in runs if r["mode"] == "live"), None)
        st.caption(
            "Discovery running"
            if active
            else (
                "Idle · Last discovery " + local_time(latest["started_at"]) + " PT"
                if latest
                else "Ready for the first discovery run"
            )
        )

    def launch(mode, limit):
        try:
            run_id = start_batch(mode, limit)
        except Exception as error:
            st.error(f"Could not start the batch. {error}")
            return
        st.session_state.update(view="Runs", saved_run=run_id, replaying=None)
        set_location(run=run_id)
        st.session_state.pop("playing", None)

    with start:
        if active:

            def follow_active():
                st.session_state.update(view="Runs", saved_run=active["id"], replaying=None)
                set_location(run=active["id"])

            st.button("View active run", on_click=follow_active, type="primary", width="stretch")
        else:
            st.button(
                "Run discovery",
                type="primary",
                on_click=launch,
                args=("live", st.session_state.get("limit_live", 10)),
                disabled=not capable,
                help="Find recently reviewed painters, match their licenses, then check their websites.",
                width="stretch",
            )
            if capable:
                spent = sum(row["dollars"] for row in usage() if row["service"] == "Exa")
                available = max(0, services.EXA_LIMIT - spent)
                if available < 0.25:
                    st.caption(
                        f"Local search budget has ${available:.2f} left. "
                        "A live batch may stop early. Saved replays are free."
                    )

    def navigate():
        st.session_state.pop("playing", None)
        if st.session_state.view == "How it works":
            set_location(view="workflow")
        elif st.session_state.view == "Runs":
            rid = st.session_state.get("saved_run") or next(iter(sources), None)
            st.session_state.replaying = None
            if rid:
                set_location(run=rid)
            else:
                set_location(view="runs")
        else:
            st.session_state.pop("contact", None)
            set_location()

    view = st.segmented_control(
        "Workspace",
        ["Contact queue", "Runs", "How it works"],
        default="Contact queue",
        required=True,
        key="view",
        on_change=navigate,
        label_visibility="collapsed",
    )
    st.html('<div class="ss-rule"></div>')

    # A background discovery can start or finish while any workspace is open.
    @st.fragment(run_every=4)
    def refresh_status():
        if pipeline.autopilot_status()["running"] != schedule["running"]:
            st.rerun()

    refresh_status()

    if view == "Contact queue":
        show_contacts(runs)
        if not capable:
            st.caption(
                "Live discovery requires configured services. You can explore the saved rehearsal in Runs."
            )
        return
    if view == "How it works":
        example = next(
            (
                r
                for r in runs
                if r["mode"] in ("live", "showcase", "recheck")
                and r["status"] == "complete"
                and r.get("published_count")
            ),
            None,
        )
        example = example or next(
            (r for r in runs if r.get("published_count") and r["status"] == "complete"), None
        )
        desk_views.how_it_works(
            pipeline.events(example["id"]) if example else (),
            synthetic=bool(example and example["mode"] in ("demo", "sample")),
        )
        return
    st.header("Runs")
    if not runs:
        st.caption("Batch activity will appear here when discovery starts.")
    else:
        if st.session_state.get("saved_run") not in sources:
            st.session_state.saved_run = (active or runs[0])["id"]

        def change_run():
            set_location(run=st.session_state.saved_run)
            st.session_state.pop("playing", None)
            st.session_state.pop("replaying", None)

        chosen = st.selectbox(
            "Run history",
            list(sources),
            format_func=lambda rid: (
                f"{local_time(sources[rid]['started_at'])} · {MODES.get(sources[rid]['mode'], sources[rid]['mode'])} · {rid[-6:]}"
            ),
            key="saved_run",
            on_change=change_run,
        )
    with st.expander("Run options"):
        st.number_input(
            "Businesses per discovery run", min_value=1, max_value=10, value=10, key="limit_live"
        )
        a, b = st.columns(2)
        with a:
            st.button(
                "Start live showcase",
                on_click=launch,
                args=("showcase", 3),
                disabled=not pipeline.showcase_available() or bool(active),
                help="Start with three known real licenses and research them afresh.",
            )
        with b:
            st.button(
                "Run synthetic rehearsal",
                on_click=launch,
                args=("demo", 10),
                disabled=bool(active),
                help="Exercise ten fictional cases with saved source evidence.",
            )
        enabled = bool(schedule["enabled"])
        toggle = st.toggle(
            "Daily autopilot", enabled, help="Research up to ten painters with a website opportunity daily at 9 AM on this Mac."
        )
        if toggle != enabled:
            try:
                pipeline.set_autopilot(toggle)
            except (OSError, RuntimeError, ValueError) as error:
                st.error(f"Schedule change failed. {error}")
            else:
                st.rerun()
    if not runs:
        return
    run = sources[chosen]
    running = bool(active and active["id"] == chosen)
    if run["status"] == "running" and not running:
        run = {**run, "status": "interrupted"}

    @st.fragment(run_every=0.45 if running or st.session_state.get("playing") else None)
    def run_view():
        if running and not pipeline.autopilot_status()["running"]:
            st.rerun()
        is_replay = st.session_state.get("replaying") == chosen or run["mode"] == "sample"
        workspace(run, replay=is_replay)

    run_view()


if __name__ == "__main__":
    main()
