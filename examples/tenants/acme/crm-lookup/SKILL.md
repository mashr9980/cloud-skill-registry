---
name: crm-lookup
description: Look up an Acme customer account in the CRM by email and return plan, account owner and renewal date. Read-only. Use when a sales rep asks who owns an account, when it renews, or what plan a customer is on.
metadata:
  secrets: CRM_API_KEY
  network: egress
---
# CRM lookup

Run `scripts/lookup.py <email>`. Report plan, owner and renewal date. If the script returns `not_found`, say so; never guess an owner.
