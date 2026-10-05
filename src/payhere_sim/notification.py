"""Build and send PayHere payment notifications (what PayHere POSTs to your notify_url)."""

from __future__ import annotations

import secrets
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass

from . import __version__
from .signing import CURRENCIES, format_amount, notification_sig

STATUS_CODES = {"success": 2, "pending": 0, "cancelled": -1, "failed": -2, "chargedback": -3}
METHODS = ("VISA", "MASTER", "AMEX", "EZCASH", "MCASH", "GENIE", "VISHWA", "PAYAPP", "HNB", "FRIMI")
CARD_METHODS = ("VISA", "MASTER")


def new_payment_id() -> str:
    """A fresh 12-digit ID, shaped like PayHere's."""
    return "32" + "".join(secrets.choice("0123456789") for _ in range(10))


def build(
    *,
    merchant_id: str,
    merchant_secret: str,
    order_id: str,
    amount,
    currency: str = "LKR",
    status: str = "success",
    payment_id: str | None = None,
    method: str = "VISA",
    custom_1: str = "",
    custom_2: str = "",
    extra: dict | None = None,
    bad_signature: bool = False,
) -> dict[str, str]:
    """A notification exactly as PayHere would send it, correctly signed unless `bad_signature`.

    `extra` adds or overrides fields after signing (for recurring/preapproval fields, or to
    send something PayHere never would).
    """
    if status not in STATUS_CODES:
        raise ValueError(f"status must be one of {', '.join(STATUS_CODES)}, not {status!r}")
    if currency not in CURRENCIES:
        raise ValueError(f"currency must be one of {', '.join(CURRENCIES)}, not {currency!r}")
    if method not in METHODS:
        raise ValueError(f"method must be one of {', '.join(METHODS)}, not {method!r}")
    amount = format_amount(amount)
    code = str(STATUS_CODES[status])
    fields = {
        "merchant_id": str(merchant_id),
        "order_id": str(order_id),
        "payment_id": payment_id or new_payment_id(),
        "payhere_amount": amount,
        "payhere_currency": currency,
        "status_code": code,
        "md5sig": notification_sig(merchant_id, order_id, amount, currency, code, merchant_secret),
        "custom_1": custom_1,
        "custom_2": custom_2,
        "method": method,
        "status_message": f"Simulated {status} notification (payhere-sim)",
    }
    if method in CARD_METHODS:
        fields.update(card_holder_name="Test Customer", card_no="************4564", card_expiry="1230")
    if bad_signature:
        fields["md5sig"] = notification_sig(merchant_id, order_id, amount, currency, code, "not-the-secret")
    fields.update({k: str(v) for k, v in (extra or {}).items()})
    return fields


@dataclass
class Response:
    status: int | None  # None when no HTTP response came back
    body: str
    elapsed_ms: int
    error: str | None = None

    @property
    def ok(self) -> bool:
        return self.status is not None and 200 <= self.status < 300

    def describe(self) -> str:
        if self.status is None:
            return f"no response ({self.error})"
        body = " ".join(self.body.split())
        body = body[:80] + "..." if len(body) > 80 else body
        return f"HTTP {self.status} in {self.elapsed_ms} ms" + (f": {body}" if body else "")


def send(url: str, fields: dict, timeout: float = 10) -> Response:
    """POST the notification form-encoded, like PayHere does."""
    data = urllib.parse.urlencode(fields).encode()
    request = urllib.request.Request(url, data=data, method="POST", headers={
        "Content-Type": "application/x-www-form-urlencoded",
        "User-Agent": f"payhere-sim/{__version__}",
    })
    start = time.monotonic()
    elapsed = lambda: int((time.monotonic() - start) * 1000)  # noqa: E731
    try:
        with urllib.request.urlopen(request, timeout=timeout) as r:
            return Response(r.status, r.read().decode(errors="replace"), elapsed())
    except urllib.error.HTTPError as e:
        return Response(e.code, e.read().decode(errors="replace"), elapsed())
    except (urllib.error.URLError, OSError) as e:
        reason = getattr(e, "reason", e)
        return Response(None, "", elapsed(), str(reason))


def as_curl(url: str, fields: dict) -> str:
    """The same request as a curl command, for sharing or scripting."""
    parts = [f"curl -X POST {url!r}"] + [f"  --data-urlencode {f'{k}={v}'!r}" for k, v in fields.items()]
    return " \\\n".join(parts)
