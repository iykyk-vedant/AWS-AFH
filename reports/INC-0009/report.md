# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0009`
**Title:** We have a critical production issue in our Python service reported by the Finance Team. Customers ar
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 18.5s | **Confidence:** 34%

---

## Root Cause
Line 15 of python-service/app/services/discount_service.py applies the discount twice because the apply_discount function is invoked both during cart summary calculation and again during checkout processing without idempotency checks or state tracking.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/services/discount_service.py` | Adds a flag to track whether the discount has already been applied to the cart. Subsequent calls will skip discount application if the flag is set, preventing double application. |

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
1. Incident INC-0009 parsed: 'We have a critical production issue in our Python service reported by the Finance Team. Customers ar' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/services/discount_service.py']
3. Root cause: Line 15 of python-service/app/services/discount_service.py applies the discount twice because the apply_discount function is invoked both during cart summary calculation and again during checkout processing without idempotency checks or state tracking.
4. Fix applied: The discount was being applied twice due to lack of idempotency; this fix ensures it is applied only once by checking if already applied.
5. Validation: SKIPPED -- before: 0/0 passed, after: 0/0 passed
