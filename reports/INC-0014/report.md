# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0014`
**Title:** We have a critical production issue in our Python service reported by the Finance Team. Customers ar
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 249.3s | **Confidence:** 44%

---

## Root Cause
Line 32 of python-service/app/services/discount_service.py applies the discount twice because the apply_discount function is invoked both during cart summary calculation and again during checkout processing without idempotency checks or state tracking.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/services/discount_service.py` | This change ensures the discount is applied only once by checking a state flag before modification, preventing duplicate invocation during cart summary and checkout. |

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
1. Incident INC-0014 parsed: 'We have a critical production issue in our Python service reported by the Finance Team. Customers ar' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/services/discount_service.py']
3. Root cause: Line 32 of python-service/app/services/discount_service.py applies the discount twice because the apply_discount function is invoked both during cart summary calculation and again during checkout processing without idempotency checks or state tracking.
4. Fix applied: Prevent double discount application by introducing a state flag to ensure idempotency during cart processing.
5. Validation: NO_CHANGE -- before: 0/0 passed, after: 0/0 passed
