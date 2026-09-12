# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0010`
**Title:** We have a critical production issue in our Python service reported by the Finance Team. Customers ar
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 21.1s | **Confidence:** 36%

---

## Root Cause
Line 15 of python-service/app/cart.py applies the discount twice because the apply_discount function is invoked both during cart summary calculation and again during checkout processing, leading to a compounding 20% reduction on an already-discounted amount.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/cart.py` | This change ensures the discount is applied only once by introducing a sentinel attribute to track application state. Subsequent calls will skip the calculation. |

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
1. Incident INC-0010 parsed: 'We have a critical production issue in our Python service reported by the Finance Team. Customers ar' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/cart.py']
3. Root cause: Line 15 of python-service/app/cart.py applies the discount twice because the apply_discount function is invoked both during cart summary calculation and again during checkout processing, leading to a compounding 20% reduction on an already-discounted amount.
4. Fix applied: The discount was being applied twice due to lack of state tracking; the fix adds a check to prevent reapplication if already discounted.
5. Validation: SKIPPED -- before: 0/0 passed, after: 0/0 passed
