import threading
import urllib.error
import urllib.parse
import urllib.request
from http.server import ThreadingHTTPServer

import pytest

from payhere_sim.server import Simulator, make_handler, validate
from payhere_sim.signing import checkout_hash, verify_notification

MID, SECRET = "1234567", "demo-secret"


def form(**over):
    f = dict(merchant_id=MID, return_url="http://shop.test/thanks", cancel_url="http://shop.test/cart",
             notify_url="http://shop.test/notify", first_name="A", last_name="P", email="a@example.com",
             phone="0771234567", address="1 Galle Rd", city="Colombo", country="Sri Lanka", order_id="ORD-1",
             items="Credits", currency="LKR", amount="1000.00", hash=checkout_hash(MID, "ORD-1", "1000", "LKR", SECRET))
    f.update(over)
    return f


def test_valid_form():
    assert validate(form(), MID, SECRET) == []


@pytest.mark.parametrize("over,expected", [
    ({"email": ""}, "Missing required field `email`"),
    ({"merchant_id": "999"}, "payhere-sim was started with '1234567'"),
    ({"currency": "INR"}, "isn't supported"),
    ({"amount": "1,000.00"}, "isn't a number"),
    ({"amount": "0"}, "more than zero"),
    ({"notify_url": "/notify"}, "full http(s) URL"),
    ({"hash": "0" * 32}, "Unauthorized payment request"),
])
def test_invalid_forms(over, expected):
    assert any(expected in p for p in validate(form(**over), MID, SECRET))


def test_hash_mistake_is_explained():
    problems = validate(form(hash=checkout_hash(MID, "ORD-1", "1000", "LKR", SECRET).lower()), MID, SECRET)
    assert any("lowercase" in p for p in problems)


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, *args):
        return None


@pytest.fixture
def sim(recorder):
    simulator = Simulator(MID, SECRET)
    httpd = ThreadingHTTPServer(("127.0.0.1", 0), make_handler(simulator))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    base = f"http://127.0.0.1:{httpd.server_address[1]}"
    opener = urllib.request.build_opener(NoRedirect)

    def post(path, data):
        try:
            with opener.open(base + path, urllib.parse.urlencode(data).encode()) as r:
                return r.status, r.headers, r.read().decode()
        except urllib.error.HTTPError as e:
            return e.code, e.headers, e.read().decode()

    def get(path):
        with urllib.request.urlopen(base + path) as r:
            return r.read().decode()

    yield simulator, post, get
    httpd.shutdown()


def test_full_checkout_notifies_localhost(sim, recorder):
    simulator, post, get = sim
    status, headers, _ = post("/pay/checkout", form(notify_url=recorder.url, custom_1="user-7"))
    assert status == 303 and headers["Location"] == "/pay/1"
    assert "Pay (success)" in get("/pay/1")

    status, _, body = post("/pay/1/complete", {"outcome": "success", "method": "MASTER"})
    assert status == 200 and "HTTP 200" in body and "http://shop.test/thanks" in body
    content_type, fields = recorder.requests[-1]
    assert content_type == "application/x-www-form-urlencoded"
    assert fields["status_code"] == "2" and fields["method"] == "MASTER" and fields["custom_1"] == "user-7"
    assert verify_notification(fields, SECRET)

    # dashboard actions: a duplicate reuses the payment_id, a late failure uses a new one
    post("/sim/1/notify", {"action": "duplicate"})
    post("/sim/1/notify", {"action": "failed_late"})
    post("/sim/1/notify", {"action": "forged"})
    dup, late, forged = (f for _, f in recorder.requests[-3:])
    assert dup == fields
    assert late["status_code"] == "-2" and late["payment_id"] != fields["payment_id"]
    assert not verify_notification(forged, SECRET)
    assert "ORD-1" in get("/")


def test_cancel_redirects_without_notifying(sim, recorder):
    _, post, _ = sim
    post("/pay/checkout", form(notify_url=recorder.url))
    status, headers, _ = post("/pay/1/complete", {"outcome": "cancel"})
    assert status == 303 and headers["Location"] == "http://shop.test/cart" and recorder.requests == []


def test_rejected_checkout_and_escaping(sim):
    _, post, _ = sim
    status, _, body = post("/pay/checkout", form(hash="bad", items="<script>x</script>"))
    assert status == 400 and "Unauthorized payment request" in body
    assert "<script>x" not in body and "&lt;script&gt;" in body


def test_notify_failure_is_shown(sim, recorder):
    _, post, _ = sim
    recorder.status = 500
    post("/pay/checkout", form(notify_url=recorder.url))
    _, _, body = post("/pay/1/complete", {"outcome": "success"})
    assert "HTTP 500" in body and "sends the notification again" in body


def test_unknown_paths(sim):
    _, post, get = sim
    assert post("/pay/42/complete", {"outcome": "success"})[0] == 404
    assert post("/nope", {})[0] == 404
