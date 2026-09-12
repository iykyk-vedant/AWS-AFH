# Amaze on Work -- Incident Resolution Report

**Incident ID:** `INC-001`
**Title:** Server returns 500 error on POST /api/auth/login after latest deployment
**Service:** `` | **Env:** staging | **Severity:** P1 - Critical
**Resolution Time:** 80.7s | **Confidence:** 36%

---

## Root Cause
The defect originates in app/routes/auth.py at line 42, where bcrypt.checkpw is called with a string instead of bytes for user.password_hash.

---

## Changes Made

| File | Rationale |
|------|-----------|
| `app/routes/auth.py` | bcrypt.checkpw requires both arguments to be bytes; encoding the stored hash converts it from str to bytes, satisfying the API contract and eliminating the TypeError |


**Patch:**
```diff
--- a/app/routes/auth.py
+++ b/app/routes/auth.py
@@ -49,7 +49,7 @@
     # causing a TypeError: a bytes-like object is required, not 'str'
     is_valid = bcrypt.checkpw(
         payload.password.encode("utf-8"),
-        user.password_hash
+        user.password_hash.encode("utf-8")
     )
 
     if not is_valid:

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
1. Incident INC-001 parsed: 'Server returns 500 error on POST /api/auth/login after latest deployment' -- service: , type: security
2. Stack trace analysis -> suspect files: ['app/routes/auth.py']
3. Root cause: The defect originates in app/routes/auth.py at line 42, where bcrypt.checkpw is called with a string instead of bytes for user.password_hash.
4. Fix applied: Fixed TypeError by encoding stored password_hash to bytes before calling bcrypt.checkpw
5. Validation: SKIPPED -- before: 0/0 passed, after: 0/0 passed
