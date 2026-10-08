"""Sum invoice lines from CSV on stdin. Usage: total.py <tax_rate>"""
import csv
import json
import sys
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation

CENT = Decimal("0.01")
rate = Decimal(sys.argv[1]) if len(sys.argv) > 1 else Decimal("0")
subtotal, bad_rows, lines = Decimal("0"), [], 0
for row in csv.DictReader(sys.stdin):
    try:
        subtotal += Decimal(row["qty"]) * Decimal(row["unit_price"])
        lines += 1
    except (InvalidOperation, KeyError, TypeError):
        bad_rows.append(row)
tax = subtotal * rate
print(json.dumps({
    "lines": lines,
    "subtotal": str(subtotal.quantize(CENT, ROUND_HALF_UP)),
    "tax": str(tax.quantize(CENT, ROUND_HALF_UP)),
    "total": str((subtotal + tax).quantize(CENT, ROUND_HALF_UP)),
    "bad_rows": bad_rows,
}))
