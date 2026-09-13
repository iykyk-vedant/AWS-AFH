# Amaze on Work -- Incident Resolution Report (INC-006)

**Incident ID:** `INC-006`
**Title:** AttributeError: 'NoneType' object has no attribute 'strip' on user profile update
**Service:** `python-service` | **Env:** production | **Severity:** P2 - High
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 97%

---

## Root Cause
Calling strip() on optional phone parameter when set to None

---

## Changes Made
- Target file: `app/services/user_service.py`

```diff
--- a/app/services/user_service.py
+++ b/app/services/user_service.py
@@ -21,7 +21,7 @@
     if "name" in updates:
         user["name"] = updates["name"].strip()

     if "phone" in updates:
-        user["phone"] = updates["phone"].strip()
+        user["phone"] = updates["phone"].strip() if updates["phone"] is not None else None
     return user
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
