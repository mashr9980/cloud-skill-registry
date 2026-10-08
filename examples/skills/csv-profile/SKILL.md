---
name: csv-profile
description: Profile a CSV export - row count, column types, blank cells and min/max per column. Use when the user wants to understand or sanity-check a CSV or spreadsheet export before analysis, or asks which columns have gaps.
---
# CSV profile

1. Pass the CSV to `scripts/profile.py` on stdin. The first row must be the header.
2. Report the row count, then one line per column: type, blank count, min and max.
3. Point out columns that are more than 20% blank, and number columns that hold text values.
