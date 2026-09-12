# Amaze on Work -- Incident Resolution Report (INC-001)

**Incident ID:** `INC-001`
**Title:** Server returns 500 error on POST /api/auth/login after latest deployment
**Service:** `python-service` | **Env:** production | **Severity:** P1 - Critical
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 95%

---

## Root Cause
bcrypt.checkpw requires bytes but user.password_hash is a string

---

## Changes Made
- Target file: `app/routes/auth.py`

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
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
