# Amaze on Work -- Incident Resolution Report (INC-010)

**Incident ID:** `INC-010`
**Title:** KeyError: 'x-forwarded-for' when extracting client IP in VPC rate limiter
**Service:** `python-service` | **Env:** production | **Severity:** P2 - High
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 98%

---

## Root Cause
Direct header dictionary indexing throws KeyError when proxy header is absent

---

## Changes Made
- Target file: `app/services/rate_limiter.py`

```diff
--- a/app/services/rate_limiter.py
+++ b/app/services/rate_limiter.py
@@ -8,6 +8,8 @@
 def extract_client_ip(headers: Dict[str, str]) -> str:
     """Extract client IP address from HTTP request headers."""
-    forwarded_for = headers["x-forwarded-for"]
+    forwarded_for = headers.get("x-forwarded-for")
+    if not forwarded_for:
+        return "127.0.0.1"
     client_ip = forwarded_for.split(",")[0].strip()
     return client_ip
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
