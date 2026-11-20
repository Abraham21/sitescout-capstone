"""Reproduce the fixture benchmark, paired writing pilot and blind human review."""

import argparse
import hashlib
import json
import random
import time
import uuid
from collections import Counter
from html import escape
from pathlib import Path
from urllib.parse import urlsplit

import research
import storage
import studio


RESULTS = storage.ROOT / "evaluation"
CASES = storage.ROOT / "examples/scenarios.json"
PILOT_CAP = 0.30


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + "\n")
    return path


def fixture_qualification(case, mode, model):
    # Frozen search and HTTP snapshots are identical for both retrieval arms.
    def fetch(url):
        return case["pages"].get(
            url, {"url": url, "status": 0, "text": "", "links": [], "has_form": False}
        )

    return research.qualify(
        case["lead"], model, lambda _: case["results"], fetcher=fetch, retrieval=mode
    )


def retrieval_row(case, qualification):
    chosen = [e for e in qualification["evidence"] if e["id"] in qualification["retrieved_ids"]]
    gold = case["expected"]
    target = gold.get("evidence_url")
    needle = gold.get("evidence_contains")
    at_url = [e["text"] for e in chosen if e["url"] == target]
    return {
        "status": qualification["status"],
        "website_status": qualification["website_status"],
        "selected_passages": len(chosen),
        "available_passages": len(qualification["evidence"]),
        "selected_chars": sum(len(e["text"]) for e in chosen),
        "context_sha256": hashlib.sha256(json.dumps(chosen, sort_keys=True).encode()).hexdigest(),
        "expected_url_in_selection": bool(at_url) if target else None,
        "expected_passage_in_selection": any(needle.casefold() in t.casefold() for t in at_url)
        if needle
        else None,
    }


def count_rate(values):
    values = [v for v in values if v is not None]
    return {"matched": sum(values), "total": len(values)}


def fixture_benchmark(cases):
    rows = []
    for case in cases:
        row = {"case": case["id"], "expected": case["expected"]}
        for mode in ("fts", "full"):
            q = fixture_qualification(case, mode, None)
            row[mode] = retrieval_row(case, q)
        rows.append(row)
    summary = {}
    for mode in ("fts", "full"):
        arm = [r[mode] for r in rows]
        summary[mode] = {
            "route_agreement": count_rate(r[mode]["status"] == r["expected"]["status"] for r in rows),
            "website_agreement": count_rate(
                r[mode]["website_status"] == r["expected"]["website_status"] for r in rows
            ),
            "routes": dict(Counter(r["status"] for r in arm)),
            "abstentions": sum(r["website_status"] == "unsure" for r in arm),
            "wrong_no_site_among_known_sites": sum(
                r[mode]["website_status"] == "no_site_found"
                for r in rows
                if r["expected"]["website_status"] == "has_site"
            ),
            "known_sites": sum(r["expected"]["website_status"] == "has_site" for r in rows),
            "seeded_blocks_caught": count_rate(
                r[mode]["status"] == "blocked" for r in rows if r["expected"]["status"] == "blocked"
            ),
            "benign_cases_blocked": sum(
                r[mode]["status"] == "blocked" for r in rows if r["expected"]["status"] != "blocked"
            ),
            "benign_cases": sum(r["expected"]["status"] != "blocked" for r in rows),
            "gold_source_url_retrieved": count_rate(r["expected_url_in_selection"] for r in arm),
            "gold_passage_retrieved": count_rate(r["expected_passage_in_selection"] for r in arm),
            "selected_passages": sum(r["selected_passages"] for r in arm),
            "selected_chars": sum(r["selected_chars"] for r in arm),
            "qualified_selected_chars": sum(
                r["selected_chars"] for r in arm if r["status"] == "qualified"
            ),
        }
    return {
        "population": "Authored synthetic cases with known expected outcomes. These cases were visible during development.",
        "method": "Same frozen pages and deterministic policy for both arms. No LLM call. Identity and contact gates run before retrieval, which supplies evidence for factual extraction and possible model escalation.",
        "route_changes": sum(r["fts"]["status"] != r["full"]["status"] for r in rows),
        "summary": summary,
        "cases": rows,
    }


def paired_pilot(cases):
    """Only this explicitly requested benchmark can make paid calls."""
    import services

    storage.initialize()
    run_id = "eval-" + uuid.uuid4().hex[:10]
    usage_ceiling = 0
    rows, errors = [], []
    # Predeclared: the two original pitch-eligible fixture mechanisms, equally weighted.
    for i, case in enumerate(c for c in cases if c["id"] in ("social-estimates", "broken-estimate")):
        row = {"case": case["id"], "retrieval": {}, "writing": {}}
        lead = case["lead"]
        actual_calls = []

        def call(prompt):
            nonlocal usage_ceiling
            maximum = (len(prompt.encode()) + 256) * 0.75e-6 + services.MAX_OUTPUT * 3.75e-6
            if usage_ceiling + maximum > PILOT_CAP:
                errors.append({"case": case["id"], "error": "Pilot reservation cap"})
                raise RuntimeError("Pilot reservation cap reached.")
            usage_ceiling += maximum
            actual_calls.append(prompt.partition("\n")[0][:22])
            try:
                return services.gemini(prompt, run_id, lead["id"])
            except Exception as error:
                errors.append({"case": case["id"], "error": type(error).__name__})
                raise

        for mode in ("fts", "full") if i == 0 else ("full", "fts"):
            try:
                q = fixture_qualification(case, mode, call)
            except Exception:
                row["retrieval"][mode] = {"status": "provider_error"}
                continue
            row["retrieval"][mode] = {**retrieval_row(case, q), "extracted_facts": q["facts"]}
        # Isolate writing from the stochastic qualifier: both known eligible cases
        # get fixed cite-checked observations even if an experimental qualifier abstained.
        q = fixture_qualification(case, "fts", None)
        if q and q["status"] == "qualified":
            preview = studio.build_preview(lead, q)
            preview["url"] = f"https://preview.example/{case['id']}"
            # Same frozen qualified facts, preview and choices. The own-site URL is fictional.
            for name in ("direct", "tree") if i == 0 else ("tree", "direct"):
                start = time.monotonic()
                before = len(actual_calls)
                pitch = studio.write_pitch(lead, q, preview, call, branch=name == "tree")
                checks = studio.review(lead, q, preview, pitch, None)
                row["writing"][name] = {
                    "calls": len(actual_calls) - before,
                    "seconds": round(time.monotonic() - start, 2),
                    "mode_returned": pitch["mode"],
                    "angle": pitch["angle_id"],
                    "invitation": pitch["invitation"],
                    "subject": pitch["subject"],
                    "body": pitch["body"],
                    "evidence_ids": pitch["evidence_ids"],
                    "code_checks": {
                        k: v for k, v in checks["checks"].items() if "model review" not in k.casefold()
                    },
                    "runner_up": (pitch.get("runner_up") or {}).get("id"),
                }
        rows.append(row)
    return {
        "population": "Two predeclared synthetic painting cases, each tried once per condition. Human preference remains unmeasured.",
        "method": "The qualifier reads the two retrieval contexts. A separate writing experiment uses the same frozen, deterministically qualified fixture for both writers regardless of qualifier abstention. Direct uses one Gemini writing call; the tree prunes angles first. Evidence, model, template choices, URL, and final code checks are shared. Order alternates by case. Code checks exclude the separate production model-review availability gate.",
        "model": services.MODEL,
        "run_id": run_id,
        "usage": storage.usage(run_id),
        "errors": errors,
        "cases": rows,
    }


def choose_blind(records, count=5, seed=711):
    rng = random.Random(seed)
    rng.shuffle(records)
    # Case mix is deliberately stratified, so results describe these five cases.
    sample = []
    for group in ("qualified", "skip", "hold", "blocked"):
        members = [r for r in records if r["qualification"]["status"] == group]
        if members:
            if group == "qualified":
                members.sort(
                    key=lambda r: bool(
                        (r.get("pitch") or {}).get("body") and (r.get("preview") or {}).get("published")
                    ),
                    reverse=True,
                )
                sample.extend(members[:2])
            else:
                members.sort(key=lambda r: r.get("source_mode") == "live", reverse=True)
                sample.append(members[0])
    sample += [r for r in records if r not in sample][: max(0, count - len(sample))]
    rng.shuffle(sample)
    return sample[:count]


def question(label, name, choices):
    options = '<option value="">Choose…</option>' + "".join(
        f'<option value="{escape(value)}">{escape(caption)}</option>' for value, caption in choices
    )
    return f'<label>{label}<select required name="{name}">{options}</select></label>'


def source_block(item):
    text = escape(str(item.get("text", "")))
    url = item.get("url") or ""
    if not studio.web_url(url):
        return f"<blockquote>{text}</blockquote>"
    label = escape(str(item.get("id") or "Source")) + " · " + escape(urlsplit(url).hostname or "")
    return f'<blockquote>{text}<small><a href="{escape(url, quote=True)}" target="_blank" rel="noreferrer">{label}</a></small></blockquote>'


def blind_page(items, packet_id, phase):
    cards = []
    for code, lead in items:
        q = lead["qualification"]
        pitch = lead.get("pitch") or {}
        if phase == "drafts" and (q["status"] != "qualified" or not pitch.get("body")):
            continue
        body = (
            f"<h2>{escape(code)} · {escape(lead['name'])}</h2><p>{escape(lead.get('city') or '')}</p>"
        )
        body += (
            "<details open><summary>Observed public evidence</summary>"
            + "".join(source_block(e) for e in q["evidence"])
            + "</details>"
        )
        if phase == "routes":
            body += question(
                "Website ownership",
                "website_status",
                [
                    ("has_site", "Confirmed business website"),
                    ("no_site_found", "No owned website found in this search"),
                    ("unsure", "Insufficient or conflicting evidence"),
                ],
            )
            body += question(
                "What should happen next?",
                "route",
                [
                    ("qualified", "Build a preview for a supported opportunity"),
                    ("skip", "Skip this opportunity"),
                    ("hold", "Hold for more evidence"),
                    ("blocked", "Block unsafe content"),
                ],
            )
        else:
            body += f"<h3>{escape(pitch['subject'])}</h3><pre>{escape(pitch['body'])}</pre>"
            preview = lead.get("preview") or {}
            if preview.get("published") and studio.web_url(preview.get("url")):
                body += f'<p><a href="{escape(preview["url"], quote=True)}" target="_blank" rel="noreferrer">Inspect website preview</a></p>'
            body += question(
                "Draft readiness",
                "readiness",
                [
                    ("ready", "Ready for manual contact"),
                    ("edit", "Needs an edit"),
                    ("withhold", "Withhold"),
                    ("insufficient", "Insufficient evidence to judge"),
                ],
            )
        body += '<label>Reason or decisive source<textarea required name="reason"></textarea></label>'
        cards.append(f'<fieldset data-code="{code}">{body}</fieldset>')
    title = "Independent lead review" if phase == "routes" else "Independent draft review"
    instructions = (
        "Complete and export this packet before opening the draft packet. Use the public sources to check identity, ownership and whether there is a specific customer-facing opportunity."
        if phase == "routes"
        else "Read the business evidence, preview and message. Judge factual support, fit, and whether you would use this draft for manual contact."
    )
    practice = (
        "<details><summary>Practice example · fictional</summary>A directory leaves a painter’s website blank and the only candidate domain times out once. Choose insufficient evidence and hold. A failed request leaves ownership unresolved.</details>"
        if phase == "routes"
        else ""
    )
    return f"""<!doctype html><html lang="en"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>{title}</title>
<style>:root{{color-scheme:light;font:16px/1.5 system-ui;color:#203a30;background:#f6f5ef}}body{{max-width:49rem;margin:3rem auto;padding:0 1rem}}h1,h2{{font-family:Georgia,serif}}fieldset{{border:1px solid #a9b8af;border-radius:8px;margin:1.5rem 0;padding:1rem 1.4rem;background:#fff}}label,small{{display:block;margin:.8rem 0}}select,textarea,input,button{{display:block;font:inherit;padding:.45rem;max-width:100%}}textarea{{width:95%}}blockquote{{margin:1rem 0;border-left:3px solid #779b89;padding-left:1rem}}pre{{white-space:pre-wrap;font:inherit}}a{{color:#145b47}}button{{background:#185942;color:white;border:0;border-radius:5px;padding:.7rem 1.2rem;cursor:pointer}}</style>
<h1>{title}</h1><p>{instructions}</p>{practice}<form><label>Reviewer name or initials<input name="reviewer" required></label>
{"".join(cards) or "<p>No eligible drafts were saved for this packet.</p>"}<button>Export my answers</button><p role="status"></p></form>
<script>document.querySelector('form').onsubmit=e=>{{e.preventDefault();const form=e.target;
const answers=[...form.querySelectorAll('fieldset')].map(card=>Object.fromEntries([['case',card.dataset.code],...[...card.querySelectorAll('select,textarea')].map(f=>[f.name,f.value])]));
const obj={{packet:'{packet_id}',phase:'{phase}',reviewer:form.elements.reviewer.value,answers}};
const a=document.createElement('a');a.href=URL.createObjectURL(new Blob([JSON.stringify(obj,null,2)],{{type:'application/json'}}));a.download='sitescout-{phase}-answers.json';a.click();setTimeout(()=>URL.revokeObjectURL(a.href),2000);document.querySelector('[role=status]').textContent='Answer file prepared.';}};</script></html>"""


def blind_cards():
    folder = storage.runtime_dir() / "blind-review"
    existing = folder / "key.json"
    if existing.exists():
        return folder
    unique = {}
    for run in storage.list_runs():
        if run["mode"] not in ("live", "showcase") or run["status"] not in (
            "complete",
            "complete_with_errors",
        ):
            continue
        for lead in storage.leads(run["id"]):
            if lead.get("qualification", {}).get("evidence") and lead["status"] != "error":
                unique.setdefault(
                    lead["id"],
                    {
                        **lead,
                        "pitch": lead.get("original_pitch", lead.get("pitch")),
                        "source_mode": run["mode"],
                        "source_run": run["id"],
                    },
                )
    if len(unique) < 5:
        raise ValueError(
            f"Five actual businesses with completed research are needed; {len(unique)} are available."
        )
    sample = choose_blind(list(unique.values()))
    packet_id = hashlib.sha256("|".join(str(r["id"]) for r in sample).encode()).hexdigest()[:12]
    items = [(f"Case {i}", r) for i, r in enumerate(sample, 1)]
    folder.mkdir(parents=True, exist_ok=True)
    for phase in ("routes", "drafts"):
        prefix = "01" if phase == "routes" else "02"
        (folder / f"{prefix}-{phase}.html").write_text(blind_page(items, packet_id, phase))
    write_json(
        existing,
        {
            "packet": packet_id,
            "selection": "Five distinct real businesses from completed live discovery or curated showcase, stratified by available qualification outcome and shuffled. Superseded runs and research errors excluded. A challenge sample cannot estimate market prevalence.",
            "system_outputs": {
                code: {
                    "lead_id": r["id"],
                    "source_mode": r["source_mode"],
                    "source_run": r["source_run"],
                    "website_status": r["qualification"]["website_status"],
                    "route": r["qualification"]["status"],
                    "draft_available": r["qualification"]["status"] == "qualified"
                    and bool((r.get("pitch") or {}).get("body")),
                }
                for code, r in items
            },
        },
    )
    return folder


def score_blind(path):
    answers = json.loads(Path(path).read_text())
    key = json.loads((storage.runtime_dir() / "blind-review/key.json").read_text())
    if answers["packet"] != key["packet"]:
        raise ValueError("These answers belong to a different review packet.")
    rows = answers["answers"]
    codes = [row["case"] for row in rows]
    if len(codes) != len(set(codes)) or any(code not in key["system_outputs"] for code in codes):
        raise ValueError("Duplicate or unknown case identifier.")
    if answers["phase"] == "drafts":
        if set(codes) != {c for c, v in key["system_outputs"].items() if v["draft_available"]}:
            raise ValueError("A completed draft packet is required.")
        return {
            "reviewer": answers["reviewer"],
            "draft_readiness": dict(Counter(r["readiness"] for r in rows)),
        }
    if set(codes) != set(key["system_outputs"]) or answers["phase"] != "routes":
        raise ValueError("A completed routing packet is required.")
    return {
        "reviewer": answers["reviewer"],
        "system_human_agreement": {
            field: count_rate(r[field] == key["system_outputs"][r["case"]][field] for r in rows)
            for field in ("route", "website_status")
        },
    }


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    options = parser.add_mutually_exclusive_group(required=True)
    for option in ("fixtures", "paired-pitches", "blind-cards"):
        options.add_argument("--" + option, action="store_true")
    options.add_argument("--score-blind", metavar="ANSWERS.json")
    args = parser.parse_args()
    if args.blind_cards:
        print(blind_cards())
    elif args.score_blind:
        print(json.dumps(score_blind(args.score_blind), indent=2))
    else:
        cases = json.loads(CASES.read_text())
        if args.fixtures:
            print(write_json(RESULTS / "results.json", fixture_benchmark(cases)))
        else:
            print(write_json(RESULTS / "paired-pitches.json", paired_pilot(cases)))


if __name__ == "__main__":
    main()
