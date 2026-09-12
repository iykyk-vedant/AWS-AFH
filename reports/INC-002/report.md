# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-002`
**Title:** Double discount deduction applied during checkout calculation

Customers are being undercharged duri
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 169.4s | **Confidence:** 36%

---

## Root Cause
The root cause of the double discount deduction is in the process_order_total function in app/services/payment_service.py, specifically at line 26.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `app/services/payment_service.py` | Replacing the erroneous expression with a single subtraction ensures the discount is applied only once, correcting the total calculation. |


**Patch:**
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
1. Incident INC-002 parsed: 'Double discount deduction applied during checkout calculation

Customers are being undercharged duri' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['app/services/payment_service.py']
3. Root cause: The root cause of the double discount deduction is in the process_order_total function in app/services/payment_service.py, specifically at line 26.
4. Fix applied: Fixes double discount deduction by applying the discount only once to the subtotal.
5. Validation: SKIPPED -- before: 0/0 passed, after: 0/0 passed
