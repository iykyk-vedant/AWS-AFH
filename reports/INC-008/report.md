# Amaze on Work -- Incident Resolution Report (INC-008)

**Incident ID:** `INC-008`
**Title:** ValueError: No scheme supplied in webhook notification target URL
**Service:** `python-service` | **Env:** production | **Severity:** P2 - High
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 96%

---

## Root Cause
Strict urlparse fails to prepend https:// on schemeless target URLs

---

## Changes Made
- Target file: `app/services/webhook_service.py`

```diff
--- a/app/services/webhook_service.py
+++ b/app/services/webhook_service.py
@@ -10,6 +10,8 @@
     """Validate and normalize outbound webhook endpoint URL."""
+    if not target_url.startswith(('http://', 'https://')):
+        target_url = f"https://{target_url}"
     parsed = urlparse(target_url)
     if not parsed.scheme:
         raise ValueError(f"Invalid URL '{target_url}': No scheme supplied. Perhaps you meant https://{target_url}?")
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
