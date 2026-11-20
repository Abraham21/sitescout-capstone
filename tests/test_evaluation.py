import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import evaluation
import storage


class EvaluationTest(unittest.TestCase):
    def test_retrieval_hit_needs_the_gold_passage_at_the_gold_url(self):
        case = {
            "expected": {"evidence_url": "https://example.test", "evidence_contains": "broken estimate"}
        }
        q = {
            "status": "hold",
            "website_status": "unsure",
            "retrieved_ids": ["a", "b"],
            "evidence": [
                {"id": "a", "url": "https://example.test", "text": "Unrelated paragraph"},
                {"id": "b", "url": "https://different.test", "text": "Broken estimate"},
                {"id": "c", "url": "https://example.test", "text": "Broken estimate"},
            ],
        }
        row = evaluation.retrieval_row(case, q)
        self.assertTrue(row["expected_url_in_selection"])
        self.assertFalse(row["expected_passage_in_selection"])
        self.assertEqual(evaluation.count_rate([None, False, True]), {"matched": 1, "total": 2})

    def test_blind_form_shows_source_but_hides_qualifier_assessments(self):
        verdict = {
            "id": "e1",
            "url": "https://example.com",
            "kind": "assessment",
            "text": "System conclusion",
        }
        self.assertNotIn("System conclusion", evaluation.source_block(verdict, blind=True))
        self.assertIn("https://example.com", evaluation.source_block(verdict, blind=True))
        self.assertIn("System conclusion", evaluation.source_block(verdict))

    def test_private_packet_excludes_superseded_runs_and_does_not_show_decisions(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"SITESCOUT_DATA_DIR": folder}),
        ):
            storage.initialize()
            for i in range(6):
                run = f"r{i}"
                storage.new_run(run, "showcase" if i == 1 else "live")
                storage.save_lead(
                    run,
                    {
                        "id": str(i),
                        "name": "Lead " + str(i),
                        "city": "Test City",
                        "status": "ready",
                        "qualification": {
                            "status": "qualified" if i == 1 else "hold",
                            "website_status": "has_site",
                            "reason": "PRIVATE MODEL VERDICT",
                            "evidence": [
                                {"id": "a", "text": "Public observation", "url": "https://example.test"}
                            ],
                        },
                        "pitch": {"subject": "Sample", "body": "Private draft for phase two"}
                        if i == 1
                        else None,
                        "preview": {"url": "https://example.test", "published": True},
                    },
                )
                storage.finish_run(run, "superseded" if i == 0 else "complete")
            packet = evaluation.blind_cards()
            key = json.loads((packet / "key.json").read_text())
            classification = (packet / "01-routes.html").read_text()
            self.assertEqual(len(key["system_outputs"]), 5)
            self.assertNotIn("Lead 0", classification)
            self.assertNotIn("PRIVATE MODEL VERDICT", classification)
            self.assertNotIn("Private draft for phase two", classification)
            self.assertIn("Private draft for phase two", (packet / "02-drafts.html").read_text())
            self.assertIn("Public observation", classification)
            answers = Path(folder) / "answers.json"
            answers.write_text(
                json.dumps(
                    {
                        "packet": key["packet"],
                        "phase": "routes",
                        "reviewer": "Independent",
                        "answers": [
                            {"case": code, "route": r["route"], "website_status": r["website_status"]}
                            for code, r in key["system_outputs"].items()
                        ],
                    }
                )
            )
            self.assertEqual(
                evaluation.score_blind(answers)["system_human_agreement"]["route"],
                {"matched": 5, "total": 5},
            )
            self.assertEqual(evaluation.blind_cards(), packet)

    def test_selected_runs_keep_separate_reproducible_packets_and_matching_score_keys(self):
        with (
            tempfile.TemporaryDirectory() as folder,
            patch.dict(os.environ, {"SITESCOUT_DATA_DIR": folder}),
        ):
            storage.initialize()
            for run in ("original", "revised"):
                storage.new_run(run, "live")
                for index in range(5):
                    storage.save_lead(
                        run,
                        {
                            "id": str(index),
                            "name": f"Business {index}",
                            "status": "skip",
                            "qualification": {
                                "status": "skip",
                                "website_status": "has_site",
                                "evidence": [{"text": "Public observation."}],
                            },
                        },
                    )
                storage.finish_run(run, "complete")
                if run == "original":
                    first = evaluation.blind_cards()
                    original_key = (first / "key.json").read_bytes()
            archive = evaluation.blind_cards(["revised"])
            self.assertEqual(archive.parent, first)
            self.assertEqual((first / "key.json").read_bytes(), original_key)
            self.assertEqual(evaluation.blind_cards(["revised"]), archive)
            first_id = json.loads(original_key)["packet"]
            self.assertNotEqual(first_id, archive.name)
            self.assertIn(
                f"sitescout-routes-answers-{archive.name}.json",
                (archive / "01-routes.html").read_text(),
            )
            for packet_id in (first_id, archive.name):
                answers = Path(folder) / "drafts.json"
                answers.write_text(
                    json.dumps({"packet": packet_id, "phase": "drafts", "reviewer": "A", "answers": []})
                )
                self.assertEqual(evaluation.score_blind(answers)["draft_readiness"], {})
            with self.assertRaisesRegex(ValueError, "missing"):
                evaluation.blind_cards(["revised", "missing"])
