# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-0041`
**Title:** There is a problem with Discount is being applied twice. repo: <https://github.com/RonakAjwani/shops
**Service:** `python-service` | **Env:** production | **Severity:** HIGH
**Resolution Time:** 94.1s | **Confidence:** 24%

---

## Root Cause
The discount is being applied twice due to duplicate invocation of the apply_discount function in the payment processing pipeline, likely within the order creation flow in python-service/app/services/payment_service.py.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `python-service/app/services/payment_service.py` | Prevents repeated application of the same discount by checking if the discount ID has already been processed, ensuring idempotency in the payment pipeline. |

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
1. Incident INC-0041 parsed: 'There is a problem with Discount is being applied twice. repo: <https://github.com/RonakAjwani/shops' -- service: python-service, type: logical_error
2. Stack trace analysis -> suspect files: ['python-service/app/services/payment_service.py', 'python-service/app/routes/orders.py', 'python-service/app/routes/payments.py', 'python-service/tests/test_payments.py', 'python-service/tests/test_orders.py']
3. Root cause: The discount is being applied twice due to duplicate invocation of the apply_discount function in the payment processing pipeline, likely within the order creation flow in python-service/app/services/payment_service.py.
4. Fix applied: Add idempotency check to prevent duplicate discount application by tracking discount IDs already applied.
5. Validation: FIX_APPLIED_NO_TESTS -- before: 0/1 passed, after: 0/1 passed
