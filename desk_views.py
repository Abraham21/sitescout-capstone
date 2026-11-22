"""Read the saved handoffs and evidence. These views never call an external service."""

import base64
import json
import re
from html import escape
from urllib.parse import urlsplit

import streamlit as st

from studio import ASSETS, display_name, web_url


# A normal click selects in-place. The links still support copying a URL or opening a new tab.
business_links = st.components.v2.component(
    "sitescout_business_links",
    html='<div class="ss-links-root"></div>',
    isolate_styles=False,
    js="""export default function({parentElement, data, setTriggerValue}) {
      const root = parentElement.querySelector('.ss-links-root');
      // Keep the queue links mounted so the dialog can return keyboard focus
      // to the same row when it closes.
      if (root.renderedMarkup !== data) {
        root.innerHTML = data;
        root.renderedMarkup = data;
      }
      root.onclick = event => {
        if (event.ctrlKey || event.metaKey || event.shiftKey || event.altKey) return;
        const draft = event.target.closest('a.ss-draft-link');
        const link = draft || event.target.closest('tbody tr')?.querySelector('a.ss-inspect');
        if (!link) return;
        event.preventDefault();
        setTriggerValue('business', new URL(link.href).searchParams.get(draft ? 'contact' : 'business'));
      };
    }""",
)


def decision_notes(lead):
    q = lead.get("qualification") or {}
    if not q.get("reason"):
        return
    built = bool((lead.get("preview") or {}).get("html"))
    st.markdown("**Why this site was built**" if built else "**Research decision**")
    notes = []
    activity = q.get("activity") or {}
    social = q.get("social", "")
    platform = (urlsplit(social).hostname or "").removeprefix("www.").split(".")[0] if web_url(social) else ""
    if activity.get("date") and activity["date"] not in q["reason"]:
        notes.append(f"A customer reviewed this business on Yelp on {activity['date']}.")
    elif built and platform:
        notes.append(f"The researcher matched the business to its public {platform.title()} profile.")
    notes.append(q["reason"])
    if built:
        features = (lead.get("preview") or {}).get("features", [])
        if "Sourced project photography" in features:
            notes.append("Homeowners can see this painter’s work and try an estimate request on the same page.")
        else:
            notes.append("The concept gives homeowners a dedicated place to find the business contact and plan an estimate.")
    st.html("<ul>" + "".join(f"<li>{escape(note)}</li>" for note in notes) + "</ul>")


def customization_notes(lead):
    preview = lead.get("preview") or {}
    notes = preview.get("customizations") or re.split(r"(?<=\.)\s+", preview.get("handoff", ""))
    notes = [note for note in notes if note]
    if notes:
        st.markdown("**How this site was customized**")
        st.html("<ul>" + "".join(f"<li>{escape(note)}</li>" for note in notes) + "</ul>")


def retrieval_view(lead, *, synthetic=False):
    q = lead.get("qualification") or {}
    passages = q.get("evidence") or []
    if not passages:
        st.caption("Evidence will appear after the researcher checks this business.")
        return
    retrieved = set(q.get("retrieved_ids", []))
    decisive = set(q.get("issue_evidence_ids", []))
    facts = q.get("facts", {}).get("citations", {})
    inferred = q.get("model_mode") == "model-assisted"
    st.markdown("**Retrieval-augmented generation (RAG)**")
    total, chosen, used = st.columns(3)
    total.metric("Saved passages", len(passages))
    chosen.metric("Retrieved", len(retrieved))
    used.metric("Cited for the finding", len(decisive))
    if inferred:
        st.write("Before Gemini describes this business, SiteScout searches its saved sources for the "
                 "passages about identity, contact details and services. Gemini can suggest a fact for the "
                 "website or flag conflicting evidence. SiteScout checks the cited words before using a fact.")
        extracted = [(fact, ref) for fact, ref in facts.items() if ref in retrieved and ref != "registry"]
        if extracted:
            fact, ref = extracted[0]
            st.html(f"<p><b>Example from this business</b> · {escape(str(fact))} · passage {escape(str(ref))}</p>")
    else:
        st.write("The researcher reached this decision using identity and website checks. "
                 "The saved passages below let you verify what it found.")
    if synthetic:
        st.caption("These passages come from fictional source snapshots.")
    for chosen in (True, False):
        group = [p for p in passages if (p["id"] in retrieved) == chosen]
        if not group:
            continue
        label = "Retrieved passages" if chosen else "Other saved passages"
        group.sort(key=lambda p: p["id"] not in decisive)
        with st.expander(f"{label} ({len(group)})", expanded=chosen):
            for p in group:
                eid, url = p["id"], p.get("url", "")
                host = urlsplit(url).hostname if web_url(url) else ""
                source = escape(host or "Saved observation")
                if host and not synthetic:
                    source = f'<a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">{source} ↗</a>'
                uses = [fact for fact, ref in facts.items() if ref == eid]
                if eid in decisive:
                    uses.insert(0, "Qualification finding")
                note = f"<p><small>Supports · {escape(' · '.join(uses))}</small></p>" if uses else ""
                passage = str(p.get("text", ""))
                if len(passage) > 420:
                    intro = passage[:360].rsplit(" ", 1)[0]
                    entry = f"<p>{escape(intro)}…</p><details><summary>Read full passage</summary><p>{escape(passage)}</p></details>"
                else:
                    entry = f"<p>{escape(passage)}</p>"
                st.html(
                    f'<article class="ss-note"><small>{escape(eid)} · {source}</small>'
                    f"{entry}{note}</article>"
                )


def technologies_used():
    st.subheader("Technologies Used")
    technologies = [
        ("LangGraph", "langgraph",
         "Routes painters between the four agents and sends work back when the reviewer requests changes."),
        ("Exa", "exa",
         "Searches the web for the painter’s own website, public profiles and ways to get in touch."),
        ("SQLite", "sqlite",
         "Saves each run. Its full-text search retrieves source passages about one painter for Gemini to read."),
        ("Gemini Flash", "gemini",
         "Reads source passages and photos, helps write the outreach, and checks the draft against the evidence and preview."),
        ("Python", "python",
         "Builds the website preview using the painter’s verified details, project photos and contact information."),
        ("Cloudflare Pages", "cloudflare-pages",
         "Hosts the approved preview. SiteScout verifies the published page before adding its link to the contact queue."),
        ("Streamlit", "streamlit",
         "Powers this desk, where you can watch a run, inspect the evidence and decide who to contact."),
    ]
    entries = []
    for name, slug, role in technologies:
        logo = (ASSETS / "tech-logos" / f"{slug}.svg").read_bytes()
        src = "data:image/svg+xml;base64," + base64.b64encode(logo).decode("ascii")
        entries.append(
            f'<div><dt><img src="{src}" alt="" width="36" height="36">{escape(name)}</dt>'
            f'<dd>{escape(role)}</dd></div>'
        )
    st.html('<dl class="ss-technologies">' + "".join(entries) + "</dl>")


def how_it_works(events=(), *, synthetic=False):
    st.subheader("How SiteScout works")
    st.caption("Approved drafts enter the contact queue. Holds and skips stay in the run history.")
    names = {e["lead_id"]: e["lead_name"] for e in events if e.get("lead_id")}
    published = {
        e["lead_id"] for e in events if e.get("stage") == "Publish" and e["status"] == "completed"
    }
    steps = []
    if names:
        if st.session_state.get("graph_business") not in names:
            st.session_state.pop("graph_business", None)

        def remember_business():
            st.query_params.from_dict({"view": "workflow", "run": events[0]["run_id"],
                                       "business": st.session_state.graph_business})

        lead_id = st.selectbox(
            "Follow a business through the graph",
            sorted(names, key=lambda key: (key not in published, names[key])),
            format_func=lambda key: display_name(names[key]),
            key="graph_business",
            on_change=remember_business,
        )
        st.caption(
            "Playing recorded handoffs from a "
            + ("fictional rehearsal." if synthetic else "real discovery run.")
        )
        for e in events:
            batch_start = e.get("stage") == "Discover" and not e.get("lead_id")
            if (batch_start or e.get("lead_id") == lead_id) and e["status"] not in {"started", "info"}:
                steps.append({"stage": e["stage"], "status": e["status"], "message": e["message"]})
    template = (ASSETS / "workflow.html").read_text()
    # A source or model observation may contain HTML. Keep it inside JSON and render as text.
    data = json.dumps(steps, ensure_ascii=False).replace("<", "\\u003c")
    st.iframe(template.replace("WORKFLOW_STEPS", data), alt="SiteScout workflow playback")
    technologies_used()
