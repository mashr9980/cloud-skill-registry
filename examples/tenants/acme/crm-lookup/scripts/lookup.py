"""Look up an account by email.

Demo data stands in for the CRM API call so this runs with no network. The
debug line below is the classic credential leak: it prints the key to output
that an agent framework would feed straight into the model's context. The
sandbox scrubs it before the model sees it.
"""
import json
import os
import sys

ACCOUNTS = {
    "jane@northwind.com": {"plan": "Annual Pro", "owner": "Sam Lee", "renews": "2027-03-01"},
}

key = os.environ.get("CRM_API_KEY", "")
print(f"debug: calling CRM with key={key}", file=sys.stderr)
email = sys.argv[1].lower() if len(sys.argv) > 1 else ""
print(json.dumps(ACCOUNTS.get(email, {"not_found": email})))
