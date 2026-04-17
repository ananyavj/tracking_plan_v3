"""
fix_tracking_plan.py
Run this once from inside the tracking_plan_v3 directory to apply all schema fixes.
Usage:  python fix_tracking_plan.py
It modifies tracking_plan.xlsx in-place and creates a backup first.
"""
import shutil, openpyxl
from copy import copy

# Backup first
shutil.copy("tracking_plan.xlsx", "tracking_plan_backup.xlsx")
print("Backed up to tracking_plan_backup.xlsx")

wb = openpyxl.load_workbook("tracking_plan.xlsx")

def write_row(ws, row_num, values, ref_row=None):
    """Write values to a row, optionally copying style from ref_row."""
    for col, val in enumerate(values, 1):
        cell = ws.cell(row=row_num, column=col)
        cell.value = val
        if ref_row:
            ref = ws.cell(row=ref_row, column=col)
            if ref.has_style:
                cell.font      = copy(ref.font)
                cell.fill      = copy(ref.fill)
                cell.alignment = copy(ref.alignment)
                cell.border    = copy(ref.border)

# ─────────────────────────────────────────────
# 1. BROWSING — add variant_id + sku to Product Viewed
# Current last row of Product Viewed = 34 (discount_pct)
# ─────────────────────────────────────────────
ws = wb["Browsing"]
ws.insert_rows(35)
write_row(ws, 35,
    ["Product Viewed", "variant_id", "Required", "string", "var_998877", None,
     "Shopify variant ID. Matches the variant selected by user.", None],
    ref_row=34)
ws.insert_rows(36)
write_row(ws, 36,
    ["Product Viewed", "sku", "Optional", "string", "SKU-LIB-BLK-M", None,
     "Merchant SKU string. Sent for cross-reference with inventory.", None],
    ref_row=34)
print("Browsing: added variant_id + sku to Product Viewed")

# ─────────────────────────────────────────────
# 2. PURCHASE FUNNEL — multiple fixes
# ─────────────────────────────────────────────
ws = wb["Purchase Funnel"]

# 2a. Fix Product Added orphan row (row 15): add event name in col A
ws["A15"] = "Product Added"
ws["C15"] = "Required"
print("Purchase Funnel: fixed Product Added variant_id orphan (row 15)")

# 2b. Insert sku after row 15
ws.insert_rows(16)
write_row(ws, 16,
    ["Product Added", "sku", "Optional", "string", "SKU-LIB-BLK-M", None,
     "Merchant SKU string.", None],
    ref_row=14)
print("Purchase Funnel: added sku to Product Added (row 16)")

# After insert, find Checkout Started checkout_token orphan dynamically
def find_first_orphan(ws, prop_name):
    for i, row in enumerate(ws.iter_rows(min_row=1, values_only=True), 1):
        if row[0] is None and row[1] == prop_name and row[2] in ("required", "Required"):
            return i
    return None

# 2c. Fix Checkout Started checkout_token orphan
r = find_first_orphan(ws, "checkout_token")
if r:
    block = None
    for i in range(r, 0, -1):
        v = ws.cell(row=i, column=1).value
        if v and "Checkout Started" in str(v) and "Step" not in str(v):
            block = "Checkout Started"
            break
        elif v and "Order Completed" in str(v):
            block = "Order Completed"
            break
    if block == "Checkout Started":
        ws.cell(row=r, column=1).value = "Checkout Started"
        ws.cell(row=r, column=3).value = "Required"
        print(f"Purchase Funnel: fixed Checkout Started checkout_token orphan (row {r})")

# 2d. Add checkout_token to Checkout Step Completed
csc_last = None
in_csc = False
for i, row in enumerate(ws.iter_rows(min_row=1, values_only=True), 1):
    if row[0] and "Checkout Step" in str(row[0]) and "▶" in str(row[0]):
        in_csc = True
    elif in_csc:
        if row[0] and "▶" in str(row[0]):
            csc_last = i - 1
            break
        elif row[0] == "Checkout Step Completed" or (row[0] is None and row[1] is not None):
            csc_last = i
ws.insert_rows(csc_last + 1)
write_row(ws, csc_last + 1,
    ["Checkout Step Completed", "checkout_token", "Optional", "string", "sh_9a8b7c6d", None,
     "Shopify checkout token. Same token as on Checkout Started.", None],
    ref_row=csc_last)
print(f"Purchase Funnel: added checkout_token to Checkout Step Completed")

# 2e. Fix Order Completed orphan rows
for i, row in enumerate(ws.iter_rows(min_row=1, values_only=True), 1):
    if row[0] is None and row[1] in ("checkout_token", "products", "total", "revenue") and row[2] in ("required", "Required"):
        ws.cell(row=i, column=1).value = "Order Completed"
        ws.cell(row=i, column=3).value = "Required"
        print(f"Purchase Funnel: fixed Order Completed orphan '{row[1]}' (row {i})")

# 2f. Remove duplicate total/revenue (old Segment-spec-note rows after products)
oc_props_seen = {}
rows_to_delete = []
in_oc = False
for i, row in enumerate(ws.iter_rows(min_row=1, values_only=True), 1):
    if row[0] and "Order Completed" in str(row[0]) and "▶" in str(row[0]):
        in_oc = True
        oc_props_seen = {}
    elif in_oc:
        if row[0] and "▶" in str(row[0]) and "Order Completed" not in str(row[0]):
            break
        if row[1] in ("total", "revenue"):
            if row[1] in oc_props_seen:
                rows_to_delete.append(i)
            else:
                oc_props_seen[row[1]] = i
for r in sorted(rows_to_delete, reverse=True):
    ws.delete_rows(r)
    print(f"Purchase Funnel: removed duplicate Order Completed row {r}")

# 2g. Add tax to Order Completed
oc_last = None
for i, row in enumerate(ws.iter_rows(min_row=1, values_only=True), 1):
    if row[0] == "Order Completed" and row[1] is not None:
        oc_last = i
ws.insert_rows(oc_last + 1)
write_row(ws, oc_last + 1,
    ["Order Completed", "tax", "Optional", "float", "273.96", None,
     "18% GST on net revenue. Informational — not used in Amplitude revenue calc.", None],
    ref_row=oc_last)
print("Purchase Funnel: added tax to Order Completed")

# ─────────────────────────────────────────────
# Save
# ─────────────────────────────────────────────
wb.save("tracking_plan.xlsx")
print("\n✅ tracking_plan.xlsx updated successfully.")
print("   Delete this script and tracking_plan_backup.xlsx when satisfied.")
