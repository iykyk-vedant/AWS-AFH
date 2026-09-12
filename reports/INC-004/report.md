# Amaze on Work -- Incident Resolution Report (INC-004)

**Incident ID:** `INC-004`
**Title:** Unhandled KeyError in inventory reservation when item ID is not in catalog
**Service:** `python-service` | **Env:** production | **Severity:** P1 - Critical
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 98%

---

## Root Cause
Direct dictionary access on STOCK_CATALOG without verifying item existence

---

## Changes Made
- Target file: `app/services/inventory_service.py`

```diff
--- a/app/services/inventory_service.py
+++ b/app/services/inventory_service.py
@@ -20,6 +20,8 @@
     if quantity <= 0:
         raise ValueError("Reservation quantity must be positive")

+    if item_id not in STOCK_CATALOG:
+        return {"success": False, "reason": "Item not found in catalog", "item_id": item_id}
     available = STOCK_CATALOG[item_id]
     if available < quantity:
         return {"success": False, "reason": "Insufficient stock", "available": available}
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
