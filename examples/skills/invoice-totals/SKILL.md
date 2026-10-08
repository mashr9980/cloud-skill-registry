---
name: invoice-totals
description: Add up invoice line items from CSV rows and apply a tax rate. Use when the user wants an invoice subtotal, tax amount or total, or wants to check that a bill adds up, even if they only paste item rows with quantities and prices.
---
# Invoice totals

1. Get the line items as CSV with the header `item,qty,unit_price`. If the user pasted rows without a header, add it.
2. Run `scripts/total.py` with the CSV on stdin and the tax rate as the only argument, as a decimal (8% is `0.08`). No tax mentioned means `0`.
3. Report subtotal, tax and total from the script output. If `bad_rows` is not empty, list those rows and say they were left out.

Money is rounded half-up to cents once, on the final figures. If the user asks why a total is off by a cent, read `references/rounding.md`.
