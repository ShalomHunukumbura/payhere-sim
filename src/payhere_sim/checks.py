"""Check that a notify_url handler survives what PayHere (or an attacker) can send it.

The checker can't see your database, so you give it two hooks into your app (a URL or a
shell command each): one that creates a fresh unpaid order, and a *probe* that returns an
order's state. Every scenario gets its own order, and the checker compares states:

- notifications that must be ignored (forged, underpaid, wrong currency) leave it unchanged;
- a valid success changes it;
- repeats and late notifications after that success leave it as the success left it;
- several identical successes at once leave it exactly as one success does.
"""

from __future__ import annotations

import json
import re
import subprocess
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Callable

from . import notification
from .notification import Response, new_payment_id
from .signing import CURRENCIES, format_amount

PASS, FAIL, WARN, SKIP = "pass", "fail", "warn", "skip"


class ProbeError(Exception):
    pass


# --- probes: read the order's state from the app under test -------------------


def url_probe(template: str, timeout: float = 10) -> Callable[[str], object]:
    """GET `template` with {order_id} filled in. JSON bodies are compared as data, others as text."""

    def probe(order_id: str):
        url = template.replace("{order_id}", urllib.parse.quote(order_id, safe=""))
        try:
            with urllib.request.urlopen(url, timeout=timeout) as r:
                body = r.read().decode(errors="replace")
        except urllib.error.HTTPError as e:
            raise ProbeError(f"probe {url} returned HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ProbeError(f"probe {url} failed: {getattr(e, 'reason', e)}") from None
        return _parse(body)

    return probe


def command_probe(template: str, timeout: float = 30) -> Callable[[str], object]:
    """Run a shell command with {order_id} filled in; its stdout is the state."""

    def probe(order_id: str):
        cmd = template.replace("{order_id}", order_id)
        try:
            done = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProbeError(f"probe command timed out: {cmd}") from None
        if done.returncode != 0:
            raise ProbeError(f"probe command exited {done.returncode}: {done.stderr.strip()[:200]}")
        return _parse(done.stdout)

    return probe


@dataclass
class Order:
    id: str
    amount: str
    currency: str


def _order(data, amount: str | None, currency: str | None) -> Order:
    if isinstance(data, dict):
        order_id = data.get("order_id", data.get("id"))
        amount = data.get("amount", amount)
        currency = data.get("currency", currency)
    else:
        order_id = data
    if order_id in (None, ""):
        raise ProbeError(f"new order: expected an order ID or JSON with order_id, got {_show(data)}")
    if amount is None:
        raise ProbeError("new order: the response has no amount, so give --amount")
    return Order(str(order_id), format_amount(amount), currency or "LKR")


def url_factory(url: str, amount=None, currency=None, timeout: float = 10) -> Callable[[], Order]:
    """POST to `url` to create an order. Expects JSON with order_id (and optionally amount, currency)."""

    def new_order():
        request = urllib.request.Request(url, data=b"", method="POST")
        try:
            with urllib.request.urlopen(request, timeout=timeout) as r:
                return _order(_parse(r.read().decode(errors="replace")), amount, currency)
        except urllib.error.HTTPError as e:
            raise ProbeError(f"new order: {url} returned HTTP {e.code}") from None
        except (urllib.error.URLError, OSError) as e:
            raise ProbeError(f"new order: {url} failed: {getattr(e, 'reason', e)}") from None

    return new_order


def command_factory(cmd: str, amount=None, currency=None, timeout: float = 30) -> Callable[[], Order]:
    """Run a shell command that creates an order and prints its ID (or JSON like the URL version)."""

    def new_order():
        try:
            done = subprocess.run(cmd, shell=True, capture_output=True, text=True, timeout=timeout)
        except subprocess.TimeoutExpired:
            raise ProbeError(f"new order command timed out: {cmd}") from None
        if done.returncode != 0:
            raise ProbeError(f"new order command exited {done.returncode}: {done.stderr.strip()[:200]}")
        return _order(_parse(done.stdout), amount, currency)

    return new_order


def _parse(text: str):
    try:
        return json.loads(text)
    except ValueError:
        return text.strip()


def _without(state, ignore: set[str]):
    """Drop ignored keys (timestamps and the like) anywhere in a JSON state."""
    if isinstance(state, dict):
        return {k: _without(v, ignore) for k, v in state.items() if k not in ignore}
    if isinstance(state, list):
        return [_without(v, ignore) for v in state]
    return state


def _placeholders(state, replace: dict[str, str]):
    """Swap order/payment IDs for placeholders so two different orders can be compared."""
    if isinstance(state, dict):
        return {k: _placeholders(v, replace) for k, v in state.items()}
    if isinstance(state, list):
        return [_placeholders(v, replace) for v in state]
    if isinstance(state, (str, int)) and not isinstance(state, bool) and str(state) in replace:
        return replace[str(state)]
    if isinstance(state, str):
        for old, new in replace.items():
            state = re.sub(rf"(?<![\w-]){re.escape(old)}(?![\w-])", new, state)
    return state


def _show(value) -> str:
    text = json.dumps(value, sort_keys=True) if not isinstance(value, str) else value
    return text if len(text) <= 120 else text[:117] + "..."


def describe_change(before, after) -> str:
    if isinstance(before, dict) and isinstance(after, dict):
        changes = [f"{k}: {_show(before.get(k, '(missing)'))} → {_show(after.get(k, '(missing)'))}"
                   for k in sorted(before.keys() | after.keys()) if before.get(k, ...) != after.get(k, ...)]
        return "; ".join(changes)
    return f"{_show(before)} → {_show(after)}"


# --- results -------------------------------------------------------------------


@dataclass
class Result:
    name: str
    title: str
    outcome: str
    detail: str = ""
    why: str = ""


@dataclass
class Target:
    notify_url: str
    merchant_id: str
    merchant_secret: str
    new_order: Callable[[], Order]
    probe: Callable[[str], object]
    race_copies: int = 5
    ignore: set[str] = field(default_factory=set)
    timeout: float = 10


WHY = {
    "forged": "Anyone can POST to your notify_url. Recompute md5sig with your merchant secret and "
              "reject the notification if it doesn't match.",
    "underpaid": "If the checkout hash is made in the browser, a customer can change the amount and pay "
                 "less. Check payhere_amount equals the order total before marking it paid.",
    "currency": "A notification for 1,000 USD is not 1,000 LKR. Check payhere_currency matches the order.",
    "success": "A correctly signed success notification should mark the order paid. If it did, the probe "
               "isn't showing it: make the probe return the order's status.",
    "duplicate": "PayHere retries a notification if it doesn't get HTTP 200 in time, so the same one can "
                 "arrive twice. Make the handler idempotent: do nothing if the order is already paid.",
    "late_pending": "Notifications aren't guaranteed to arrive in order. Never move an order backwards "
                    "from paid because of an older status.",
    "late_failed": "Each payment attempt has its own payment_id. A failed attempt's notification can arrive "
                   "after the successful one: it must not un-pay the order.",
    "responds_2xx": "PayHere treats anything but HTTP 200 as not delivered and tries again, which is how "
                    "duplicates happen. Return 200 once you've handled (or deliberately ignored) it.",
    "chargeback": "Status -3 means the customer's bank reversed the payment. You'll usually want to flag the "
                  "order or take back what was delivered.",
    "race": "Retries can arrive at the same moment. Two requests that both read 'not paid yet' will both "
            "fulfil the order. Mark it paid with one atomic UPDATE ... WHERE status != 'paid' (or a "
            "unique constraint on payment_id) and only fulfil if that update changed a row.",
}

TITLES = {
    "forged": "ignores a notification with a forged signature",
    "underpaid": "ignores a signed success for less than the order total",
    "currency": "ignores a signed success in the wrong currency",
    "success": "records a valid success notification",
    "duplicate": "processes a repeated notification only once",
    "late_pending": "keeps the order paid when a late 'pending' arrives",
    "late_failed": "keeps the order paid when an earlier failed attempt arrives late",
    "responds_2xx": "answers valid notifications with HTTP 2xx",
    "chargeback": "records a chargeback",
    "race": "processes simultaneous duplicates only once",
}


class Checker:
    def __init__(self, target: Target, on_result: Callable[[Result], None] | None = None):
        self.t = target
        self.results: list[Result] = []
        self.on_result = on_result
        self.bad_responses: list[str] = []
        self.orders: list[Order] = []

    # helpers

    def new_order(self) -> Order:
        order = self.t.new_order()
        self.orders.append(order)
        return order

    def state(self, order: Order):
        return _without(self.t.probe(order.id), self.t.ignore)

    def fields(self, order: Order, **kw) -> dict:
        kw.setdefault("amount", order.amount)
        kw.setdefault("currency", order.currency)
        return notification.build(merchant_id=self.t.merchant_id, merchant_secret=self.t.merchant_secret,
                                  order_id=order.id, **kw)

    def notify(self, order: Order, *, expect_ok=True, **kw) -> Response:
        response = notification.send(self.t.notify_url, self.fields(order, **kw), timeout=self.t.timeout)
        if expect_ok and not response.ok:
            self.bad_responses.append(f"{kw.get('status', 'success')}: {response.describe()}")
        return response

    def record(self, name, outcome, detail=""):
        result = Result(name, TITLES[name], outcome, detail, WHY[name] if outcome in (FAIL, WARN) else "")
        self.results.append(result)
        if self.on_result:
            self.on_result(result)

    def unchanged(self, name, before, after, response: Response):
        if after == before:
            self.record(name, PASS, f"state unchanged ({response.describe()})")
        else:
            self.record(name, FAIL, f"state changed: {describe_change(before, after)}")

    def must_ignore(self, name, change: Callable[[Order], dict]):
        """A signed-looking success that's wrong for this order must leave it as it was."""
        order = self.new_order()
        before = self.state(order)
        response = self.notify(order, status="success", payment_id=new_payment_id(), expect_ok=False,
                               **change(order))
        self.unchanged(name, before, self.state(order), response)

    # the run

    def run(self) -> list[Result]:
        self.must_ignore("forged", lambda o: {"bad_signature": True})
        self.must_ignore("underpaid", lambda o: {"amount": format_amount(Decimal(o.amount) / 100)})
        self.must_ignore("currency", lambda o: {"currency": next(c for c in CURRENCIES if c != o.currency)})

        order = self.new_order()
        payment_id = new_payment_id()
        before = self.state(order)
        self.notify(order, status="pending", payment_id=payment_id)
        pending = self.state(order)
        r = self.notify(order, status="success", payment_id=payment_id)
        paid = once = self.state(order)
        if paid in (before, pending):
            self.record("success", FAIL, f"state didn't change ({r.describe()}): {_show(paid)}")
            for name in ("duplicate", "late_pending", "late_failed", "race", "chargeback"):
                self.record(name, SKIP, "needs a recorded success")
            self.finish_responses()
            return self.results
        self.record("success", PASS, describe_change(pending, paid))

        r = self.notify(order, status="success", payment_id=payment_id)
        self.unchanged("duplicate", paid, paid := self.state(order), r)  # later checks judged on their own

        r = self.notify(order, status="pending", payment_id=payment_id)
        self.unchanged("late_pending", paid, paid := self.state(order), r)

        r = self.notify(order, status="failed", payment_id=new_payment_id())
        self.unchanged("late_failed", paid, paid := self.state(order), r)

        self.check_race({order.id: "{order_id}", payment_id: "{payment_id}"}, once)
        self.finish_responses()

        r = self.notify(order, status="chargedback", payment_id=payment_id)
        after = self.state(order)
        if after != paid:
            self.record("chargeback", PASS, describe_change(paid, after))
        else:
            self.record("chargeback", WARN, f"state unchanged ({r.describe()})")
        return self.results

    def finish_responses(self):
        if self.bad_responses:
            self.record("responds_2xx", FAIL, "; ".join(dict.fromkeys(self.bad_responses)))
        else:
            self.record("responds_2xx", PASS)

    def check_race(self, swap_once: dict, once):
        """Same history as the success order (pending, then success), but the success arrives N times at once."""
        t = self.t
        order, payment_id = self.new_order(), new_payment_id()
        self.notify(order, status="pending", payment_id=payment_id)
        fields = self.fields(order, status="success", payment_id=payment_id)
        with ThreadPoolExecutor(t.race_copies) as pool:
            responses = list(pool.map(lambda _: notification.send(t.notify_url, fields, t.timeout),
                                      range(t.race_copies)))
        after = _placeholders(self.state(order), {order.id: "{order_id}", payment_id: "{payment_id}"})
        expected = _placeholders(once, swap_once)
        codes = ", ".join(str(r.status) for r in responses)
        if after == expected:
            self.record("race", PASS, f"{t.race_copies} at once left the same state as one (HTTP {codes})")
        else:
            self.record("race", FAIL, f"{t.race_copies} at once vs one: {describe_change(expected, after)}")
