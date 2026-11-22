"""Exercise the operator's edit, replay and dismiss actions using an isolated database."""

import json
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
            self.assertEqual(app.segmented_control(key="view").value, "Contact queue")
            self.assertFalse(storage.contact_queue())
            app.segmented_control(key="view").set_value("Runs").run()
            self.assertIn("Recorded sample (synthetic)", app.selectbox(key="saved_run").options[0])
            self.assertTrue(any('class="ss-draft"' in h.value for h in app.get("html")))
            self.assertFalse(app.text_input)
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

            app.segmented_control(key="view").set_value("How it works").run()
            self.assertFalse(app.exception)
            tools = next(h.value for h in app.get("html") if 'class="ss-technologies"' in h.value)
            self.assertIn("LangGraph", tools)
            self.assertIn("Cloudflare Pages", tools)
            self.assertEqual(tools.count("<dt>"), tools.count("data:image/svg+xml;base64,"))

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
            self.assertEqual([r["id"] for r in storage.contact_queue()], ["test"])
            app.query_params["contact"] = "test"
            app.run()
            app.text_input[0].set_value("Reviewed subject")
            app.button(key="edit_test-run_test").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(storage.leads("test-run")[0]["pitch"]["subject"], "Reviewed subject")
            app.query_params.clear()
            app.query_params.update({"replay": "test-run", "business": "test"})
            app.run()
            self.assertFalse(app.text_input)
            self.assertIn(
                "First draft", next(h.value for h in app.get("html") if 'class="ss-draft"' in h.value)
            )
            self.assertTrue(
                any(
                    "Copy draft" in f.proto.srcdoc
                    for f in app.get("iframe")
                    if f.proto.HasField("srcdoc")
                )
            )
            self.assertFalse(any(b.label == "Dismiss lead" for b in app.button))
            app.query_params.clear()
            app.query_params["contact"] = "test"
            app.run()
            app.button(key="dismiss_test-run_test").click().run()
            self.assertEqual(storage.leads("test-run")[0]["status"], "ready")
            self.assertEqual([row["id"] for row in storage.contact_queue("dismissed")], ["test"])
            app.segmented_control(key="queue_group").set_value("dismissed").run()
            app.query_params.update({"contact": "test", "group": "dismissed"})
            app.run()
            app.button(key="restore_test-run_test").click().run()
            self.assertFalse(storage.dismissed_ids())
            self.assertEqual([r["id"] for r in storage.contact_queue()], ["test"])

    def test_real_contact_queue_uses_latest_evidence_and_all_confirmed_channels(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"SITESCOUT_DATA_DIR": folder}),
        ):
            storage.initialize()
            lead = {
                "id": "abc",
                "name": "Matched Painter",
                "city": "Glendale",
                "status": "ready",
                "channel": "Email",
                "preview": {"url": "https://preview.example/site", "published": True},
                "pitch": {"subject": "Preview", "body": "Your preview"},
                "qualification": {
                    "email": "paint@example.test",
                    "social": "https://www.instagram.com/test_painter/",
                    "phone": "8185551234",
                },
            }
            for mode in ["showcase", "demo", "recheck"]:
                storage.new_run(mode, mode)
                storage.save_lead(mode, lead)
                storage.finish_run(mode, "complete")
            self.assertEqual([r["run_id"] for r in storage.contact_queue()], ["recheck"])
            app = AppTest.from_file(str(Path(__file__).resolve().parents[1] / "app.py"))
            app.run()
            card = json.loads(app.get("bidi_component")[0].proto.json)
            channels = ("mailto:paint@example.test", "instagram.com/test_painter/", "tel:8185551234")
            self.assertTrue(all(channel in card for channel in channels))
            self.assertIn("?contact=abc&amp;group=ready", card)
            app.query_params["contact"] = "abc"
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.get("bidi_component")[0].key, "queue_links")
            self.assertTrue(any(all(channel in h.value for channel in channels) for h in app.get("html")))
            app.query_params.clear()
            app.query_params["group"] = "ready"
            app.run()
            self.assertEqual(app.session_state.opened_location, {"group": "ready"})
            self.assertFalse(app.text_input)
            app.query_params["contact"] = "abc"
            app.run()
            app.button(key="contacted_recheck_abc").click().run()
            self.assertEqual(storage.contact_queue(), [])
            self.assertEqual([r["run_id"] for r in storage.contact_queue("contacted")], ["recheck"])
            storage.new_run("later", "live")
            storage.save_lead("later", {**lead, "status": "hold"})
            storage.finish_run("later", "complete")
            app.query_params.update({"contact": "abc", "group": "contacted"})
            app.run()
            self.assertEqual(app.segmented_control(key="queue_group").value, "contacted")
            self.assertFalse(app.text_input)
            self.assertTrue(storage.contact_action("abc")["changed_at"])
            app.button(key="restore_recheck_abc").click().run()
            self.assertFalse(app.exception)
            self.assertEqual(storage.contact_queue("contacted"), [])
            self.assertEqual(storage.contact_queue(), [])  # Latest research placed this business on hold.

    def test_prior_dismissal_migrates_and_can_be_restored(self):
        with tempfile.TemporaryDirectory() as folder, patch.dict("os.environ", {"SITESCOUT_DATA_DIR": folder}):
            storage.initialize()
            storage.new_run("old", "live")
            storage.save_lead("old", {"id": "123", "name": "Legacy Painter", "status": "dismissed",
                                      "original_status": "manual", "pitch": {"body": "Hello"},
                                      "preview": {"url": "https://example.test/site", "published": True}})
            storage.finish_run("old", "complete")
            with storage.connect() as db:
                db.execute("DROP TABLE contact_actions")
                db.execute("PRAGMA user_version=0")
            storage.initialize()
            self.assertEqual(storage.dismissed_ids(), {"123"})
            self.assertEqual(storage.contact_queue("dismissed")[0]["run_id"], "old")
            storage.set_contact_action("old", "123", "restore")
            storage.initialize()
            self.assertEqual([row["id"] for row in storage.contact_queue()], ["123"])
            self.assertEqual(storage.leads("old")[0]["status"], "manual")

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
            app.query_params.update({"replay": "audit", "at": "6"})
            app.run()
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

    def test_showcase_button_dispatches_one_fresh_run(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict("os.environ", {"SITESCOUT_DATA_DIR": folder}),
            patch("pipeline.showcase_available", return_value=True),
            patch(
                "pipeline.run_batch",
                side_effect=lambda **kw: kw["on_event"](
                    {"stage": "Discover", "status": "started", "run_id": "featured"}
                ),
            ) as runner,
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
            app.segmented_control(key="view").set_value("Runs").run()
            next(b for b in app.button if "Start live showcase" in b.label).click().run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="saved_run").value, "featured")
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
            app.query_params["at"] = str(len(events))
            app.run()
            self.assertFalse(app.exception)
            self.assertEqual(app.selectbox(key="saved_run").value, run_id)

            def board():
                component = next(c for c in app.get("bidi_component")
                                 if c.proto.component_name == "sitescout_business_links")
                markup = json.loads(component.proto.json)
                return BeautifulSoup(markup, "html.parser")

            self.assertEqual(board().find(role="status").get_text(), "10 of 10 finished · 0 ready to contact")
            businesses = app.selectbox(key=f"inspect_{run_id}")
            other = next(e for e in events if e.get("lead_id") and e["lead_id"] != businesses.value)
            businesses.set_value(other["lead_id"]).run()
            self.assertEqual(app.session_state.opened_location["business"], other["lead_id"])
            self.assertIn(other["lead_name"], board().select_one(".ss-following").parent.get_text())
            name = events[first_started - 1]["lead_name"]
            app.slider(key=f"cursor_{run_id}").set_value(discovered).run()
            self.assertEqual(len(board().select("tbody tr")), 10)
            self.assertEqual({td.text for td in board().select("tbody td:last-child")}, {"Waiting"})
            self.assertFalse(board().select(".done"))
            self.assertTrue(
                all("at=" in a["href"] and "business=" in a["href"] for a in board().select("a"))
            )
            self.assertIsNone(board().select_one(".ss-following"))
            app.slider(key=f"cursor_{run_id}").set_value(first_started).run()
            self.assertEqual(len(board().select(".ss-status.active")), 1)
            self.assertIn("9 waiting", board().find(role="status").get_text())
            self.assertIn(name, board().select_one(".ss-following").parent.get_text())
            self.assertTrue(
                any(name in h.value for h in app.get("html") if 'id="business-detail"' in h.value)
            )
            target = next(
                e["lead_id"]
                for e in events
                if e.get("lead_id") and e["lead_id"] != events[first_started - 1]["lead_id"]
            )
            app.query_params.update({"at": str(first_started), "business": target})
            app.run()
            self.assertEqual(app.selectbox(key=f"follow_{run_id}").value, target)
            self.assertFalse(app.text_area)
            app.segmented_control(key="view").set_value("Contact queue").run()
            self.assertEqual(app.session_state.opened_location, {})
