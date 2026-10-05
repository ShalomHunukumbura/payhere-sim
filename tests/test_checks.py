"""Run the checker against the example shop: the buggy handler fails, the safe one passes."""

import json
import sys

import pytest

from payhere_sim import checks
from payhere_sim.checks import FAIL, PASS, _placeholders, describe_change

from .conftest import MERCHANT_ID, SECRET


def run(base, **kw):
    target = checks.Target(
        notify_url=f"{base}/payhere/notify", merchant_id=MERCHANT_ID, merchant_secret=SECRET,
        new_order=checks.url_factory(f"{base}/orders"), probe=checks.url_probe(base + "/orders/{order_id}"), **kw,
    )
    return {r.name: r for r in checks.Checker(target).run()}


def test_safe_handler_passes_everything(shops):
    results = run(shops["safe"])
    assert {name: r.outcome for name, r in results.items()} == dict.fromkeys(checks.TITLES, PASS)


def test_buggy_handler_is_caught(shops):
    results = run(shops["buggy"])
    failed = {name for name, r in results.items() if r.outcome == FAIL}
    assert failed == {"underpaid", "currency", "late_pending", "late_failed", "race"}
    assert results["race"].detail.endswith("credits: 100 → 500")
    assert results["underpaid"].why.startswith("If the checkout hash")
    # the sequential duplicate passes: the bug only shows when copies arrive together
    assert results["duplicate"].outcome == PASS


def test_wrong_secret_means_success_is_never_recorded(shops):
    base = shops["safe"]
    target = checks.Target(
        notify_url=f"{base}/payhere/notify", merchant_id=MERCHANT_ID, merchant_secret="not-the-app-secret",
        new_order=checks.url_factory(f"{base}/orders"), probe=checks.url_probe(base + "/orders/{order_id}"),
    )
    results = {r.name: r for r in checks.Checker(target).run()}
    assert results["success"].outcome == FAIL and results["responds_2xx"].outcome == FAIL
    assert results["race"].outcome == checks.SKIP


def test_command_hooks(shops, tmp_path):
    base = shops["safe"]
    py = sys.executable
    new = f"{py} -c \"import urllib.request;print(urllib.request.urlopen(urllib.request.Request('{base}/orders', b'')).read().decode())\""
    probe = f"{py} -c \"import urllib.request;print(urllib.request.urlopen('{base}/orders/{{order_id}}').read().decode())\""
    target = checks.Target(notify_url=f"{base}/payhere/notify", merchant_id=MERCHANT_ID, merchant_secret=SECRET,
                           new_order=checks.command_factory(new), probe=checks.command_probe(probe))
    assert all(r.outcome == PASS for r in checks.Checker(target).run())


def test_probe_errors(shops):
    with pytest.raises(checks.ProbeError, match="HTTP 404"):
        checks.url_probe(shops["safe"] + "/orders/{order_id}")("999999")
    with pytest.raises(checks.ProbeError, match="exited 3"):
        checks.command_probe("exit 3")("1")
    with pytest.raises(checks.ProbeError, match="no amount"):
        checks.command_factory("echo 42")()
    assert checks.command_factory("echo 42", amount="10")().amount == "10.00"
    order = checks.command_factory(f"echo '{json.dumps({'id': 7, 'amount': 5, 'currency': 'USD'})}'")()
    assert (order.id, order.amount, order.currency) == ("7", "5.00", "USD")


def test_ignore_and_placeholders():
    assert checks._without({"a": 1, "updated_at": 2, "n": [{"updated_at": 3}]}, {"updated_at"}) == {"a": 1, "n": [{}]}
    swap = {"12": "{order_id}"}
    assert _placeholders({"id": 12, "ref": "order 12 of 120", "x": 120}, swap) == \
        {"id": "{order_id}", "ref": "order {order_id} of 120", "x": 120}
    assert describe_change({"a": 1, "b": 2}, {"a": 1, "b": 3, "c": 4}) == "b: 2 → 3; c: (missing) → 4"
