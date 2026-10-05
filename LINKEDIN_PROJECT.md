# LinkedIn project entry

**Project name:** AI Assisted IAM Lifecycle Automation Lab

**Associated with:** Leave employer association empty. This is a personal portfolio lab.

**Description:**

Created a personal IAM lifecycle lab with AI assisted development. Synthetic HR events drive joiner, mover, and leaver changes across a simulated directory and SaaS application. The workflow calculates role changes from policy, requires a named human review tied to the exact plan, and records each simulated action in a verifiable audit log. I also included an optional AI investigation note and a read only Microsoft Graph test tenant check. The Graph request path has been tested with mocked responses but has not been connected to a live tenant. No production identity systems or employer data are used.

**Skills to select:** Identity and Access Management, Python, Identity Governance, Automation, Microsoft Graph. Choose the closest options available in LinkedIn.

**Media:** Add a link to the public GitHub repository after uploading the contents of this folder. The README should appear at the repository root.

## Interview walkthrough

1. Run `python3 jml_lab.py demo` and open `output/demo/1_joiner_plan.json`. Explain how a synthetic HR event becomes a policy based access plan.
2. Compare `2_mover_plan.json` with the joiner state. Point out that old Finance access is removed before Operations access is granted.
3. Open `3_leaver_state.json` to show disabled and deactivated accounts with empty access lists.
4. Show that an approval refers to the exact `plan_hash`. Explain that the local reviewer list is a simulation, not enterprise authentication.
5. Run `python3 jml_lab.py verify-audit` and explain what a hash chain can and cannot prove.
6. Explain the optional AI note boundary and the read only Graph test tenant path. Be clear that live Graph connectivity still needs to be verified in your own test tenant.

## Suggested repository description

Synthetic HR to IAM lifecycle workflow with approved directory and SaaS changes, audit verification, optional AI notes, and a read only Microsoft Graph test path.
