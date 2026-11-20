"""Exercise the operator's edit, replay and dismiss actions using an isolated database."""

import tempfile
import unittest
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
