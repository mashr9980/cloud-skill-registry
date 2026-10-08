"""Profile a CSV on stdin: rows, and per column type, blanks, min, max."""
import csv
import json
import sys

rows = list(csv.DictReader(sys.stdin))
columns = {}
for name in (rows[0].keys() if rows else []):
    values = [r[name] for r in rows]
    filled = [v for v in values if v not in ("", None)]
    numbers = []
    for v in filled:
        try:
            numbers.append(float(v))
        except ValueError:
            pass
    numeric = bool(filled) and len(numbers) == len(filled)
    columns[name] = {
        "type": "number" if numeric else "text",
        "blank": len(values) - len(filled),
        "min": min(numbers) if numeric else (min(filled) if filled else None),
        "max": max(numbers) if numeric else (max(filled) if filled else None),
        "text_in_number_column": (len(filled) - len(numbers)) if numbers and not numeric else 0,
    }
print(json.dumps({"rows": len(rows), "columns": columns}))
