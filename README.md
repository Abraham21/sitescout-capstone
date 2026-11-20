# SiteScout

SiteScout is a research assistant for an independent web designer. It discovers licensed painting businesses in the Los Angeles area, checks whether customers can reach them online, and prepares a website concept and a personalized outreach draft when the evidence supports one. Abraham decides whether to make contact.

**For a first look**, open the operator desk, choose **Start live showcase** on the prepared Mac or **Saved replay** for an existing recording. A fresh clone includes a ten-business synthetic recording with reviewed drafts, three distinct hosted websites and the source observations behind each decision. Playback and the embedded previews work without credentials. The prepared Mac also has a **Featured live run** from real public sources.

The current [Paintastic concept](https://p-0caf0bac7bdc97792e.sitescout-previews-ec8f1c9e.pages.dev) uses six photographs from its matched Burbank listing. The [fictional Linen Leaf example](https://p-e45c8e9ca20f3128e3.sitescout-previews-ec8f1c9e.pages.dev) demonstrates service selection and a testimonial carousel. On the prepared Mac, open the [ten-business rehearsal](http://127.0.0.1:8501/?replay=3513730a56a0) to see businesses take different paths.

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
| Featured live run | Opens a completed real run with a verified hosted preview. Select **Replay saved run** for its recorded timeline, or **Start live showcase** for fresh research. |
| Live discovery | Selects unseen actual licenses, searches the web, and inspects current public pages. A batch can legitimately end entirely in holds and skips. |
| Curated live showcase | Starts with three preselected registry IDs. The researcher finds current sources, images and contact evidence through the ordinary discovery workflow. Outcomes can change. This is the quickest way to show fresh research and the full workflow on the prepared Mac. |
| Demo fixtures | Uses frozen synthetic search and page snapshots with deterministic qualification. With model and hosting configured, it writes, reviews and publishes anew. Without hosting, eligible concepts remain available in the local Preview tab. |
| Saved replay | Plays stored timestamps in order. Move the event slider to pause and inspect. Playback incurs no inference or publication cost. |

The batch board shows every discovered business, its current step, and its final route. Up to three workers process leads at once. Choose **Live discovery** for ten unseen contractors or **Curated live showcase** for the three preselected licenses. In **Saved replay**, each row advances as its recorded events arrive. During a run or replay the row marked **Shown below** identifies the business whose handoffs are open. The latest-events log includes the whole batch. After completion, **Inspect business** lets you choose which record to open. Open the researcher’s source, jump to the resulting section of the verified site, read the writer’s opening, and inspect the reviewer’s checks. Recorded playback reveals each result at the event that produced it. A held candidate may have a directory link and repeated availability checks to verify manually. Below the handoffs, **Evidence** shows sources and the qualification decision. **Website preview** opens the saved layout or verified public URL. **Agent trace** shows tool events, feedback and repair attempts. In **Outreach draft**, the operator can edit and copy a reviewed message. A public phone or social profile routes the lead to **Manual contact**. Dismissed businesses are excluded from future discovery. SiteScout has no email delivery integration.

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
| [business_profile.py](business_profile.py) | Reads gallery photos, logos and attributed review text from matched business sources. Checks image type and size before embedding. |
| [studio.py](studio.py) | `build_preview` fills the painter template. `write_pitch` compares grounded openings. `review` rebuilds the HTML, verifies the claims and asks Gemini for a separate judgment. |
| [assets/painter.html](assets/painter.html) | The painter layouts display the business phone, verified services, available photos and sourced testimonials. Service cards lead to the estimate form and preselect the project type when the business offers multiple verified specialties. **Send request** demonstrates how the owner could activate inquiries. Contact details stay in the browser. The [painter skill](.agents/skills/painter-preview/SKILL.md) explains which facts and images may appear. |
| [services.py](services.py), [publication.py](publication.py) | The Gemini and Exa calls, followed by direct upload to Cloudflare Pages and a check of the HTML actually served. |
| [storage.py](storage.py), [autopilot.py](autopilot.py) | SQLite stores events, results, spending reservations and operator edits. macOS launchd schedules one batch per day when enabled. |
| [app.py](app.py), [evaluation.py](evaluation.py) | The Streamlit operator desk and the reproducible evaluation commands. |

### How a lead earns a preview

1. Discovery filters active, unexpired C-33 licenses in Glendale, Burbank, Pasadena and Los Angeles. The four cities alternate in a batch. Only business fields used for research are retained. The filtered registry is cached for a day. [CSLB data portal](https://www.cslb.ca.gov/OnlineServices/DataPortal/)
2. The qualifier searches by identity, cross-checks by phone or license as needed, and opens plausible first-party sites. A third-party license mirror never counts as the owner’s site. A verified site with a working published phone can pass the contact check. Repeated missing-page responses from an estimate link can establish a specific defect.
3. A social or directory presence can establish an opportunity when independent identity-matched sources support a real contact gap and two searches fail to identify an owned estimate site. When evidence is thin, a third search checks for a business profile with an outbound website or portfolio link. An unavailable linked domain gets a second check. The record keeps the listing, host observations and any mismatch for manual verification. Weak evidence goes on hold. Instruction-like source text can block the case.
4. **RAG** uses SQLite full-text search over a versioned evidence set for one business. Name, locality, contact and service queries retrieve passages. Gemini may extract explicitly cited services or flag a contradiction. A deterministic check requires the extracted wording to occur in the cited text.
5. The writer uses bounded **tree-of-thought-style search** when it has competing observations. It compares possible opening angles, shortlists up to two, and compares the resulting pitches. The trace shows inspectable alternatives and evidence IDs. Gemini Flash is the sole cloud model to keep the implementation small. The course project has too few independently labeled examples to justify fine-tuning; local inference is a future comparison.
6. The reviewer validates the actual preview and the draft. A failed review can send the lead back through the builder and writer twice. Cloudflare receives only the approved static preview. A content fingerprint must match at the public address before a draft enters the contact queue. The researcher shortlists up to six project photos from a verified company site or a matched listing. Its existing Gemini qualification request includes a numbered contact sheet. Gemini chooses the cover and describes unlabeled images. Original source references travel with the photos. If a visual choice is unavailable, the builder uses the size- and crop-ranked images. For a Yahoo Local gallery, it checks the business name, city, phone and original gallery URLs. Attributed review quotes stay linked to their source. The template omits testimonials when no named review can be verified. Local or licensed design photography carries its own credit. The page identifies itself as an independent concept. [Asset credits](assets/CREDITS.md)

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
.venv/bin/python pipeline.py --mode showcase --limit 3
.venv/bin/python pipeline.py --mode live --limit 10
```

On macOS, **Daily autopilot** registers a 9:00 AM local launchd job for ten unseen businesses. Keep the Mac signed in and online. Switching it off unregisters the job. One batch lock prevents overlapping work. Results and logs survive a closed dashboard. Daily operation is opt-in. Hosted previews remain available when the Mac is offline.

## Evaluation and reproducibility

One operator independently judged the first release on five selected real businesses. Website ownership agreed on **5/5** and the proposed action on **4/5**. The one draft needed editing. A directory link supplied by the operator exposed a follow-up the original search missed. That input and a request for more useful sites drove the redesign. In a later walkthrough, the operator accepted the message and would withhold the morning website. The split headline, lack of on-page project photos and copy-only estimate interaction informed the next revision. [Operator evaluation](evaluation/operator-review.json)

On 15 authored synthetic cases with known labels, the deterministic qualifier matched all 15 expected routes. It held eight cases, skipped three, qualified three and blocked one. It labeled none of six known websites missing. Retrieval recovered all 12 authored decisive passages in both arms, using 5,439 characters with FTS versus 11,668 with full context. This benchmark measures regression behavior on visible examples. [Fixture results](evaluation/results.json)

In the revised two-case paid writing comparison, both approaches selected the same opening in both cases. Direct writing took one call per case (2.51–2.60 seconds). The branching approach took two calls (4.04–5.18 seconds). The ten-call study, including the qualification comparison, cost $0.006831. The small study does not establish a preference for branching. [Paired results](evaluation/paired-pitches.json) · [Initial pilot](evaluation/initial-paired-pilot.json)

| Morning recorded run | Output | Elapsed¹ | Search + inference² |
| --- | --- | ---: | ---: |
| Ordinary discovery, ten unseen actual contractors | 1 healthy-site skip, 9 holds | 15.1 s | $0.1680 |
| Curated live showcase, three actual contractors | 1 reviewed manual-contact draft with a verified site, 1 hold with a directory-linked domain to check, 1 skip | 16.4 s | $0.0445 |
| Frozen rehearsal, three synthetic contractors | 1 reviewed email, 1 manual contact, 1 skip, 2 verified sites | 17.9 s | $0.0029 |

¹ From the first through the last recorded event with a warm registry. Internet latency varies. ² Exa-reported search costs plus inference estimated from provider token usage. A first calibration pass misidentified license directories as owned sites. Strengthening the ownership check changed six records from skip to hold in the ten-record retest. The [recorded run measurements](evaluation/observed-runs.json) contain event times, provider call counts and outcomes. The curated example demonstrates feasibility. The samples are too small to predict daily yield or throughput at 100 businesses per day.

```sh
uv pip install pytest==9.2.0
.venv/bin/python -m pytest -q
.venv/bin/python evaluation.py --fixtures
```

The latest three-license showcase started from registry IDs alone. It independently found Paintastic’s verified gallery, selected a cover, described six photos, published the reviewed concept and routed the draft to manual contact. The other two businesses went to hold and skip. The latest ordinary batch held nine unseen contractors and skipped one. The ten-business synthetic rehearsal produced three verified sites. Browser checks cover gallery navigation, responsive headlines and a local estimate-form submission. [Browser and media checks](evaluation/preview-checks.json)

The tests and fixture benchmark need no live service. Optional `--paired-pitches` makes metered Gemini calls and writes a new pilot. `--blind-cards` assembles two local HTML forms from five unique real records after discovery and showcase runs. Complete the routes form before the draft form. Each form downloads a JSON response file. Run `evaluation.py --score-blind PATH_TO_ANSWERS` for each response. The rating key stays in the operator’s private data directory.

Runtime data lives in `.runtime/` (override with `SITESCOUT_DATA_DIR`). Each metered call reserves its worst-case allowance in SQLite before dispatch. This checkout’s cumulative budgets are $3 for Gemini and $6 for Exa. They apply to calls through this application and to its configured data directory. Failed calls with an unknown charge retain the reservation. Replaying a run is always free.

## Capstone handoff

The morning submission drafts describe the earlier measured run. The [final report](submission/SiteScout%20Report.pdf) follows the course template. The [editable presentation](submission/SiteScout%20Presentation.pptx) contains speaker notes. The [recording guide](submission/SiteScout%20Recording%20Guide.docx) includes an 8:55 technical walkthrough and an optional 90-second pitch. After recording, paste the shareable video link into the [Canvas submission document](submission/SiteScout%20Canvas%20Submission.docx). The [evaluation guide](evaluation/README.md) explains the independent human review.
