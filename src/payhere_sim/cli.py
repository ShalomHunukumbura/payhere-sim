"""payhere-sim command line: send, check, serve, hash."""

from __future__ import annotations

import argparse
import os
import shutil
import sys
import textwrap

from . import __version__, checks, notification
from .notification import METHODS, STATUS_CODES
from .signing import CURRENCIES, checkout_hash, format_amount

ICONS = {checks.PASS: "✓", checks.FAIL: "✗", checks.WARN: "!", checks.SKIP: "-"}
COLORS = {checks.PASS: "32", checks.FAIL: "31", checks.WARN: "33", checks.SKIP: "90"}


def color(text: str, code: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def merchant(args) -> tuple[str, str]:
    mid = args.merchant_id or os.environ.get("PAYHERE_MERCHANT_ID")
    secret = args.secret or os.environ.get("PAYHERE_MERCHANT_SECRET")
    if not mid or not secret:
        raise SystemExit("payhere-sim: set the merchant ID and secret your app uses, with --merchant-id/--secret "
                         "or PAYHERE_MERCHANT_ID/PAYHERE_MERCHANT_SECRET")
    return mid, secret


def amount(value: str) -> str:
    try:
        return format_amount(value)
    except ValueError as e:
        raise argparse.ArgumentTypeError(str(e)) from None


def key_value(value: str) -> tuple[str, str]:
    key, sep, val = value.partition("=")
    if not sep or not key:
        raise argparse.ArgumentTypeError(f"expected NAME=VALUE, got {value!r}")
    return key, val


def parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="payhere-sim", description="Test PayHere integrations on your own machine.")
    p.add_argument("--version", action="version", version=f"payhere-sim {__version__}")
    sub = p.add_subparsers(dest="command", required=True)

    creds = argparse.ArgumentParser(add_help=False)
    creds.add_argument("--merchant-id", help="default: $PAYHERE_MERCHANT_ID")
    creds.add_argument("--secret", help="merchant secret; default: $PAYHERE_MERCHANT_SECRET")

    timeout = argparse.ArgumentParser(add_help=False)
    timeout.add_argument("--timeout", type=float, default=10, help="seconds to wait for your app (default 10)")

    s = sub.add_parser("send", parents=[creds, timeout], help="send one signed notification to a URL",
                       description="POST a correctly signed PayHere notification to your notify_url.")
    s.add_argument("url", help="your notify_url, e.g. http://localhost:8000/payhere/notify")
    s.add_argument("--order-id", required=True, help="an order that exists in your app")
    s.add_argument("--amount", required=True, type=amount, help="the order total, e.g. 1000 or 1000.00")
    s.add_argument("--currency", default="LKR", choices=CURRENCIES)
    s.add_argument("--status", default="success", choices=STATUS_CODES)
    s.add_argument("--payment-id", help="default: a new random one (reuse one to send a duplicate)")
    s.add_argument("--method", default="VISA", choices=METHODS)
    s.add_argument("--custom-1", default="")
    s.add_argument("--custom-2", default="")
    s.add_argument("--field", action="append", type=key_value, default=[], metavar="NAME=VALUE",
                   help="add or override a field after signing (repeatable)")
    s.add_argument("--bad-signature", action="store_true", help="sign with the wrong secret")
    s.add_argument("--repeat", type=int, default=1, help="send the identical notification N times")
    s.add_argument("--curl", action="store_true", help="print it as a curl command instead of sending")

    c = sub.add_parser("check", parents=[creds, timeout], help="check a notify_url handler is safe",
                       description="Send the notifications PayHere (or an attacker) can send, and check your "
                                   "app's state after each one. Each scenario uses a fresh order (5 in all).")
    c.add_argument("url", help="your notify_url")
    new = c.add_mutually_exclusive_group(required=True)
    new.add_argument("--new-order", metavar="URL", help="POST here to create a fresh unpaid order; it should "
                                                        "return JSON with order_id (and amount, currency)")
    new.add_argument("--new-order-cmd", metavar="CMD", help="or run this; it prints an order ID or that JSON")
    c.add_argument("--amount", type=amount, help="order total, if the new-order response doesn't include it")
    c.add_argument("--currency", choices=CURRENCIES, help="order currency if not in the response (default LKR)")
    probe = c.add_mutually_exclusive_group(required=True)
    probe.add_argument("--probe", metavar="URL", help="GET this to read the order's state; {order_id} is "
                                                      "replaced, e.g. http://localhost:8000/orders/{order_id}")
    probe.add_argument("--probe-cmd", metavar="CMD", help="or run this shell command; its output is the state")
    c.add_argument("--race-copies", type=int, default=5, help="how many at once (default 5)")
    c.add_argument("--ignore", action="append", default=[], metavar="KEY",
                   help="JSON key to leave out of comparisons, e.g. updated_at (repeatable)")

    v = sub.add_parser("serve", parents=[creds], help="run a local PayHere checkout page",
                       description="A local stand-in for PayHere's checkout that can notify localhost.")
    v.add_argument("--host", default="127.0.0.1")
    v.add_argument("--port", type=int, default=9090)

    h = sub.add_parser("hash", parents=[creds], help="compute a checkout hash",
                       description="Print the hash your checkout form should send.")
    h.add_argument("--order-id", required=True)
    h.add_argument("--amount", required=True, type=amount)
    h.add_argument("--currency", default="LKR", choices=CURRENCIES)
    return p


def cmd_send(args) -> int:
    mid, secret = merchant(args)
    fields = notification.build(
        merchant_id=mid, merchant_secret=secret, order_id=args.order_id, amount=args.amount,
        currency=args.currency, status=args.status, payment_id=args.payment_id, method=args.method,
        custom_1=args.custom_1, custom_2=args.custom_2, extra=dict(args.field), bad_signature=args.bad_signature,
    )
    if args.curl:
        print(notification.as_curl(args.url, fields))
        return 0
    worst = 0
    for i in range(args.repeat):
        r = notification.send(args.url, fields, timeout=args.timeout)
        prefix = f"[{i + 1}/{args.repeat}] " if args.repeat > 1 else ""
        print(f"{prefix}{args.status} order {args.order_id} payment {fields['payment_id']} → "
              f"{color(r.describe(), '32' if r.ok else '31')}")
        worst = worst or (0 if r.ok else 1)
    return worst


def cmd_check(args) -> int:
    mid, secret = merchant(args)
    probe = checks.url_probe(args.probe) if args.probe else checks.command_probe(args.probe_cmd)
    new_order = (checks.url_factory(args.new_order, args.amount, args.currency, args.timeout) if args.new_order
                 else checks.command_factory(args.new_order_cmd, args.amount, args.currency))
    target = checks.Target(
        notify_url=args.url, merchant_id=mid, merchant_secret=secret, new_order=new_order, probe=probe,
        race_copies=args.race_copies, ignore=set(args.ignore), timeout=args.timeout,
    )
    print(f"payhere-sim check → {args.url}\n")

    width = min(shutil.get_terminal_size().columns, 100)

    def indented(text: str) -> str:
        return textwrap.fill(text, width, initial_indent=" " * 6, subsequent_indent=" " * 6)

    def show(r: checks.Result):
        print(f"  {color(ICONS[r.outcome], COLORS[r.outcome])} {r.title}")
        if r.outcome != checks.PASS and r.detail:
            print(color(indented(r.detail), "90" if r.outcome == checks.SKIP else COLORS[r.outcome]))
        if r.why:
            print(indented(r.why))

    checker = checks.Checker(target, on_result=show)
    try:
        results = checker.run()
    except checks.ProbeError as e:
        print(color(f"\n  {e}", "31"))
        return 2
    counts = {o: sum(r.outcome == o for r in results) for o in ICONS}
    summary = ", ".join(f"{n} {label}" for label, n in (
        ("passed", counts["pass"]), ("failed", counts["fail"]), ("warnings", counts["warn"]),
        ("skipped", counts["skip"])) if n)
    print(f"\n{summary} · orders used: {', '.join(o.id for o in checker.orders)}")
    return 1 if counts["fail"] else 0


def cmd_serve(args) -> int:
    from .server import serve

    mid, secret = merchant(args)
    serve(mid, secret, host=args.host, port=args.port)
    return 0


def cmd_hash(args) -> int:
    mid, secret = merchant(args)
    print(checkout_hash(mid, args.order_id, args.amount, args.currency, secret))
    return 0


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    try:
        return {"send": cmd_send, "check": cmd_check, "serve": cmd_serve, "hash": cmd_hash}[args.command](args)
    except ValueError as e:
        print(f"payhere-sim: {e}", file=sys.stderr)
        return 2
