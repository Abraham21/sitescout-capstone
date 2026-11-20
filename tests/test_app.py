"""Exercise the operator's edit, replay and dismiss actions using an isolated database."""

import tempfile
import unittest
from html import escape
from pathlib import Path
from unittest.mock import patch

from streamlit.testing.v1 import AppTest

import storage


class OperatorDeskTest(unittest.TestCase):
    def test_keyless_clone_opens_recorded_sample_read_only(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                "os.environ",
                {
                    "SITESCOUT_DATA_DIR": folder,
                    "PYTHON_DOTENV_DISABLED": "1",
                    "EXA_API_KEY": "",
                    "GOOGLE_API_KEY": "",
                    "CLOUDFLARE_API_TOKEN": "",
                },
            ),
        ):
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            self.assertFalse(app.exception)
            self.assertEqual(app.radio(key="data_source").value, "replay")
            self.assertIn("Recorded sample (synthetic)", app.selectbox(key="saved_run").options[0])
            self.assertTrue(app.text_input[0].disabled)
            self.assertEqual([r["mode"] for r in storage.list_runs()], ["sample"])
            run_id = storage.list_runs()[0]["id"]
            ready = next(item for item in storage.leads(run_id) if item["status"] == "ready")
            event = next(
                e
                for e in storage.events(run_id)
                if e["stage"] == "Qualify"
                and e.get("handoff", {}).get("source_url")
                and e["lead_id"] == ready["id"]
            )
            url = event["handoff"]["source_url"]
            excerpt = next(e["text"] for e in ready["qualification"]["evidence"] if e["url"] == url)
            chain = next(h.value for h in app.get("html") if 'class="ss-chain"' in h.value)
            self.assertIn("Read saved source excerpt", chain)
            self.assertIn(escape(excerpt), chain)
            self.assertNotIn(f'href="{url}"', chain)
            for note in (h.value for h in app.get("html") if 'class="ss-note"' in h.value):
                self.assertNotIn("<a ", note)
            events = storage.events(run_id)
            first_source_event = next(
                i for i, e in enumerate(events, 1) if e.get("handoff", {}).get("source_url")
            )
            app.slider(key=f"cursor_{run_id}").set_value(first_source_event).run()
            chain = next(h.value for h in app.get("html") if 'class="ss-chain"' in h.value)
            self.assertIn("Synthetic source snapshot", chain)
            self.assertNotIn("href=", chain)
            self.assertNotIn("Read saved source excerpt", chain)

    def test_reviewed_draft_survives_rerun_and_replay_is_read_only(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"SITESCOUT_DATA_DIR": folder, "PYTHON_DOTENV_DISABLED": "1"}),
        ):
            storage.initialize()
            storage.new_run("test-run", "live")
            storage.save_lead(
                "test-run",
                {
                    "id": "test",
                    "name": "Sample painter",
                    "city": "Glendale",
                    "status": "ready",
                    "qualification": {},
                    "channel": "Email",
                    "pitch": {"subject": "First draft", "body": "Concept https://example.test/preview"},
                    "preview": {"url": "https://example.test/preview", "published": True},
                },
            )
            storage.finish_run("test-run", "complete")
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            app.text_input[0].set_value("Reviewed subject")
            app.button(key="edit_test-run_test").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(storage.leads("test-run")[0]["pitch"]["subject"], "Reviewed subject")
            app.radio(key="data_source").set_value("replay").run()
            self.assertTrue(app.text_input[0].disabled)
            self.assertEqual(app.text_input[0].value, "First draft")
            self.assertFalse(any(b.label == "Dismiss lead" for b in app.button))
            app.radio(key="data_source").set_value("live").run()
            app.button(key="dismiss_test-run_test").click().run()
            self.assertEqual(storage.leads("test-run")[0]["status"], "dismissed")

    def test_recorded_handoffs_show_only_what_was_known_at_the_cursor(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"SITESCOUT_DATA_DIR": folder}),
        ):
            storage.initialize()
            storage.new_run("audit", "showcase")
            lead = {
                "id": "test",
                "name": "Sample Painter",
                "city": "Pasadena",
                "status": "manual",
                "pitch": {"subject": "Subject", "body": "The complete final draft"},
                "preview": {"url": "https://example.test/published", "published": True},
            }
            storage.save_lead("audit", lead)
            steps = [
                (
                    "Qualify",
                    {"text": "Matching directory record", "source_url": "https://example.test/source"},
                ),
                ("Build", {"text": "Visible phone and estimate brief", "section": "#estimate"}),
                ("Write", {"text": "A specific draft opening"}),
                ("Review", {"text": "The cited facts passed"}),
                ("Publish", {"url": "https://example.test/published"}),
                ("Queue", {}),
            ]
            for stage, handoff in steps:
                storage.add_event(
                    {
                        "run_id": "audit",
                        "lead_id": "test",
                        "lead_name": "Sample Painter",
                        "stage": stage,
                        "status": "completed",
                        "message": "Completed.",
                        "timestamp": "2026-11-20T10:30:00+00:00",
                        "handoff": handoff,
                    }
                )
            storage.finish_run("audit", "complete")
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            app.radio(key="data_source").set_value("replay").run()
            cursor = app.slider(key="cursor_audit")
            cursor.set_value(2).run()
            chain = next(h.value for h in app.get("html") if 'class="ss-chain"' in h.value)
            self.assertIn('href="https://example.test/source"', chain)
            self.assertIn("Visible phone and estimate brief", chain)
            self.assertNotIn("https://example.test/published", chain)
            self.assertNotIn("A specific draft opening", chain)
            self.assertFalse(app.text_area)
            app.slider(key="cursor_audit").set_value(5).run()
            chain = next(h.value for h in app.get("html") if 'class="ss-chain"' in h.value)
            self.assertIn('href="https://example.test/published#estimate"', chain)
            self.assertIn("A specific draft opening", chain)
            self.assertNotIn("The complete final draft", chain)

    def test_featured_live_start_dispatches_one_fresh_showcase(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"SITESCOUT_DATA_DIR": folder}),
            patch("pipeline.showcase_available", return_value=True),
            patch("pipeline.run_batch", return_value="featured") as runner,
        ):
            storage.initialize()
            storage.new_run("featured", "showcase")
            storage.save_lead(
                "featured",
                {
                    "id": "p",
                    "name": "Sample Painter",
                    "status": "manual",
                    "preview": {"published": True, "url": "https://example.test/site"},
                },
            )
            storage.finish_run("featured", "complete")
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py")).run()
            next(b for b in app.button if "Start live showcase" in b.label).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.radio(key="data_source").value, "showcase")
            self.assertEqual(runner.call_count, 1)
            self.assertEqual(runner.call_args.kwargs["mode"], "showcase")
            app.run()
            self.assertEqual(runner.call_count, 1)

    def test_replay_board_tracks_all_ten_from_discovery_without_future_results(self):
        from bs4 import BeautifulSoup
        import pipeline

        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(
                "os.environ",
                {
                    "SITESCOUT_DATA_DIR": folder,
                    "PYTHON_DOTENV_DISABLED": "1",
                    "GOOGLE_API_KEY": "",
                    "EXA_API_KEY": "",
                    "CLOUDFLARE_API_TOKEN": "",
                },
            ),
        ):
            run_id = pipeline.run_batch(limit=10, mode="demo")
            events = storage.events(run_id)
            discovered = next(
                i
                for i, e in enumerate(events, 1)
                if e["stage"] == "Discover" and e["status"] == "completed"
            )
            first_started = next(i for i, e in enumerate(events, 1) if e.get("lead_id"))
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            app.query_params["replay"] = run_id
            with patch("time.sleep"):
                app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="saved_run").value, run_id)

            def board():
                markup = next(
                    h.value for h in app.get("html") if '<section class="ss-batch"' in h.value
                )
                return BeautifulSoup(markup, "html.parser")

            self.assertEqual(board().find(role="status").get_text(), "10 of 10 finished")
            app.slider(key=f"cursor_{run_id}").set_value(discovered).run()
            self.assertEqual(len(board().select("tbody tr")), 10)
            self.assertEqual({td.text for td in board().select("tbody td:last-child")}, {"Waiting"})
            self.assertFalse(board().select("a, .done"))
            app.slider(key=f"cursor_{run_id}").set_value(first_started).run()
            self.assertEqual(len(board().select(".ss-status.active")), 1)
            self.assertIn("9 waiting", board().find(role="status").get_text())
