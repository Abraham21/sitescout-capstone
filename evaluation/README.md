# Reproduce and interpret the evaluation

Run these commands from the repository root with the project virtual environment.

- `.venv/bin/python evaluation.py --fixtures` writes `evaluation/results.json`. It runs the same 15 authored source snapshots with SQLite FTS5 and the full passage set. A gold-passage hit needs both the expected source URL and the annotated evidence text. All cases contribute to route and website-status accuracy, including abstentions. This is a regression suite used during development.
- `.venv/bin/python evaluation.py --paired-pitches` writes `evaluation/paired-pitches.json` and makes metered Gemini calls. Two predeclared synthetic businesses get paired qualification runs, then a separate comparison of one-pass and two-depth writing with the same frozen eligible inputs. This pilot caps its conservative call reservations at $0.30. The earlier end-to-end pilot lives in `initial-paired-pilot.json`, including the case where the model held a lead and the writer did not run. The writing checks measure supported facts, features, and URLs. Human preference is a separate measurement.
- `.venv/bin/python evaluation.py --blind-cards` creates a private packet in `.runtime/blind-review/` once the local database contains five actual businesses with completed evidence gathering. It reuses an existing frozen packet so two reviewers can label identical cases. The sample spans available routes from ordinary discovery and preselected real showcase runs. It supports a small challenge test of system decisions.

## Human review

1. Open `.runtime/blind-review/01-routes.html`. Check the sources, choose the website status and next action independently for each business, then export your answers.
2. Open `.runtime/blind-review/02-drafts.html`. Inspect the saved drafts and available previews, record readiness, and export this second file. Keep the private key file closed until labeling is complete.
3. Score the two exported files separately. Substitute their actual download paths.

```sh
.venv/bin/python evaluation.py --score-blind ~/Downloads/sitescout-routes-answers.json
.venv/bin/python evaluation.py --score-blind ~/Downloads/sitescout-drafts-answers.json
```

The first command prints system–human agreement with a numerator and denominator for each decision. The second prints the counts of ready, edit, withhold, or insufficient-evidence ratings. A second reviewer can complete the original blank forms independently. Keep real evidence packets, ratings, and identities in private storage. The checked-in results contain synthetic cases only.
