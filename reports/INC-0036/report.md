# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0036`
**Title:** Discount is being applied twice.
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 79.9s | **Confidence:** 38%

---

## Root Cause
The discount is being applied twice because the `apply_discount` function in `python-service/app/services/payment_service.py` is being invoked both during order creation and payment calculation, leading to duplicate discount logic execution when both endpoints are called in sequence.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/services/payment_service.py` | By introducing a tracking mechanism (applied_discounts set), the function becomes idempotent and safely skips re-applying the same discount, preventing double discount application when called in multiple stages. |

---

## Validation Results

| Metric | Before | After |
|--------|--------|-------|
| Tests Run | 1 | 1 |
| Passed | 0 | 0 |
| Failed | 0 | 0 |

**Verdict:** FIX_APPLIED_NO_TESTS
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
1. Incident INC-0036 parsed: 'Discount is being applied twice.' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/services/payment_service.py', 'python-service/tests/test_orders.py', 'python-service/tests/test_payments.py']
3. Root cause: The discount is being applied twice because the `apply_discount` function in `python-service/app/services/payment_service.py` is being invoked both during order creation and payment calculation, leading to duplicate discount logic execution when both endpoints are called in sequence.
4. Fix applied: Add idempotency check to prevent duplicate discount application by tracking if discount was already applied.
5. Validation: FIX_APPLIED_NO_TESTS -- before: 0/1 passed, after: 0/1 passed
