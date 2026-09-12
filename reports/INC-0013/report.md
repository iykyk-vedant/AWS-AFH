# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0013`
**Title:** We have a critical production issue in our Python service reported by the Finance Team. Customers ar
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 255.8s | **Confidence:** 44%

---

## Root Cause
Line 152 of checkout_service.py applies the discount twice: once in the cart summary calculation and again during checkout processing, due to redundant discount application logic in the `calculate_final_total` function when the 'SAVE20' code is present.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/services/checkout_service.py` | This change ensures the 'SAVE20' discount is only applied if it hasn't already been marked as applied in the cart's discount tracking, preventing duplicate discount application during final total calculation. |

---

## Validation Results

| Metric | Before | After |
|--------|--------|-------|
| Tests Run | 0 | 0 |
| Passed | 0 | 0 |
| Failed | 0 | 0 |

**Verdict:** [NO CHANGE]
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
1. Incident INC-0013 parsed: 'We have a critical production issue in our Python service reported by the Finance Team. Customers ar' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/services/checkout_service.py']
3. Root cause: Line 152 of checkout_service.py applies the discount twice: once in the cart summary calculation and again during checkout processing, due to redundant discount application logic in the `calculate_final_total` function when the 'SAVE20' code is present.
4. Fix applied: Prevent double application of 'SAVE20' discount by ensuring it is only applied once during final total calculation.
5. Validation: NO_CHANGE -- before: 0/0 passed, after: 0/0 passed
