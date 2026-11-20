# SiteScout

SiteScout is a research assistant for an independent web designer. It discovers licensed painting businesses in the Los Angeles area, checks whether customers can reach them online, and prepares a website concept and a personalized outreach draft when the evidence supports one. Abraham decides whether to make contact.

**For a first look**, open the operator desk, select **Saved replay**, and play a recorded run. A fresh clone includes a three-business synthetic recording with real reviewed drafts, two distinct hosted websites and the source observations behind each decision. Playback and the embedded previews work without credentials. The prepared Mac also has a **Featured live run** from real public sources.

## Open the desk

On Abraham’s prepared Mac, double-click **Open SiteScout.command** in Finder. It opens the running desk or starts it. You can also use these commands when the desk is closed. Open **http://127.0.0.1:8501** in a browser.

```sh
cd /Users/abe/agent/cmu-agentic-ai/sitescout-capstone
.venv/bin/streamlit run app.py
```

On a new Mac or Linux checkout, use Python 3.12 with SQLite FTS5 support. The following uses [uv](https://docs.astral.sh/uv/). Standard Python 3.12 with `python3.12 -m venv .venv` and `.venv/bin/python -m pip install -r requirements.txt` works too.

```sh
git clone https://github.com/Abraham21/sitescout-capstone.git
cd sitescout-capstone
uv venv --python 3.12
uv pip install -r requirements.txt
.venv/bin/streamlit run app.py
```

### Choose a run

| Operator control | What actually happens |
| --- | --- |
| Featured live run | Opens a completed real run with a verified hosted preview. Select **Play this run** for its recorded timeline. |
| Live discovery | Selects unseen actual licenses, searches the web, and inspects current public pages. A batch can legitimately end entirely in holds and skips. |
| Curated live showcase | Rechecks a preselected pair of actual licenses against **current** sources. Outcomes can change. This is the quickest way to show fresh research and the full workflow on the prepared Mac. |
| Demo fixtures | Uses frozen synthetic search and page snapshots with deterministic qualification. With model and hosting configured, it writes, reviews and publishes anew. Without hosting, eligible concepts remain available in the local Preview tab. |
| Saved replay | Plays stored timestamps in order. Move the event slider to pause and inspect. Playback incurs no inference or publication cost. |

Select a business to inspect the four tabs. **Evidence** shows sources and the qualification decision. **Website preview** opens the saved layout or verified public URL. **Agent trace** shows tool events, feedback and repair attempts. In **Outreach draft**, the operator can edit and copy a reviewed message. A public phone or social profile routes the lead to **Manual contact**. Dismissed businesses are excluded from future discovery. SiteScout has no email delivery integration.

## Read the code

Start with [pipeline.py](pipeline.py). Read `run_lead` from the node definitions through the graph edges. Each node returns the few fields it adds to the business record. Read `run_batch` second to see where the businesses and tools come from. The same graph powers the command line, dashboard and scheduled run.

```mermaid
flowchart LR
    D[Discover] --> Q[Qualify]
    Q -->|evidence supports opportunity| B[Build concept]
    Q -->|healthy, uncertain, or blocked| O[Operator queue]
    B --> W[Write pitch]
    W --> R[Review]
    R -->|revision, up to twice| B
    R -->|unresolved| O
    R -->|approved| P[Publish and verify URL]
    P --> O
```

| File | Where to look |
| --- | --- |
| [research.py](research.py) | `discover` filters the public California CSLB file. `qualify` matches identities, checks possible sites and their estimate links, and retrieves evidence for the model. |
| [studio.py](studio.py) | `build_preview` fills the painter template. `write_pitch` compares grounded openings. `review` rebuilds the HTML, verifies the claims and asks Gemini for a separate judgment. |
| [assets/painter.html](assets/painter.html) | The responsive template contains two visual directions, a color palette and an interactive project brief. The brief stays in the visitor’s browser. The [painter skill](.agents/skills/painter-preview/SKILL.md) explains which facts and images may appear. |
| [services.py](services.py), [publication.py](publication.py) | The Gemini and Exa calls, followed by direct upload to Cloudflare Pages and a check of the HTML actually served. |
| [storage.py](storage.py), [autopilot.py](autopilot.py) | SQLite stores events, results, spending reservations and operator edits. macOS launchd schedules one batch per day when enabled. |
| [app.py](app.py), [evaluation.py](evaluation.py) | The Streamlit operator desk and the reproducible evaluation commands. |

### How a lead earns a preview

1. Discovery filters active, unexpired C-33 licenses in Glendale, Burbank, Pasadena and Los Angeles. The four cities alternate in a batch. Only business fields used for research are retained. The filtered registry is cached for a day. [CSLB data portal](https://www.cslb.ca.gov/OnlineServices/DataPortal/)
2. The qualifier searches by identity, cross-checks by phone or license as needed, and opens plausible first-party sites. A third-party license mirror never counts as the owner’s site. A verified site with a working published phone can pass the contact check. Repeated missing-page responses from an estimate link can establish a specific defect.
3. A social or directory presence can establish an opportunity when independent identity-matched sources support a real contact gap and two searches fail to identify an owned estimate site. The draft describes what was checked. Weak evidence goes on hold. Instruction-like source text can block the case.
4. **RAG** uses SQLite full-text search over a versioned evidence set for one business. Name, locality, contact and service queries retrieve passages. Gemini may extract explicitly cited services or flag a contradiction. A deterministic check requires the extracted wording to occur in the cited text.
5. The writer uses bounded **tree-of-thought-style search** when it has competing observations. It compares possible opening angles, shortlists up to two, and compares the resulting pitches. The trace shows inspectable alternatives and evidence IDs. Gemini Flash is the sole cloud model to keep the implementation small. The course project has too few independently labeled examples to justify fine-tuning; local inference is a future comparison.
6. The reviewer validates the actual preview and the draft. A failed review can send the lead back through the builder and writer twice. Cloudflare receives only the approved static preview. A content fingerprint must match at the public address before a draft enters the contact queue. Photos are labeled stock, and the preview identifies itself as an independent design. [Asset credits](assets/CREDITS.md)

### Enable fresh research and hosting

Copy `.env.example` to `.env` **only on a new checkout**. On the prepared Mac, use the existing project-local `.env`. It is ignored by Git.

| Variable | Purpose |
| --- | --- |
| `GOOGLE_API_KEY` | Gemini Developer API access for the configured Flash model. |
| `EXA_API_KEY` | Exa Search access for web research. |
| `CLOUDFLARE_ACCOUNT_ID`, `CLOUDFLARE_API_TOKEN` | Account-scoped Cloudflare Pages edit permission for publishing, followed by ordinary public HTTP verification. |
| `SITESCOUT_PAGES_PROJECT` | Optional, globally unique Pages project name. Otherwise a short account-derived suffix identifies this project. |

For deployment, install a supported Node.js release (the prepared Mac uses Node 26) and run `npm ci` beside `package-lock.json`. Node is only needed for Wrangler uploads. Cloudflare supplies the shareable `pages.dev` addresses. The desk binds to localhost. Preview links are unlisted with a no-index directive and anyone with a link can open them. An operator can remove deployments from the preview project’s Deployments page in Cloudflare.

```sh
.venv/bin/python pipeline.py --mode showcase --limit 2
.venv/bin/python pipeline.py --mode live --limit 10
```

On macOS, **Daily autopilot** registers a 9:00 AM local launchd job for ten unseen businesses. Keep the Mac signed in and online. Switching it off unregisters the job. One batch lock prevents overlapping work. Results and logs survive a closed dashboard. Daily operation is opt-in. Hosted previews remain available when the Mac is offline.

## Evaluation and reproducibility

On 15 authored synthetic cases with known labels, the deterministic qualifier matched all 15 expected routes. It held eight cases, skipped three, qualified three and blocked one. It labeled none of six known websites missing. Retrieval recovered all 12 authored decisive passages in both arms, using 5,073 characters with FTS versus 10,719 with full context. This benchmark measures regression behavior on visible examples. [Fixture results](evaluation/results.json)

In a two-case paid writing comparison, direct generation took one writing call and branching took two. Branching selected the observed broken estimate link on one case. All four drafts passed the deterministic checks. With identical passages, Gemini sometimes disagreed with itself about the social case. Human pitch preference and sales conversion remain unmeasured. [Paired results](evaluation/paired-pitches.json) · [Initial pilot](evaluation/initial-paired-pilot.json)

| Actual recorded run | Output | Elapsed¹ | Search + inference² |
| --- | --- | ---: | ---: |
| Ordinary discovery, ten actual contractors | 3 healthy-site skips, 7 holds | 6.5 s | $0.1190 |
| Selected live showcase, two actual contractors | 1 reviewed manual-contact draft and verified site, 1 skip | 29.6 s | $0.0231 |
| Frozen rehearsal, three synthetic contractors | 1 reviewed email, 1 manual contact, 1 skip, 2 verified sites | 12.2 s | $0.0026 |

¹ From the first through the last recorded event with a warm registry. Internet latency varies. ² Exa-reported search costs plus inference estimated from provider token usage. A first calibration pass misidentified license directories as owned sites. Strengthening the ownership check changed six records from skip to hold in the ten-record retest. The [recorded run measurements](evaluation/observed-runs.json) contain event times, provider call counts and outcomes. The curated example demonstrates feasibility. The samples are too small to predict daily yield or throughput at 100 businesses per day.

```sh
uv pip install pytest==9.2.0
.venv/bin/python -m pytest -q
.venv/bin/python evaluation.py --fixtures
```

The tests and fixture benchmark need no live service. Optional `--paired-pitches` makes metered Gemini calls and writes a new pilot. `--blind-cards` assembles two local HTML forms from five unique real records after discovery and showcase runs. Complete the routes form before the draft form. Each form downloads a JSON response file. Run `evaluation.py --score-blind PATH_TO_ANSWERS` for each response. The rating key stays in the operator’s private data directory.

Runtime data lives in `.runtime/` (override with `SITESCOUT_DATA_DIR`). Each metered call reserves its worst-case allowance in SQLite before dispatch. This checkout’s cumulative budgets are $3 for Gemini and $6 for Exa. They apply to calls through this application and to its configured data directory. Failed calls with an unknown charge retain the reservation. Replaying a run is always free.
