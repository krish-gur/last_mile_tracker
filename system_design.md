# System Design: Last-Mile Delivery Tracker

## 1. Rate Calculation Engine & Zone Detection
The dynamic pricing engine determines delivery costs without hardcoding rates. Upon order input, the pickup and drop-off zones are evaluated to establish intra-zone or inter-zone routing.

Volumetric weight is computed using the standard formula:
$$Volumetric\ Weight = (Length \times Breadth \times Height) + 5000$$

The billable weight is derived using $\max(Actual\ Weight, Volumetric\ Weight)$. The engine queries the `rate_cards` table matching the source zone, destination zone, and order classification (B2B or B2C). The base cost is computed as:
$$Base\ Charge = Billable\ Weight \times Rate\ Per\ Kg$$

If the payment type is Cash on Delivery (COD), the system appends the admin-configured surcharge for that order category. The full price breakdown is displayed for confirmation before order creation.

## 2. Auto-Assignment & Availability Modeling
Agent allocation operates dynamically to minimize transit latency:
1. **Zone Filter:** When auto-assignment is triggered, the engine queries active delivery agents where `is_available = True` and `current_zone_id` matches the order's pickup zone.
2. **Fallback Allocation:** If no local agent is free, it falls back to the nearest available agent globally.
3. **State Locking:** Once assigned, the agent's ID is bound to the order record, and an event is logged in the tracking ledger.

## 3. Order Lifecycle & Immutable History
The lifecycle follows standard progression: `Order Placed` $\rightarrow$ `Picked Up` $\rightarrow$ `In Transit` $\rightarrow$ `Out for Delivery` $\rightarrow$ `Delivered` or `Failed`.

To guarantee auditability, status updates do not overwrite past states. Instead, every transition writes an append-only row to `order_tracking_history` containing the `order_id`, `status`, `changed_by_user_id`, `timestamp`, and operational notes. Read queries retrieve the chronological ledger to present the real-time tracking timeline.

## 4. Failed Delivery & Reschedule Flow
If an agent marks an attempt as `Failed`:
1. The status updates in the primary order record and logs into the tracking ledger.
2. An automated notification alert is dispatched to the customer.
3. The customer accesses the portal to select a new delivery window via the `/orders/{id}/reschedule` endpoint.
4. Upon rescheduling, the order transitions to `Rescheduled`, and the auto-assignment engine immediately selects and binds an available agent for the next attempt.