import copy
import io
import json
import tempfile
import unittest
from unittest.mock import patch
from pathlib import Path

import graph_readonly
import jml_lab
import ai_review_note


ROOT = Path(__file__).resolve().parents[1]
DATA = ROOT / "sample_data"


class LifecycleTests(unittest.TestCase):
    def setUp(self):
        self.state = jml_lab.read(DATA / "initial_state.json")
        self.policy = jml_lab.read(DATA / "policy.json")
        self.events = jml_lab.read(DATA / "hr_events.json")
        self.reviewers = jml_lab.read(DATA / "reviewers.json")

    def execute(self, state, event, audit):
        proposal = jml_lab.plan(state, event, self.policy)
        approval = jml_lab.review(proposal, "Demo Approver", "approve", "Checked HR source and target access", self.reviewers)
        return jml_lab.apply(state, event, self.policy, proposal, approval, self.reviewers, "Demo Executor", audit)

    def test_end_to_end_access_transitions(self):
        state, audit = self.execute(self.state, self.events[0], [])
        self.assertEqual(state["directory"]["PERSON-100"]["groups"], ["GRP-BASE", "GRP-FINANCE"])
        self.assertEqual(state["saas"]["PERSON-100"]["roles"], ["App User", "Invoice Viewer"])
        state, audit = self.execute(state, self.events[1], audit)
        self.assertEqual(state["directory"]["PERSON-100"]["groups"], ["GRP-BASE", "GRP-OPERATIONS"])
        self.assertEqual(state["saas"]["PERSON-100"]["roles"], ["App User", "Case Editor"])
        mover_actions = [row["details"]["action"]["operation"] for row in audit if row["kind"] == "simulated_action" and row["details"]["event_id"] == "HR-002"]
        self.assertLess(mover_actions.index("remove_group"), mover_actions.index("add_group"))
        self.assertLess(mover_actions.index("revoke_role"), mover_actions.index("grant_role"))
        state, audit = self.execute(state, self.events[2], audit)
        self.assertFalse(state["directory"]["PERSON-100"]["enabled"])
        self.assertFalse(state["saas"]["PERSON-100"]["active"])
        self.assertEqual(state["directory"]["PERSON-100"]["groups"], [])
        self.assertEqual(state["saas"]["PERSON-100"]["roles"], [])
        self.assertEqual(state["processed_event_ids"], ["HR-001", "HR-002", "HR-003"])
        self.assertEqual(jml_lab.verify_audit(audit), len(audit))

    def test_reject_missing_unauthorized_and_self_approval(self):
        proposal = jml_lab.plan(self.state, self.events[0], self.policy)
        with self.assertRaisesRegex(ValueError, "authorized"):
            jml_lab.review(proposal, "Demo Observer", "approve", "Looks fine", self.reviewers)
        rejected = jml_lab.review(proposal, "Demo Approver", "reject", "Needs HR validation", self.reviewers)
        with self.assertRaisesRegex(ValueError, "Approved review"):
            jml_lab.apply(self.state, self.events[0], self.policy, proposal, rejected, self.reviewers, "Demo Executor", [])
        approved = jml_lab.review(proposal, "Demo Approver", "approve", "Validated", self.reviewers)
        with self.assertRaisesRegex(ValueError, "differ"):
            jml_lab.apply(self.state, self.events[0], self.policy, proposal, approved, self.reviewers, "Demo Approver", [])
        self.assertEqual(self.state["directory"], {})

    def test_stale_plan_tamper_and_replay_rejected(self):
        proposal = jml_lab.plan(self.state, self.events[0], self.policy)
        approval = jml_lab.review(proposal, "Demo Approver", "approve", "Validated", self.reviewers)
        changed = copy.deepcopy(proposal)
        changed["actions"].append({"system": "directory", "operation": "add_group", "person_id": "PERSON-100", "value": "GRP-ADMIN"})
        with self.assertRaisesRegex(ValueError, "mismatch"):
            jml_lab.apply(self.state, self.events[0], self.policy, changed, approval, self.reviewers, "Demo Executor", [])
        state, audit = jml_lab.apply(self.state, self.events[0], self.policy, proposal, approval, self.reviewers, "Demo Executor", [])
        with self.assertRaisesRegex(ValueError, "already processed"):
            jml_lab.plan(state, self.events[0], self.policy)
        self.assertTrue(audit)

    def test_audit_edit_detected(self):
        _, audit = self.execute(self.state, self.events[0], [])
        audit[1]["details"]["action"]["operation"] = "disable"
        with self.assertRaisesRegex(ValueError, "event 2"):
            jml_lab.verify_audit(audit)

    def test_cli_demo_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "demo"
            state, audit = jml_lab.run_demo(DATA, output)
            self.assertEqual(len(state["processed_event_ids"]), 3)
            self.assertTrue((output / "3_leaver_state.json").exists())
            self.assertEqual(len((output / "audit.jsonl").read_text().splitlines()), len(audit))
            with self.assertRaises(FileExistsError):
                jml_lab.run_demo(DATA, output)


class GraphTests(unittest.TestCase):
    def setUp(self):
        self.guid = "11111111-1111-1111-1111-111111111111"
        self.user_id = "22222222-2222-2222-2222-222222222222"
        self.env = {"LAB_TENANT_ID": self.guid, "LAB_TEST_TENANT_ID": self.guid,
                    "LAB_CLIENT_ID": "33333333-3333-3333-3333-333333333333",
                    "LAB_CLIENT_SECRET": "test-only", "LAB_TEST_USER_ID": self.user_id,
                    "LAB_ALLOWED_DOMAIN": "lab.example.com"}

    def test_tenant_allowlist_and_read_only_requests(self):
        changed = dict(self.env, LAB_TEST_TENANT_ID=self.user_id)
        with self.assertRaisesRegex(ValueError, "allowlisted"):
            graph_readonly.config(changed)
        requests = []
        payloads = [{"access_token": "mock-token"}, {"id": self.user_id, "userPrincipalName": "person@lab.example.com", "accountEnabled": True, "department": "Finance"}]

        def opener(req, timeout):
            requests.append(req)
            return io.BytesIO(json.dumps(payloads.pop(0)).encode())

        result = graph_readonly.read_test_user(graph_readonly.config(self.env), opener)
        self.assertEqual(result["department"], "Finance")
        self.assertEqual([req.get_method() for req in requests], ["POST", "GET"])
        self.assertEqual(requests[1].full_url.split("?")[0], f"https://graph.microsoft.com/v1.0/users/{self.user_id}")

    def test_graph_response_domain_guard(self):
        payloads = [{"access_token": "mock-token"}, {"id": self.user_id, "userPrincipalName": "person@other.example.com"}]

        def opener(req, timeout):
            return io.BytesIO(json.dumps(payloads.pop(0)).encode())

        with self.assertRaisesRegex(ValueError, "outside"):
            graph_readonly.read_test_user(graph_readonly.config(self.env), opener)


class AINoteTests(unittest.TestCase):
    def test_advisory_only_note_does_not_change_plan(self):
        proposal = jml_lab.plan(jml_lab.read(DATA / "initial_state.json"), jml_lab.read(DATA / "hr_events.json")[0], jml_lab.read(DATA / "policy.json"))
        original = copy.deepcopy(proposal)
        response = {"status": "completed", "output": [{"type": "message", "content": [{"type": "output_text", "text": json.dumps({"verification_note": "Verify HR event.", "potential_risk": "Wrong department mapping."})}]}]}
        captured = []

        def opener(req, timeout):
            captured.append(json.loads(req.data))
            return io.BytesIO(json.dumps(response).encode())

        with patch.dict("os.environ", {"OPENAI_API_KEY": "mock-key"}):
            note = ai_review_note.note_for_plan(proposal, "test-model", opener)
        self.assertEqual(note["ai_authority"], "advisory_only")
        self.assertEqual(note["plan_hash"], proposal["plan_hash"])
        self.assertEqual(proposal, original)
        self.assertFalse(captured[0]["store"])


if __name__ == "__main__":
    unittest.main()
