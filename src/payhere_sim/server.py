"""A local stand-in for PayHere's hosted checkout.

Point your checkout form at http://localhost:PORT/pay/checkout instead of
https://sandbox.payhere.lk/pay/checkout. payhere-sim checks the form and its hash (explaining
common mistakes), shows a payment page, then sends the signed notification to your notify_url
from your own machine, so localhost URLs work.
"""

from __future__ import annotations

import html
import itertools
import threading
import urllib.parse
from dataclasses import dataclass, field
from datetime import datetime
from decimal import Decimal
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from . import __version__, notification
from .notification import METHODS, STATUS_CODES, Response, new_payment_id
from .signing import CURRENCIES, checkout_hash, diagnose_checkout_hash, format_amount

REQUIRED = ("merchant_id", "return_url", "cancel_url", "notify_url", "first_name", "last_name", "email",
            "phone", "address", "city", "country", "order_id", "items", "currency", "amount", "hash")


@dataclass
class Sent:
    status: str
    fields: dict
    response: Response
    forged: bool = False
    at: str = field(default_factory=lambda: datetime.now().strftime("%H:%M:%S"))


@dataclass
class Checkout:
    id: str
    form: dict
    payment_id: str = field(default_factory=new_payment_id)
    sent: list[Sent] = field(default_factory=list)


def validate(form: dict, merchant_id: str, merchant_secret: str) -> list[str]:
    """Problems PayHere would reject the checkout for, each with a fix."""
    problems = [f"Missing required field `{name}`." for name in REQUIRED if not form.get(name, "").strip()]
    if form.get("merchant_id") and form["merchant_id"] != merchant_id:
        problems.append(f"merchant_id is {form['merchant_id']!r}, but payhere-sim was started with "
                        f"{merchant_id!r}. Use the same merchant ID (and secret) in both.")
    if form.get("currency") and form["currency"] not in CURRENCIES:
        problems.append(f"currency {form['currency']!r} isn't supported. Use one of {', '.join(CURRENCIES)}.")
    amount = form.get("amount", "")
    try:
        if amount and Decimal(format_amount(amount)) <= 0:
            problems.append("amount must be more than zero.")
    except ValueError:
        problems.append(f"amount {amount!r} isn't a number. Send it like 1000.00 (no commas).")
    for name in ("return_url", "cancel_url", "notify_url"):
        value = form.get(name, "")
        if value and urllib.parse.urlsplit(value).scheme not in ("http", "https"):
            problems.append(f"{name} must be a full http(s) URL, not {value!r}.")
    if not problems:
        expected = checkout_hash(merchant_id, form["order_id"], amount, form["currency"], merchant_secret)
        if form["hash"] != expected:
            hints = diagnose_checkout_hash(form, merchant_secret) or [
                "No common mistake matches. Check you're using this merchant's secret (sandbox and live "
                "secrets differ, and each domain/app has its own) and hashing exactly the posted values."]
            problems.append("The hash doesn't match. PayHere would say \"Unauthorized payment request\".")
            problems += hints
            problems.append("Expected: upper(md5(merchant_id + order_id + amount + currency + "
                            f"upper(md5(merchant_secret)))) with amount = {format_amount(amount)!r}.")
    return problems


class Simulator:
    def __init__(self, merchant_id: str, merchant_secret: str):
        self.merchant_id = merchant_id
        self.merchant_secret = merchant_secret
        self.checkouts: dict[str, Checkout] = {}
        self._ids = itertools.count(1)
        self._lock = threading.Lock()

    def new_checkout(self, form: dict) -> Checkout:
        with self._lock:
            checkout = Checkout(str(next(self._ids)), form)
            self.checkouts[checkout.id] = checkout
        return checkout

    def notify(self, checkout: Checkout, status: str, *, method="VISA", payment_id=None,
               bad_signature=False) -> Sent:
        form = checkout.form
        fields = notification.build(
            merchant_id=self.merchant_id, merchant_secret=self.merchant_secret, order_id=form["order_id"],
            amount=form["amount"], currency=form["currency"], status=status,
            payment_id=payment_id or checkout.payment_id, method=method, custom_1=form.get("custom_1", ""),
            custom_2=form.get("custom_2", ""), bad_signature=bad_signature,
        )
        sent = Sent(status, fields, notification.send(form["notify_url"], fields), forged=bad_signature)
        checkout.sent.append(sent)
        label = status + (" (forged signature)" if bad_signature else "")
        log(f"order {form['order_id']}: {label} → {form['notify_url']}  {sent.response.describe()}")
        return sent


def log(message: str):
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


# --- HTML ---------------------------------------------------------------------

e = html.escape

CSS = """
:root{--bg:#f6f7f9;--card:#fff;--text:#1d2330;--muted:#647083;--line:#e2e5ea;--brand:#2563eb;
--ok:#15803d;--okbg:#e8f6ec;--bad:#b91c1c;--badbg:#fdecec;--warn:#a16207;--warnbg:#fdf6e3}
@media (prefers-color-scheme:dark){:root{--bg:#12151b;--card:#1b2029;--text:#e6e9ef;--muted:#97a1b3;
--line:#2c333f;--brand:#6b9bff;--ok:#4ade80;--okbg:#14301f;--bad:#f87171;--badbg:#3a1a1a;--warn:#facc15;--warnbg:#352d10}}
*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--text);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:760px;margin:0 auto;padding:24px 16px}
header{display:flex;justify-content:space-between;align-items:baseline;gap:12px;flex-wrap:wrap}
h1{font-size:20px;margin:0}h2{font-size:16px;margin:24px 0 8px}a{color:var(--brand)}
.tag{font-size:12px;color:var(--muted)}.card{background:var(--card);border:1px solid var(--line);
border-radius:10px;padding:16px;margin:16px 0}.amount{font-size:28px;font-weight:600}
.muted{color:var(--muted)}table{width:100%;border-collapse:collapse;font-size:14px}
td,th{text-align:left;padding:6px 8px;border-bottom:1px solid var(--line);vertical-align:top;word-break:break-word}
code{font:13px ui-monospace,Menlo,Consolas,monospace}
button,.btn{font:inherit;border:1px solid var(--line);background:var(--card);color:var(--text);
border-radius:8px;padding:8px 14px;cursor:pointer;text-decoration:none;display:inline-block}
button.primary,.btn.primary{background:var(--brand);border-color:var(--brand);color:#fff}
.row{display:flex;gap:8px;flex-wrap:wrap;align-items:center}
.ok{color:var(--ok);background:var(--okbg)}.bad{color:var(--bad);background:var(--badbg)}
.warn{color:var(--warn);background:var(--warnbg)}.pill{border-radius:6px;padding:2px 8px;font-size:13px}
.msg{border-radius:8px;padding:12px;margin:12px 0}ul{padding-left:20px}select{font:inherit;padding:7px;
border-radius:8px;border:1px solid var(--line);background:var(--card);color:var(--text)}
"""


def page(title: str, body: str) -> bytes:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1"><title>{e(title)}</title>
<style>{CSS}</style></head><body><main>
<header><h1>{e(title)}</h1><span class="tag">payhere-sim {__version__} · local simulator, no real money</span></header>
{body}</main></body></html>""".encode()


def response_pill(response: Response) -> str:
    cls = "ok" if response.ok else "bad"
    return f'<span class="pill {cls}">{e(response.describe())}</span>'


def money(form) -> str:
    return f"{e(form.get('currency', ''))} {e(format_amount(form['amount']))}"


def dashboard(sim: Simulator, host: str) -> bytes:
    rows = []
    for c in reversed(sim.checkouts.values()):
        last = c.sent[-1] if c.sent else None
        status = (f"{e(last.status)}{' (forged)' if last.forged else ''} {response_pill(last.response)}" if last else '<span class="muted">not paid yet</span>')
        rows.append(f"""<tr><td><a href="/pay/{c.id}">{e(c.form['order_id'])}</a></td><td>{money(c.form)}</td>
<td>{status}</td><td><form method="post" action="/sim/{c.id}/notify" class="row">
<select name="action" aria-label="Notification to send">
<option value="duplicate">Send last notification again</option><option value="chargedback">Send chargeback</option>
<option value="failed_late">Send a late 'failed' (earlier attempt)</option>
<option value="forged">Send forged signature</option></select><button>Send</button></form></td></tr>""")
    table = ("<table><tr><th>Order</th><th>Amount</th><th>Last notification</th><th>Send another</th></tr>"
             + "".join(rows) + "</table>") if rows else '<p class="muted">No checkouts yet.</p>'
    return page("PayHere simulator", f"""
<div class="card"><p>Point your checkout form here instead of the PayHere sandbox:</p>
<p><code>&lt;form method="post" action="{e(host)}/pay/checkout"&gt;</code></p>
<p class="muted">Merchant ID <code>{e(sim.merchant_id)}</code>. Notifications are sent from this machine,
so <code>notify_url</code> can be <code>localhost</code>.</p></div>
<h2>Checkouts</h2>{table}""")


def checkout_page(c: Checkout) -> bytes:
    f = c.form
    history = "".join(f"<tr><td>{e(s.at)}</td><td>{e(s.status)}{' (forged)' if s.forged else ''}</td><td>{response_pill(s.response)}</td></tr>"
                      for s in c.sent)
    methods = "".join(f"<option>{m}</option>" for m in METHODS)
    return page(f"Pay {f.get('items', '')}", f"""
<div class="card"><div class="muted">Order {e(f['order_id'])} · {e(f.get('first_name', ''))} {e(f.get('last_name', ''))}
 · {e(f.get('email', ''))}</div><div class="amount">{money(f)}</div><div>{e(f.get('items', ''))}</div>
<div class="msg ok">Checkout form and hash are valid.</div></div>
<form method="post" action="/pay/{c.id}/complete" class="card">
<p>Choose what happens. payhere-sim sends the signed notification to
<code>{e(f['notify_url'])}</code>, then shows you the response.</p>
<p class="row"><label>Payment method <select name="method">{methods}</select></label></p>
<p class="row"><button class="primary" name="outcome" value="success">Pay (success)</button>
<button name="outcome" value="failed">Fail the payment</button>
<button name="outcome" value="pending">Leave pending</button>
<button name="outcome" value="cancel">Cancel and go back</button></p></form>
{f'<h2>Notifications sent</h2><table>{history}</table>' if history else ''}
<p><a href="/">All checkouts</a></p>""")


def result_page(c: Checkout, sent: Sent) -> bytes:
    f = c.form
    r = sent.response
    rows = "".join(f"<tr><td><code>{e(k)}</code></td><td><code>{e(v)}</code></td></tr>" for k, v in sent.fields.items())
    advice = "" if r.ok else ("""<div class="msg bad">Your notify_url didn't answer with HTTP 200. PayHere treats that
as not delivered and sends the notification again, so make sure your handler copes with repeats.</div>""")
    target, label = (f["return_url"], "Continue to return_url") if sent.status in ("success", "pending") \
        else (f["cancel_url"], "Back to cancel_url")
    body = (f"<div class='msg bad'>{e(r.body[:2000])}</div>" if r.body and not r.ok
            else f"<pre><code>{e(r.body[:2000])}</code></pre>" if len(r.body) > 80 else "")
    return page(f"Sent {sent.status} for order {f['order_id']}", f"""
<div class="card"><p>POST <code>{e(f['notify_url'])}</code> → {response_pill(r)}</p>{advice}{body}
<p class="row"><a class="btn primary" href="{e(target)}">{label}</a><a class="btn" href="/pay/{c.id}">Back to checkout</a>
<a class="btn" href="/">All checkouts</a></p>
<p class="muted">Like PayHere, no payment status is added to return_url: your page should read it from your database.</p></div>
<h2>What was sent (application/x-www-form-urlencoded)</h2><div class="card"><table>{rows}</table></div>""")


def error_page(problems: list[str], form: dict) -> bytes:
    items = "".join(f"<li>{e(p)}</li>" for p in problems)
    rows = "".join(f"<tr><td><code>{e(k)}</code></td><td><code>{e(v)}</code></td></tr>" for k, v in form.items())
    return page("Checkout rejected", f"""
<div class="msg bad"><strong>PayHere would reject this checkout.</strong><ul>{items}</ul></div>
<h2>What your site posted</h2><div class="card"><table>{rows or '<tr><td>(nothing)</td></tr>'}</table></div>""")


# --- HTTP ---------------------------------------------------------------------


def make_handler(sim: Simulator):
    class Handler(BaseHTTPRequestHandler):
        server_version = f"payhere-sim/{__version__}"

        def log_message(self, format, *args):  # quiet: we log what matters ourselves
            pass

        def send_html(self, body: bytes, status=200):
            self.send_response(status)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def redirect(self, location: str):
            self.send_response(303)
            self.send_header("Location", location)
            self.send_header("Content-Length", "0")
            self.end_headers()

        def form(self) -> dict:
            length = int(self.headers.get("Content-Length") or 0)
            raw = self.rfile.read(length).decode(errors="replace")
            return {k: v[-1] for k, v in urllib.parse.parse_qs(raw, keep_blank_values=True).items()}

        def not_found(self):
            self.send_html(page("Not found", "<p>Nothing here. <a href='/'>All checkouts</a></p>"), 404)

        def route(self) -> tuple[list[str], Checkout | None]:
            parts = urllib.parse.urlsplit(self.path).path.strip("/").split("/")
            checkout = sim.checkouts.get(parts[1]) if len(parts) > 1 and parts[0] in ("pay", "sim") else None
            return parts, checkout

        def do_GET(self):
            parts, c = self.route()
            if parts == [""]:
                return self.send_html(dashboard(sim, f"http://{self.headers.get('Host', 'localhost')}"))
            if len(parts) == 2 and parts[0] == "pay" and c:
                return self.send_html(checkout_page(c))
            self.not_found()

        def do_POST(self):
            parts, c = self.route()
            form = self.form()
            if parts == ["pay", "checkout"]:
                problems = validate(form, sim.merchant_id, sim.merchant_secret)
                if problems:
                    log(f"checkout {form.get('order_id', '?')} rejected: {problems[0]}")
                    return self.send_html(error_page(problems, form), 400)
                c = sim.new_checkout(form)
                log(f"checkout order {form['order_id']} {form['currency']} {format_amount(form['amount'])}: hash ok")
                return self.redirect(f"/pay/{c.id}")
            if c and parts == ["pay", c.id, "complete"]:
                outcome = form.get("outcome", "")
                if outcome == "cancel":
                    log(f"order {c.form['order_id']}: cancelled by customer → {c.form['cancel_url']}")
                    return self.redirect(c.form["cancel_url"])
                if outcome not in STATUS_CODES:
                    return self.send_html(page("Bad request", "<p>Unknown outcome.</p>"), 400)
                method = form.get("method") if form.get("method") in METHODS else "VISA"
                return self.send_html(result_page(c, sim.notify(c, outcome, method=method)))
            if c and parts == ["sim", c.id, "notify"]:
                action, last = form.get("action"), (c.sent[-1] if c.sent else None)
                if action == "duplicate" and last:
                    sim.notify(c, last.status, method=last.fields["method"], payment_id=last.fields["payment_id"],
                               bad_signature=last.forged)
                elif action == "chargedback":
                    sim.notify(c, "chargedback")
                elif action == "failed_late":
                    sim.notify(c, "failed", payment_id=new_payment_id())
                elif action == "forged":
                    sim.notify(c, "success", bad_signature=True)
                return self.redirect("/")
            self.not_found()

    return Handler


def serve(merchant_id: str, merchant_secret: str, host="127.0.0.1", port=9090) -> None:
    sim = Simulator(merchant_id, merchant_secret)
    httpd = ThreadingHTTPServer((host, port), make_handler(sim))
    shown = "localhost" if host in ("127.0.0.1", "0.0.0.0") else host
    print(f"payhere-sim checkout running at http://{shown}:{port}\n"
          f"Post your checkout form to http://{shown}:{port}/pay/checkout (merchant ID {merchant_id}).\n"
          "Ctrl+C to stop.", flush=True)
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
