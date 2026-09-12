# Amaze on Work -- Incident Resolution Report (INC-011)

**Incident ID:** `INC-011`
**Title:** ValueError: Cart subtotal mismatch on floating-point precision sum
**Service:** `python-service` | **Env:** production | **Severity:** P2 - High
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 99%

---

## Root Cause
Strict float equality check fails on IEEE-754 binary floating-point representation

---

## Changes Made
- Target file: `app/services/cart_service.py`

```diff
--- a/app/services/cart_service.py
+++ b/app/services/cart_service.py
@@ -10,6 +10,6 @@
     computed_sum = sum(item_prices)

-    if computed_sum != reported_subtotal:
+    if round(computed_sum, 2) != round(reported_subtotal, 2):
         raise ValueError(
             f"Cart subtotal mismatch: computed sum {computed_sum} does not match reported subtotal {reported_subtotal}"
         )
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
