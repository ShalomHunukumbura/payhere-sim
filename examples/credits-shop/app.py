"""A tiny shop that sells 100 credits for Rs. 1,000 through PayHere.

Run the safe version:   uvicorn app:app --port 8000
Run the buggy version:  SHOP_BUGGY=1 uvicorn app:app --port 8000

The buggy handler checks the signature and even skips orders that are already paid, so it
looks fine when you test it by hand. payhere-sim finds three bugs in it anyway.
"""

import hashlib
import os
import sqlite3
import tempfile
import time

from fastapi import FastAPI, Form, HTTPException, Request
from fastapi.responses import HTMLResponse, PlainTextResponse
from starlette.concurrency import run_in_threadpool

MERCHANT_ID = os.environ.get("PAYHERE_MERCHANT_ID", "1234567")
MERCHANT_SECRET = os.environ.get("PAYHERE_MERCHANT_SECRET", "demo-secret")
CHECKOUT_URL = os.environ.get("PAYHERE_CHECKOUT_URL", "http://localhost:9090/pay/checkout")
BUGGY = os.environ.get("SHOP_BUGGY") == "1"
DB = os.environ.get("SHOP_DB", os.path.join(tempfile.gettempdir(), f"credits-shop-{os.getpid()}.db"))
PRICE, CREDITS = "1000.00", 100

app = FastAPI(title="credits shop" + (" (buggy)" if BUGGY else ""))


def db():
    conn = sqlite3.connect(DB, timeout=10, isolation_level=None)  # autocommit; transactions are explicit
    conn.row_factory = sqlite3.Row
    return conn


with db() as conn:
    conn.executescript("""
        create table if not exists users (name text primary key, credits integer not null default 0);
        create table if not exists orders (id integer primary key, user text not null, amount text not null,
            currency text not null, status text not null default 'unpaid', payment_id text);
    """)


def md5_upper(text: str) -> str:
    return hashlib.md5(text.encode()).hexdigest().upper()


def signature_ok(f: dict) -> bool:
    expected = md5_upper(f["merchant_id"] + f["order_id"] + f["payhere_amount"] + f["payhere_currency"]
                         + f["status_code"] + md5_upper(MERCHANT_SECRET))
    return f["md5sig"] == expected


@app.post("/orders")
def create_order(user: str | None = None):
    """A fresh unpaid order. Without a user, each order gets its own new customer (handy for tests)."""
    user = user or f"customer-{time.time_ns()}"
    with db() as conn:
        conn.execute("insert or ignore into users (name) values (?)", (user,))
        order_id = conn.execute("insert into orders (user, amount, currency) values (?, ?, 'LKR')",
                                (user, PRICE)).lastrowid
    return {"order_id": str(order_id), "amount": PRICE, "currency": "LKR"}


@app.get("/orders/{order_id}")
def order_state(order_id: int):
    """What payhere-sim's --probe reads: the order and what the customer ended up with."""
    with db() as conn:
        row = conn.execute("select o.status, u.credits from orders o join users u on u.name = o.user "
                           "where o.id = ?", (order_id,)).fetchone()
    if not row:
        raise HTTPException(404, "no such order")
    return {"status": row["status"], "credits": row["credits"]}


def send_receipt_email(order_id):
    time.sleep(0.05)  # stands in for any slow call: email, invoice PDF, an API


@app.post("/payhere/notify", response_class=PlainTextResponse)
async def notify(request: Request):
    f = dict(await request.form())
    if not signature_ok(f):
        return PlainTextResponse("bad signature", status_code=400)
    await run_in_threadpool(buggy_notify if BUGGY else safe_notify, f)  # DB work off the event loop
    return "ok"


def buggy_notify(f):
    with db() as conn:
        order = conn.execute("select * from orders where id = ?", (f["order_id"],)).fetchone()
        status = f["status_code"]
        if status == "2":
            if order["status"] == "paid":  # looks idempotent...
                return
            send_receipt_email(order["id"])  # ...but two requests can both get here before either updates
            conn.execute("update orders set status = 'paid', payment_id = ? where id = ?", (f["payment_id"], order["id"]))
            conn.execute("update users set credits = credits + ? where name = ?", (CREDITS, order["user"]))
        elif status == "0":
            conn.execute("update orders set status = 'pending' where id = ?", (order["id"],))
        elif status in ("-1", "-2"):
            conn.execute("update orders set status = 'failed' where id = ?", (order["id"],))
        elif status == "-3":
            conn.execute("update orders set status = 'chargedback' where id = ?", (order["id"],))
            conn.execute("update users set credits = credits - ? where name = ?", (CREDITS, order["user"]))


def safe_notify(f):
    with db() as conn:
        order = conn.execute("select * from orders where id = ?", (f["order_id"],)).fetchone()
        if not order or f["payhere_amount"] != order["amount"] or f["payhere_currency"] != order["currency"]:
            return  # not a payment for this order: acknowledge it, change nothing
        status = f["status_code"]
        if status == "2":
            conn.execute("begin immediate")
            # one atomic step decides who fulfils the order; repeats and racers change no row
            paid = conn.execute("update orders set status = 'paid', payment_id = ? where id = ? and status != 'paid'",
                                (f["payment_id"], order["id"])).rowcount
            if paid:
                conn.execute("update users set credits = credits + ? where name = ?", (CREDITS, order["user"]))
            conn.execute("commit")
            if paid:
                send_receipt_email(order["id"])
        elif status == "0":
            conn.execute("update orders set status = 'pending' where id = ? and status = 'unpaid'", (order["id"],))
        elif status in ("-1", "-2"):
            conn.execute("update orders set status = 'failed' where id = ? and status in ('unpaid', 'pending')",
                         (order["id"],))
        elif status == "-3":
            conn.execute("begin immediate")
            reversed_ = conn.execute("update orders set status = 'chargedback' where id = ? and status = 'paid' "
                                     "and payment_id = ?", (order["id"], f["payment_id"])).rowcount
            if reversed_:
                conn.execute("update users set credits = credits - ? where name = ?", (CREDITS, order["user"]))
            conn.execute("commit")


# --- a checkout page, to try `payhere-sim serve` end to end --------------------


@app.get("/", response_class=HTMLResponse)
def shop(request: Request):
    order = create_order("alice")
    order_id, base = order["order_id"], str(request.base_url).rstrip("/")
    hash_ = md5_upper(MERCHANT_ID + order_id + PRICE + "LKR" + md5_upper(MERCHANT_SECRET))
    fields = dict(merchant_id=MERCHANT_ID, return_url=f"{base}/thanks/{order_id}", cancel_url=f"{base}/",
                  notify_url=f"{base}/payhere/notify", first_name="Alice", last_name="Perera",
                  email="alice@example.com", phone="0771234567", address="1 Galle Road", city="Colombo",
                  country="Sri Lanka", order_id=order_id, items="100 credits", currency="LKR", amount=PRICE,
                  hash=hash_)
    inputs = "".join(f'<input type="hidden" name="{k}" value="{v}">' for k, v in fields.items())
    return f"""<!doctype html><meta name="viewport" content="width=device-width,initial-scale=1">
<title>Credits shop</title><body style="font-family:system-ui;max-width:480px;margin:40px auto;padding:0 16px">
<h1>Credits shop{' (buggy)' if BUGGY else ''}</h1><p>Alice has {order_state(int(order_id))['credits']} credits.</p>
<form method="post" action="{CHECKOUT_URL}">{inputs}<button>Buy 100 credits for Rs. 1,000</button></form></body>"""


@app.get("/thanks/{order_id}", response_class=HTMLResponse)
def thanks(order_id: int):
    state = order_state(order_id)
    return f"""<!doctype html><title>Thanks</title><body style="font-family:system-ui;max-width:480px;margin:40px auto">
<h1>Order {order_id}: {state['status']}</h1><p>Alice now has {state['credits']} credits.</p><a href="/">Back to shop</a></body>"""
