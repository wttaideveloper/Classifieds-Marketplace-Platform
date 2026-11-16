# Mobile order actions

Interactive Swagger: `/docs` or `/api/docs` on the API host after deployment. The adjacent
`cart-patient-actions.openapi.json` is an offline OpenAPI document for the affected routes.
All paths below have the prefix `/api/v1`. Use the existing authenticated session.

| Actor | Method and route | Body / result |
| --- | --- | --- |
| Patient | GET /cart/orders/{order_id} | Own order and current action flags; another patient's order returns 404. |
| Patient | POST /cart/orders/{order_id}/cancel | Before shipment. No body needed for an unpaid order. A paid order needs a reason and store approval. Returns 200 with updated order detail. |
| Patient | POST /refunds | Use the payment ID from order detail. Returns 201 with the refund request; approval and money movement are separate. |
| Admin | POST /cart/orders/{order_id}/cancel/decision | Approve or reject the cancellation. Rejection requires a reason. |
| Admin | PATCH /cart/orders/{order_id}/status | Fulfillment transition. Shipping requires carrier and tracking number. |
| Admin | POST /cart/orders/{order_id}/refund/approve | Decide the single pending refund. If ambiguous, decide by refund ID instead. |
| Admin | POST /refunds/{refund_id}/decision | Approve or reject a particular refund. |

Enterprise Admins manage their tenant's orders; Super Admins can manage across tenants. Patients receive 403 on admin actions.

### Paid cancellation, before shipment

Patient sends `{"reason":"Ordered the wrong size"}`. The response status is
`cancellation_requested`. Stock stays reserved. Admin sends `{"action":"approve"}`
or `{"action":"reject","reason":"Already dispatched"}` to the cancellation decision endpoint.
Approval releases eligible stock once. If the payment still needs to be refunded,
order detail reports `refund_due: true`; cancellation approval does not transfer money.

### Refund after delivery

Example **fields from** an eligible GET response:

```json
{
  "status": "delivered",
  "can_cancel": false,
  "cancel_mode": null,
  "cancel_blocked_reason": "This order was delivered. You can ask for a refund within 7 days of delivery",
  "can_refund": true,
  "refund_blocked_reason": null,
  "payment_id": "3f2a0b6e-0c1d-4a53-9a8e-6a4f3d6f9b10",
  "refund_id": null,
  "refund_status": null,
  "refund_amount": null,
  "refund_due": false
}
```

Patient POST /refunds:

```json
{"payment_id":"3f2a0b6e-0c1d-4a53-9a8e-6a4f3d6f9b10","reason":"Wrong item delivered"}
```

Omit amount for the remaining refundable balance. A partial refund can include
`"amount":"250.00"`. Re-fetch order detail after a mutation. Blocked actions return
409; malformed data or a missing required reason returns 422. Seven days are
measured from the recorded delivery timestamp. For a historical order without
that timestamp, contact support. A monetary refund alone does not prove that
delivered items were physically returned; it does not restock them.

### Release

Apply the Alembic migrations before deploying the application. Historical order
lines default to zero stock debit; cancelling an old order cannot inflate stock.

## Enterprise Admin: orders list, detail, cancellation requests

The same `/api/v1/cart/orders` routes serve the Enterprise Admin; the caller's role decides what they return.

| Call | Customer | Enterprise Admin (tenant owner) | Super Admin |
|---|---|---|---|
| `GET /cart/orders` | their own orders (`id, user_id, status, total, currency, item_count, created_at`) | **every order of their tenant**, newest first, in the staff shape below | every tenant (`tenant_id` narrows it) |
| `GET /cart/orders/{id}` | their own order | any order of their tenant | any order |
| `GET /cart/orders/cancellation-requests` | 403 | orders in `cancellation_requested`, oldest request first | any tenant |

An admin who also shops uses `GET /cart/orders?mine=true` for their own purchases. An order that is not yours and not your tenant's is a 404 (never another tenant's data).

Staff list item: `id, status, payment_status, total, currency, created_at, customer {user_id, name, phone}, item_count, items_preview [first 3 product names], carrier, tracking_number,
cancellation_requested_at, city`. Filters: `status` (comma list), `q` (customer name or phone, tracking number, order id prefix, product name), `enterprise_id` (orders that contain that
enterprise's products), `date_from`, `date_to`, `page`, `page_size`; wrapped as `{ items, total, page, page_size }`.

Order detail now also carries what the customer's tracking screen needs: `carrier`, `tracking_number`, `tracking_url`, `shipped_at`, `delivered_at`, `payment_status`, `cancellation`
(`requested_at`, `reason`, `decision_reason`, `cancelled_at`, `cancelled_by_role`), `history` (`action`, `from_status`, `to_status`, `actor_role`, `reason`, `at`), `refund_currency`,
`refund_window_days`; and for staff `customer` and `allowed_statuses` (what the order can be moved to now; empty while a cancellation request is waiting). `shipping_address` is
returned as stored even when incomplete (an older order) instead of failing.

### Telling the admins
When a customer cancels (`order_cancelled`) or asks to cancel a paid order (`order_cancellation_requested`), the store's Enterprise Admins are notified. Finding them is an identity-service
lookup that needs the **caller's bearer token**; the cancel call now passes the customer's token (and the status and decision calls the admin's), where before the token was left out
and the lookup failed quietly. A failed or empty lookup is now logged as a warning (`order N: no Enterprise Admin found to notify` / `could not list the admins of order N`) instead of
being hidden, and never fails the customer's call.

Tests: `tests/test_cart_admin_orders.py` (9 tests: tenant isolation of the list and detail, filters, tracking visible to the customer, the request list, the token reaching the lookup).
