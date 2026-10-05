"""Synthetic joiner, mover, leaver workflow with approval gated simulation."""

from __future__ import annotations

import argparse
import copy
import hashlib
import json
from datetime import datetime, timezone
from pathlib import Path


def canonical(value):
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def digest(value):
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def read(path):
    return json.loads(Path(path).read_text(encoding="utf-8"))


def write(path, value):
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("x", encoding="utf-8") as stream:
        json.dump(value, stream, indent=2, ensure_ascii=False)
        stream.write("\n")


def validate_policy(policy):
    if not isinstance(policy, dict) or not isinstance(policy.get("departments"), dict):
        raise ValueError("Policy requires departments")
    for name, access in policy["departments"].items():
        if not name or not isinstance(access, dict):
            raise ValueError("Invalid department policy")
        for key in ("groups", "roles"):
            values = access.get(key)
            if not isinstance(values, list) or any(not isinstance(x, str) or not x for x in values) or len(values) != len(set(values)):
                raise ValueError(f"Invalid {key} in {name}")
    return policy


def validate_state(state):
    if not isinstance(state, dict) or not all(isinstance(state.get(k), t) for k, t in
            (("directory", dict), ("saas", dict), ("processed_event_ids", list))):
        raise ValueError("Invalid state shape")
    return state


def plan(state, event, policy):
    validate_state(state)
    validate_policy(policy)
    if not isinstance(event, dict) or not all(isinstance(event.get(k), str) and event[k] for k in ("event_id", "person_id", "kind")):
        raise ValueError("Event requires event_id, person_id, and kind")
    if event["kind"] not in {"joiner", "mover", "leaver"}:
        raise ValueError("Unknown event kind")
    if event["event_id"] in state["processed_event_ids"]:
        raise ValueError("Event already processed")
    person = event["person_id"]
    directory = state["directory"].get(person)
    saas = state["saas"].get(person)
    actions = []
    kind = event["kind"]
    if kind == "joiner":
        if directory or saas:
            raise ValueError("Joiner already exists")
        department = event.get("department")
        if department not in policy["departments"]:
            raise ValueError("Unknown department")
        target = policy["departments"][department]
        actions.append({"system": "directory", "operation": "create", "person_id": person, "department": department})
        for group in target["groups"]:
            actions.append({"system": "directory", "operation": "add_group", "person_id": person, "value": group})
        actions.append({"system": "saas", "operation": "create", "person_id": person})
        for role in target["roles"]:
            actions.append({"system": "saas", "operation": "grant_role", "person_id": person, "value": role})
    elif kind == "mover":
        if not directory or not saas or not directory["enabled"] or not saas["active"]:
            raise ValueError("Mover needs an active identity in both systems")
        department = event.get("department")
        if department not in policy["departments"] or department == directory["department"]:
            raise ValueError("Mover needs a different known department")
        target = policy["departments"][department]
        for group in sorted(set(directory["groups"]) - set(target["groups"])):
            actions.append({"system": "directory", "operation": "remove_group", "person_id": person, "value": group})
        for role in sorted(set(saas["roles"]) - set(target["roles"])):
            actions.append({"system": "saas", "operation": "revoke_role", "person_id": person, "value": role})
        actions.append({"system": "directory", "operation": "set_department", "person_id": person, "value": department})
        for group in sorted(set(target["groups"]) - set(directory["groups"])):
            actions.append({"system": "directory", "operation": "add_group", "person_id": person, "value": group})
        for role in sorted(set(target["roles"]) - set(saas["roles"])):
            actions.append({"system": "saas", "operation": "grant_role", "person_id": person, "value": role})
    else:
        if not directory or not saas or not directory["enabled"]:
            raise ValueError("Leaver needs an active directory identity and SaaS record")
        actions.append({"system": "directory", "operation": "disable", "person_id": person})
        actions.append({"system": "saas", "operation": "deactivate", "person_id": person})
        for role in sorted(saas["roles"]):
            actions.append({"system": "saas", "operation": "revoke_role", "person_id": person, "value": role})
        for group in sorted(directory["groups"]):
            actions.append({"system": "directory", "operation": "remove_group", "person_id": person, "value": group})
    content = {"event": event, "source_state_hash": digest(state), "policy_hash": digest(policy), "actions": actions}
    return {**content, "plan_hash": digest(content)}


def review(plan_doc, reviewer, decision, reason, authorized_reviewers):
    if reviewer not in authorized_reviewers or authorized_reviewers[reviewer] != "approver":
        raise ValueError("Reviewer is not an authorized demo approver")
    if decision not in {"approve", "reject"} or not reason.strip():
        raise ValueError("Review needs a decision and reason")
    return {"plan_hash": plan_doc["plan_hash"], "reviewer": reviewer, "decision": decision,
            "reason": reason.strip(), "reviewed_at_utc": datetime.now(timezone.utc).isoformat()}


def _perform(state, action):
    person = action["person_id"]
    operation = action["operation"]
    system = action["system"]
    if system == "directory":
        if operation == "create":
            state["directory"][person] = {"enabled": True, "department": action["department"], "groups": []}
        elif operation == "disable":
            state["directory"][person]["enabled"] = False
        elif operation == "set_department":
            state["directory"][person]["department"] = action["value"]
        elif operation in {"add_group", "remove_group"}:
            groups = state["directory"][person]["groups"]
            if operation == "add_group":
                groups.append(action["value"])
                groups.sort()
            else:
                groups.remove(action["value"])
        else:
            raise ValueError("Unknown directory action")
    elif system == "saas":
        if operation == "create":
            state["saas"][person] = {"active": True, "roles": []}
        elif operation == "deactivate":
            state["saas"][person]["active"] = False
        elif operation in {"grant_role", "revoke_role"}:
            roles = state["saas"][person]["roles"]
            if operation == "grant_role":
                roles.append(action["value"])
                roles.sort()
            else:
                roles.remove(action["value"])
        else:
            raise ValueError("Unknown SaaS action")
    else:
        raise ValueError("Unknown system")


def _audit(events, kind, details):
    previous = events[-1]["event_hash"] if events else "0" * 64
    record = {"sequence": len(events) + 1, "timestamp_utc": datetime.now(timezone.utc).isoformat(),
              "kind": kind, "details": details, "previous_hash": previous}
    record["event_hash"] = digest(record)
    events.append(record)


def apply(state, event, policy, plan_doc, approval, reviewers, executor, audit_events):
    if approval.get("decision") != "approve":
        raise ValueError("Approved review required before changes")
    if approval.get("reviewer") not in reviewers or reviewers[approval["reviewer"]] != "approver":
        raise ValueError("Reviewer is not an authorized demo approver")
    if executor == approval["reviewer"] or not executor:
        raise ValueError("Executor must differ from reviewer")
    if not approval.get("reason") or not approval.get("reviewed_at_utc"):
        raise ValueError("Incomplete review")
    expected = plan(state, event, policy)
    if plan_doc != expected or approval.get("plan_hash") != expected["plan_hash"]:
        raise ValueError("Plan or approval mismatch; replan and rereview")
    updated = copy.deepcopy(state)
    new_events = copy.deepcopy(audit_events)
    _audit(new_events, "approval", {"event_id": event["event_id"], "plan_hash": expected["plan_hash"],
                                    "reviewer": approval["reviewer"], "decision": approval["decision"], "reason": approval["reason"]})
    for action in expected["actions"]:
        _perform(updated, action)
        _audit(new_events, "simulated_action", {"event_id": event["event_id"], "executor": executor,
                                                "plan_hash": expected["plan_hash"], "action": action})
    updated["processed_event_ids"].append(event["event_id"])
    _audit(new_events, "completed", {"event_id": event["event_id"], "state_hash": digest(updated)})
    return updated, new_events


def verify_audit(events):
    if not events:
        raise ValueError("Empty audit")
    previous = "0" * 64
    for index, item in enumerate(events, 1):
        candidate = {k: v for k, v in item.items() if k != "event_hash"}
        if candidate.get("sequence") != index or candidate.get("previous_hash") != previous or digest(candidate) != item.get("event_hash"):
            raise ValueError(f"Audit verification failed at event {index}")
        previous = item["event_hash"]
    return len(events)


def run_demo(input_dir, output_dir):
    source = Path(input_dir)
    output = Path(output_dir)
    if output.exists():
        raise FileExistsError("Demo output already exists; use a new output directory")
    state = validate_state(read(source / "initial_state.json"))
    policy = validate_policy(read(source / "policy.json"))
    events = read(source / "hr_events.json")
    reviewers = read(source / "reviewers.json")
    decisions = read(source / "decisions.json")
    if len(events) != len(decisions):
        raise ValueError("One review decision required for every HR event")
    audit_events = []
    output.mkdir(parents=True)
    for index, (event, decision) in enumerate(zip(events, decisions), 1):
        if decision.get("event_id") != event.get("event_id"):
            raise ValueError("Review order or event ID mismatch")
        planned = plan(state, event, policy)
        approval = review(planned, decision["reviewer"], decision["decision"], decision["reason"], reviewers)
        state, audit_events = apply(state, event, policy, planned, approval, reviewers, "Demo Executor", audit_events)
        write(output / f"{index}_{event['kind']}_plan.json", planned)
        write(output / f"{index}_{event['kind']}_review.json", approval)
        write(output / f"{index}_{event['kind']}_state.json", state)
    write(output / "final_state.json", state)
    with (output / "audit.jsonl").open("x", encoding="utf-8") as stream:
        for item in audit_events:
            stream.write(canonical(item) + "\n")
    verify_audit(audit_events)
    return state, audit_events


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest="command", required=True)
    demo = commands.add_parser("demo", help="Run the approved synthetic HR sequence")
    demo.add_argument("--input", default="sample_data")
    demo.add_argument("--output", default="output/demo")
    verify = commands.add_parser("verify-audit", help="Verify the local audit chain")
    verify.add_argument("--audit", default="output/demo/audit.jsonl")
    args = parser.parse_args()
    try:
        if args.command == "demo":
            state, audit = run_demo(args.input, args.output)
            print(f"Simulated {len(state['processed_event_ids'])} approved HR events and verified {len(audit)} audit events; no external changes")
        else:
            events = [json.loads(line) for line in Path(args.audit).read_text(encoding="utf-8").splitlines()]
            print(f"Verified {verify_audit(events)} audit events")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
