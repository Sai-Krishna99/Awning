import json
from dataclasses import replace

from test_web_shop import paypal, place, shop, web  # noqa: F401 (fixtures)

SANDBOX_ACTIVITY = "https://www.sandbox.paypal.com/myaccount/activities/"


def post_event(web, event: dict):
    return web.post("/webhooks/paypal", content=json.dumps(event), headers={"paypal-transmission-id": "t1"})


def configure(ctx) -> None:
    ctx.settings = replace(ctx.settings, paypal_webhook_id="WH1")


def approved(order: dict, paypal_order_id: str = "PPORDER1") -> dict:
    return {
        "event_type": "CHECKOUT.ORDER.APPROVED",
        "resource": {"id": paypal_order_id, "purchase_units": [{"custom_id": order["order_id"]}]},
    }


def test_webhook_rejects_bad_signature(ctx, web, paypal, shop):
    configure(ctx)
    paypal.webhook_valid = False
    order = place(web, shop)
    assert post_event(web, approved(order)).status_code == 400
    assert paypal.capture_count == 0


def test_webhook_rejects_when_unconfigured(ctx, web, paypal, shop):
    assert post_event(web, {"event_type": "X", "resource": {}}).status_code == 400
    assert paypal.calls_to("verify_webhook") == []


def test_webhook_approved_captures_once(ctx, web, paypal, shop):
    configure(ctx)
    order = place(web, shop)
    assert post_event(web, approved(order)).status_code == 200
    assert post_event(web, approved(order)).status_code == 200
    web.get(f"/o/{order['order_id']}/return", follow_redirects=False)
    assert paypal.capture_count == 1
    assert ctx.orders.get(order["order_id"]).status == "paid"
    assert paypal.calls_to("verify_webhook")[0]["webhook_id"] == "WH1"


def test_approved_falls_back_to_paypal_order_id(ctx, web, paypal, shop):
    configure(ctx)
    order = place(web, shop)
    paypal_id = ctx.orders.get(order["order_id"]).paypal_order_id
    event = {"event_type": "CHECKOUT.ORDER.APPROVED", "resource": {"id": paypal_id, "purchase_units": [{}]}}
    assert post_event(web, event).status_code == 200
    assert ctx.orders.get(order["order_id"]).status == "paid"


def test_capture_completed_by_related_order_id(ctx, web, paypal, shop):
    configure(ctx)
    order = place(web, shop)
    paypal_id = ctx.orders.get(order["order_id"]).paypal_order_id
    event = {
        "event_type": "PAYMENT.CAPTURE.COMPLETED",
        "resource": {"id": "CAPX", "supplementary_data": {"related_ids": {"order_id": paypal_id}}},
    }
    assert post_event(web, event).status_code == 200
    assert ctx.orders.get(order["order_id"]).status == "paid"


def test_capture_completed_by_custom_id_is_idempotent(ctx, web, paypal, shop):
    configure(ctx)
    order = place(web, shop)
    event = {"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "CAPX", "custom_id": order["order_id"]}}
    assert post_event(web, event).status_code == 200
    assert post_event(web, event).status_code == 200
    assert paypal.capture_count == 1


def test_unknown_order_and_event_type_acknowledged(ctx, web, paypal, shop):
    configure(ctx)
    unknown = {"event_type": "PAYMENT.CAPTURE.COMPLETED", "resource": {"id": "C", "custom_id": "order_nope"}}
    assert post_event(web, unknown).status_code == 200
    assert post_event(web, {"event_type": "BILLING.PLAN.CREATED", "resource": {}}).status_code == 200
    assert paypal.capture_count == 0


def test_payment_not_completed_returns_500_for_retry(ctx, web, paypal, shop):
    configure(ctx)
    paypal.capture_status = "PENDING"
    order = place(web, shop)
    assert post_event(web, approved(order)).status_code == 500
    assert ctx.orders.get(order["order_id"]).status == "awaiting_payment"


def test_refunded_by_custom_id_then_by_capture_link(ctx, web, paypal, shop):
    configure(ctx)
    first = place(web, shop)
    post_event(web, approved(first))
    refund = {"event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "R1", "custom_id": first["order_id"]}}
    assert post_event(web, refund).status_code == 200
    assert post_event(web, refund).status_code == 200
    assert ctx.orders.get(first["order_id"]).status == "refunded"

    second = place(web, shop)
    post_event(web, approved(second))
    capture_id = ctx.orders.get(second["order_id"]).capture_id
    event = {
        "event_type": "PAYMENT.CAPTURE.REFUNDED",
        "resource": {"id": "R2", "links": [
            {"rel": "self", "href": "https://api.sandbox.paypal.com/v2/payments/refunds/R2"},
            {"rel": "up", "href": f"https://api.sandbox.paypal.com/v2/payments/captures/{capture_id}"},
        ]},
    }
    assert post_event(web, event).status_code == 200
    assert ctx.orders.get(second["order_id"]).status == "refunded"


def test_refunded_does_not_touch_unpaid_order(ctx, web, paypal, shop):
    configure(ctx)
    order = place(web, shop)
    event = {"event_type": "PAYMENT.CAPTURE.REFUNDED", "resource": {"id": "R", "custom_id": order["order_id"]}}
    assert post_event(web, event).status_code == 200
    assert ctx.orders.get(order["order_id"]).status == "awaiting_payment"
