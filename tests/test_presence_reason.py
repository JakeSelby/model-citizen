# SPDX-License-Identifier: MIT
"""The presence dialog's reason line: who asks, what will happen, and why, in plain words.

"<Model Citizen | Model Citizen Studio> wants to <action>, so <purpose>.", within
`presence.REASON_LIMIT`, naming the Studio only when the request came through it.

Run: python3 -m unittest tests.test_presence_reason
"""
import argparse
import unittest
from pathlib import Path
from unittest import mock

from test_harness import harness

from harness_core import presence
from harness_core.studio import server

CHECKED = {"suite_id": "live-replay", "target_kind": "branch", "target_ref": "main",
           "case_count": 3, "estimate_usd": None, "max_budget_usd": "4.00",
           "spend_cap_usd": "10.00", "pricing_source": "api_credit"}


class Reason(unittest.TestCase):
    def test_the_line_names_the_requester_the_action_and_the_purpose(self):
        reason = presence.ask(presence.STUDIO_NAME, "apply draft d", "your edits take effect")
        self.assertEqual(presence.sentence(*reason),
                         "Model Citizen Studio wants to apply draft d, so your edits take effect.")
        # The helper's reason is a verb phrase: macOS titles its dialog with the app's name.
        self.assertEqual(presence.phrase(reason.action, reason.purpose),
                         "apply draft d, so your edits take effect")
        self.assertEqual(presence.requester(False), "Model Citizen")
        self.assertEqual(presence.requester(True), "Model Citizen Studio")

    def test_a_long_action_is_shortened_and_the_purpose_still_shows(self):
        text = presence.sentence(presence.CLI_NAME, "x" * 2000, "the purpose survives")
        self.assertEqual(len(text), presence.REASON_LIMIT)
        self.assertTrue(text.startswith("Model Citizen wants to xxx"))
        self.assertTrue(text.endswith("..., so the purpose survives."))
        text = presence.phrase("x" * 2000, "the purpose survives")
        self.assertEqual(len(text), presence.REASON_LIMIT)
        self.assertTrue(text.endswith("..., so the purpose survives"))

    def test_a_purpose_with_a_percent_sign_is_kept_verbatim(self):
        self.assertIn("so 100% of it runs.", presence.sentence(presence.CLI_NAME, "a",
                                                               "100% of it runs"))

    def test_a_spend_names_the_cap_suite_cases_targets_estimate_and_cap(self):
        text = presence.sentence(*presence.spend_reason(CHECKED))
        self.assertEqual(text, "Model Citizen wants to spend up to $4.00 on a paid live-replay run "
                               "of 3 case(s) against branch main (estimated unknown, no history; "
                               "spend cap $10.00, api_credit pricing), so it can run the "
                               "live-replay suite you previewed.")
        self.assertLessEqual(len(text), presence.REASON_LIMIT)

    def test_the_studio_route_names_the_studio_and_the_cli_face_does_not(self):
        self.assertEqual(server._requester(mock.Mock(spec=[])), "Model Citizen Studio")
        self.assertEqual(server._requester(mock.Mock(face="Studio")), "Model Citizen Studio")
        self.assertEqual(server._requester(mock.Mock(face="citizen")), "Model Citizen")

    def test_a_replay_with_a_draft_target_says_it_measures_that_draft(self):
        handler = mock.Mock(spec=["server"])
        handler.server.mutations.call.side_effect = lambda work: work()
        admission = mock.Mock(spec=["supervisor", "repository"])
        admission.supervisor.check_start.return_value = CHECKED
        launch = {"suite_id": "live-replay", "parameters": {}, "target_kind": "branch",
                  "target_ref": "main", "confirmed": "t", "max_budget_usd": "4.00",
                  "spend_cap_usd": "10.00", "pricing_source": "api_credit",
                  "case_identities": ["a", "b", "c"],
                  "targets": [{"kind": "branch", "ref": "main"},
                              {"kind": "draft", "ref": "tune-stances"}]}
        with mock.patch.object(server.replay, "launch_payload", return_value=launch):
            text = presence.sentence(*server._replay_spend(handler, admission, {}, "t"))
        self.assertTrue(text.startswith("Model Citizen Studio wants to spend up to $4.00"), text)
        self.assertIn("against branch main vs draft tune-stances", text)
        self.assertTrue(text.endswith("so it can measure whether draft tune-stances helped."))


class Refusal(unittest.TestCase):
    """A refusal says what did not happen, from the action, not the dialog's sentence."""

    def test_the_refusal_names_what_was_not_done(self):
        reason = presence.ask(presence.CLI_NAME, "apply draft tune-stances (revision a1b2c3) to "
                              "your live harness configuration", "your edits take effect")
        with mock.patch.object(presence, "unavailable", return_value=None):
            text = presence.refusal(reason)
        self.assertTrue(text.startswith(
            "Model Citizen did not apply draft tune-stances (revision a1b2c3) to your live harness "
            "configuration: it needs a person's confirmation at this Mac (Touch ID or the login "
            "password), and none was given: the presence check was declined, cancelled or "
            "failed. "), text)
        self.assertNotIn("wants to", text)


class DraftPreflight(unittest.TestCase):
    """`citizen draft apply|rollback|recover` name the Studio only when it ran them."""

    def args(self, **values):
        base = {"action": "apply", "name": "tune-stances", "revision": "a1b2c3",
                "via_studio": False, "abandon": False, "draft": None}
        base.update(values)
        return argparse.Namespace(**base)

    def preflight(self, args):
        with mock.patch.object(harness.studio.drafts, "read_snapshot", return_value="a1b2c3"), \
                mock.patch.object(harness.studio_apply, "unfinished_applies", return_value=[]):
            return harness._draft_preflight(args, Path("/nonexistent"))

    def test_an_apply_through_the_studio_names_the_studio(self):
        early, reason = self.preflight(self.args(via_studio=True))
        self.assertIsNone(early)
        self.assertEqual(presence.sentence(*reason), "Model Citizen Studio wants to apply draft tune-stances (revision "
                                 "a1b2c3) to your live harness configuration, so your edits take "
                                 "effect.")

    def test_an_apply_from_the_cli_names_model_citizen(self):
        self.assertEqual(self.preflight(self.args())[1].requester, "Model Citizen")

    def test_a_rollback_names_what_it_restores(self):
        preview = {"apply": {"draft": "tune-stances", "revision": "a1b2c3"}, "refusals": [],
                   "config": [1, 2], "files": [1]}
        with mock.patch.object(harness.studio_rollback, "preview", return_value=preview):
            early, reason = harness._draft_preflight(
                self.args(action="rollback", apply_id="c" * 32, via_studio=True), Path("/x"))
        self.assertIsNone(early)
        self.assertEqual(presence.sentence(*reason), "Model Citizen Studio wants to roll back apply %s of draft "
                                 "tune-stances (revision a1b2c3), restoring 2 setting(s) and 1 "
                                 "file(s), so your live configuration returns to how it was before "
                                 "that apply." % ("c" * 32))


if __name__ == "__main__":
    unittest.main()
