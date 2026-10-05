"""Optional Microsoft Graph read only check for one allowlisted test tenant user.

This module never writes to Graph and is separate from the synthetic simulator.
"""

import argparse
import json
import os
import re
from urllib import error, parse, request


GUID = re.compile(r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$")


def config(environ=None):
    env = environ if environ is not None else os.environ
    keys = ("LAB_TENANT_ID", "LAB_TEST_TENANT_ID", "LAB_CLIENT_ID", "LAB_CLIENT_SECRET", "LAB_TEST_USER_ID", "LAB_ALLOWED_DOMAIN")
    values = {key: env.get(key, "") for key in keys}
    if any(not values[key] for key in keys):
        raise ValueError("Set all LAB_ variables described in README.md")
    if not all(GUID.fullmatch(values[key]) for key in ("LAB_TENANT_ID", "LAB_TEST_TENANT_ID", "LAB_CLIENT_ID", "LAB_TEST_USER_ID")):
        raise ValueError("Tenant, app, and test user IDs must be GUIDs")
    if values["LAB_TENANT_ID"].lower() != values["LAB_TEST_TENANT_ID"].lower():
        raise ValueError("Tenant does not match the explicitly allowlisted test tenant")
    domain = values["LAB_ALLOWED_DOMAIN"].lower().strip()
    if not re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", domain):
        raise ValueError("Invalid allowed test domain")
    values["LAB_ALLOWED_DOMAIN"] = domain
    return values


def read_test_user(values, opener=request.urlopen):
    tenant = values["LAB_TENANT_ID"]
    token_url = f"https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    form = parse.urlencode({"client_id": values["LAB_CLIENT_ID"], "client_secret": values["LAB_CLIENT_SECRET"],
                            "scope": "https://graph.microsoft.com/.default", "grant_type": "client_credentials"}).encode()
    token_request = request.Request(token_url, data=form, headers={"Content-Type": "application/x-www-form-urlencoded"}, method="POST")
    try:
        with opener(token_request, timeout=20) as response:
            token = json.load(response)["access_token"]
        user_id = values["LAB_TEST_USER_ID"]
        user_url = f"https://graph.microsoft.com/v1.0/users/{user_id}?{parse.urlencode({'$select': 'id,userPrincipalName,accountEnabled,department'})}"
        user_request = request.Request(user_url, headers={"Authorization": f"Bearer {token}"}, method="GET")
        with opener(user_request, timeout=20) as response:
            user = json.load(response)
    except (error.HTTPError, error.URLError) as exc:
        raise RuntimeError("Graph check failed; inspect test tenant permissions and configuration") from exc
    if str(user.get("id", "")).lower() != user_id.lower():
        raise ValueError("Graph returned a different user ID")
    if not str(user.get("userPrincipalName", "")).lower().endswith("@" + values["LAB_ALLOWED_DOMAIN"]):
        raise ValueError("User is outside the allowlisted test domain")
    return {key: user.get(key) for key in ("id", "userPrincipalName", "accountEnabled", "department")}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.parse_args()
    try:
        result = read_test_user(config())
        print(json.dumps(result, indent=2))
        print("Read only Graph check succeeded. No identity changes were made.")
    except (ValueError, KeyError, RuntimeError) as exc:
        parser.exit(2, f"Error: {exc}\n")


if __name__ == "__main__":
    main()
