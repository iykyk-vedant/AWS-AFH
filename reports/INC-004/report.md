# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-004`
**Title:** Unhandled KeyError in inventory reservation when item ID is not in catalog
**Service:** `` | **Env:** production | **Severity:** P1 - Critical
**Resolution Time:** 254.1s | **Confidence:** 37%

---

## Root Cause
Direct dictionary access on STOCK_CATALOG without verifying item existence

---

## Changes Made

| File | Rationale |
|------|-----------|
| `app/services/inventory_service.py` | The added existence check returns a controlled error response when the item is absent, avoiding the KeyError that occurs from direct dictionary indexing. |


**Patch:**
```diff
--- a/app/services/inventory_service.py
+++ b/app/services/inventory_service.py
@@ -19,7 +19,8 @@
     if quantity <= 0:
         raise ValueError("Reservation quantity must be positive")
 
-    # BUG (INC-004): Directly indexing dictionary throws unhandled KeyError for unlisted items
+    if item_id not in STOCK_CATALOG:
+        return {"success": False, "reason": "Item not found in catalog", "item_id": item_id}
     available = STOCK_CATALOG[item_id]
 
     if available < quantity:

```

---

## Validation Results

| Metric | Before | After |
|--------|--------|-------|
| Tests Run | 0 | 0 |
| Passed | 0 | 0 |
| Failed | 0 | 0 |

**Verdict:** [SKIPPED]
**Fixes Confirmed:** None
**Regressions:** None

---

## Risk Assessment
**Risk Level:** [MEDIUM]
- Blast radius: N/A downstream callers
- Files changed: 1
- Change size: N/A lines

---

## Reasoning Chain
1. Incident INC-004 parsed: 'Unhandled KeyError in inventory reservation when item ID is not in catalog' -- service: , type: logical_error
2. Stack trace analysis -> suspect files: ['app/services/inventory_service.py']
3. Root cause: Direct dictionary access on STOCK_CATALOG without verifying item existence
4. Fix applied: Added a guard for missing item IDs to prevent an unhandled KeyError in reserve_stock.
5. Validation: SKIPPED -- before: 0/0 passed, after: 0/0 passed
