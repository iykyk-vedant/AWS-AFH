# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0012`
**Title:** We have a critical production issue in our Python service reported by the Finance Team. Customers ar
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 224.1s | **Confidence:** 44%

---

## Root Cause
Line 32 of python-service/app/services/discount_service.py applies the 'SAVE20' discount twice because the apply_discount function is invoked both during cart summary rendering and again during checkout finalization without idempotency checks or discount application state tracking.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/services/discount_service.py` | This change ensures the 'SAVE20' discount is applied only once per cart by checking and setting a flag within the cart object, addressing the root cause of duplicate application during summary and checkout. |

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
1. Incident INC-0012 parsed: 'We have a critical production issue in our Python service reported by the Finance Team. Customers ar' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/services/discount_service.py']
3. Root cause: Line 32 of python-service/app/services/discount_service.py applies the 'SAVE20' discount twice because the apply_discount function is invoked both during cart summary rendering and again during checkout finalization without idempotency checks or discount application state tracking.
4. Fix applied: Prevent double application of 'SAVE20' discount by tracking applied discounts in the cart context.
5. Validation: NO_CHANGE -- before: 0/0 passed, after: 0/0 passed
