import pytest

from payhere_sim import notification
from payhere_sim.signing import verify_notification

MID, SECRET = "1234567", "demo-secret"


def build(**kw):
    return notification.build(merchant_id=MID, merchant_secret=SECRET, order_id="ORD-1", amount="1000", **kw)


def test_fields_match_payhere_docs():
    f = build(payment_id="320000000001", custom_1="a", custom_2="b")
    assert list(f) == ["merchant_id", "order_id", "payment_id", "payhere_amount", "payhere_currency", "status_code",
                       "md5sig", "custom_1", "custom_2", "method", "status_message", "card_holder_name", "card_no",
                       "card_expiry"]
    assert f["payhere_amount"] == "1000.00" and f["status_code"] == "2" and f["custom_1"] == "a"
    assert f["md5sig"] == "EE2B0589E45DF098B14877F998DBA283"
    assert verify_notification(f, SECRET)


@pytest.mark.parametrize("status,code", [("success", "2"), ("pending", "0"), ("cancelled", "-1"),
                                         ("failed", "-2"), ("chargedback", "-3")])
def test_status_codes_are_signed(status, code):
    f = build(status=status)
    assert f["status_code"] == code and verify_notification(f, SECRET)


def test_card_fields_only_for_cards():
    assert "card_no" in build(method="MASTER")
    assert "card_no" not in build(method="EZCASH")


def test_bad_signature_and_extra():
    assert not verify_notification(build(bad_signature=True), SECRET)
    f = build(extra={"customer_token": "tok", "payhere_amount": "1.00"})  # extras are applied after signing
    assert f["customer_token"] == "tok" and not verify_notification(f, SECRET)


def test_new_payment_ids_differ():
    assert build()["payment_id"] != build()["payment_id"]
    assert len(notification.new_payment_id()) == 12


@pytest.mark.parametrize("kw", [{"status": "paid"}, {"currency": "INR"}, {"method": "PAYPAL"}])
def test_rejects_unknown_values(kw):
    with pytest.raises(ValueError):
        build(**kw)


def test_send_posts_form_encoded(recorder):
    r = notification.send(recorder.url, build())
    assert r.ok and r.status == 200 and r.body == "recorded"
    content_type, fields = recorder.requests[0]
    assert content_type == "application/x-www-form-urlencoded" and fields["order_id"] == "ORD-1"


def test_send_reports_errors(recorder):
    recorder.status = 500
    r = notification.send(recorder.url, build())
    assert not r.ok and r.status == 500 and "HTTP 500" in r.describe()
    down = notification.send("http://127.0.0.1:9/nothing", build(), timeout=2)
    assert down.status is None and down.describe().startswith("no response")


def test_as_curl():
    cmd = notification.as_curl("http://x/notify", {"order_id": "A 1"})
    assert cmd.startswith("curl -X POST 'http://x/notify'") and "'order_id=A 1'" in cmd
