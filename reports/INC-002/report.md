# Amaze on Work -- Incident Resolution Report (INC-002)

**Incident ID:** `INC-002`
**Title:** Double discount deduction applied during checkout calculation
**Service:** `python-service` | **Env:** production | **Severity:** P1 - Critical
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 96%

---

## Root Cause
Discount was subtracted twice from subtotal in process_order_total

---

## Changes Made
- Target file: `app/services/payment_service.py`

```diff
--- a/app/services/payment_service.py
+++ b/app/services/payment_service.py
@@ -23,7 +23,7 @@
     """Calculate the final total including discount and tax."""
     discount = calculate_discount(subtotal, discount_code)
     # BUG (INC-002): Discount is mistakenly subtracted twice from subtotal
-    discounted_subtotal = max(0.0, (subtotal - discount) - discount)
+    discounted_subtotal = max(0.0, subtotal - discount)
     tax = round(discounted_subtotal * tax_rate, 2)
     total = round(discounted_subtotal + tax, 2)
     return {
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
