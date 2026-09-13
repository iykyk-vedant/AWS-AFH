# Amaze on Work -- Incident Resolution Report (INC-003)

**Incident ID:** `INC-003`
**Title:** ZeroDivisionError in shipping rate calculation for digital orders
**Service:** `python-service` | **Env:** production | **Severity:** P2 - High
**Verdict:** `FIX_CONFIRMED` | **Confidence:** 94%

---

## Root Cause
Division by zero when order weight_kg is 0.0 for digital items

---

## Changes Made
- Target file: `app/services/shipping_service.py`

```diff
--- a/app/services/shipping_service.py
+++ b/app/services/shipping_service.py
@@ -11,7 +11,7 @@
     if distance_km < 0:
         raise ValueError("Distance cannot be negative")

-    cost_per_kg = 15.0 / weight_kg
+    cost_per_kg = 0.0 if weight_kg <= 0.0 else 15.0 / weight_kg
     weight_charge = round(cost_per_kg * weight_kg, 2)
     distance_charge = round(distance_km * 0.5, 2)
     total_shipping = round(base_fee + weight_charge + distance_charge, 2)
```

---

## Validation Results
- **Verdict:** `FIX_CONFIRMED`
- **Regressions:** 0 detected
