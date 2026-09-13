# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-004`
**Title:** Unhandled KeyError in inventory reservation when item ID is not in catalog
**Service:** `` | **Env:** production | **Severity:** P1 - Critical
**Resolution Time:** 57.1s | **Confidence:** 37%

---

## Root Cause
By verifying the presence of the requested item in STOCK_CATALOG before accessing it, the function now handles unknown item IDs gracefully, eliminating the crash and providing a clear error payload.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `app/services/inventory_service.py` | Checks for the presence of item_id in STOCK_CATALOG before indexing; if absent, returns a structured error instead of raising KeyError. |


**Patch:**
```diff
--- a/app/services/inventory_service.py
+++ b/app/services/inventory_service.py
@@ -20,6 +20,8 @@
         raise ValueError("Reservation quantity must be positive")
 
     # BUG (INC-004): Directly indexing dictionary throws unhandled KeyError for unlisted items
+    if item_id not in STOCK_CATALOG:
+        return {"success": False, "reason": "Item not found", "item_id": item_id, "available": 0}
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
3. Root cause: By verifying the presence of the requested item in STOCK_CATALOG before accessing it, the function now handles unknown item IDs gracefully, eliminating the crash and providing a clear error payload.
4. Fix applied: Unhandled KeyError when reserving stock for unknown item IDs; added existence check and error response.
5. Validation: SKIPPED -- before: 0/0 passed, after: 0/0 passed
