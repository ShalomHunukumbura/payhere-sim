import os
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

MERCHANT_ID, SECRET = "1234567", "demo-secret"
SHOP = Path(__file__).parent.parent / "examples" / "credits-shop"


def free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


class Recorder:
    """A notify_url that records what it receives and answers with a chosen status."""

    def __init__(self):
        self.requests = []
        self.status = 200
        recorder = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *args):
                pass

            def do_POST(self):
                body = self.rfile.read(int(self.headers["Content-Length"])).decode()
                recorder.requests.append((self.headers["Content-Type"], dict(urllib.parse.parse_qsl(body))))
                self.send_response(recorder.status)
                self.end_headers()
                self.wfile.write(b"recorded")

        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.httpd.server_address[1]}/notify"
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()


@pytest.fixture
def recorder():
    r = Recorder()
    yield r
    r.httpd.shutdown()


def start_shop(tmp_path, buggy: bool):
    port = free_port()
    env = dict(os.environ, SHOP_DB=str(tmp_path / f"shop-{port}.db"), SHOP_BUGGY="1" if buggy else "0",
               PAYHERE_MERCHANT_ID=MERCHANT_ID, PAYHERE_MERCHANT_SECRET=SECRET)
    proc = subprocess.Popen([sys.executable, "-m", "uvicorn", "--app-dir", str(SHOP), "app:app", "--port", str(port)],
                            env=env, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    base = f"http://127.0.0.1:{port}"
    for _ in range(100):
        try:
            urllib.request.urlopen(base + "/docs", timeout=1)
            break
        except OSError:
            time.sleep(0.1)
    else:
        proc.kill()
        raise RuntimeError("example shop didn't start")
    return proc, base


@pytest.fixture(scope="module")
def shops(tmp_path_factory):
    tmp = tmp_path_factory.mktemp("shops")
    buggy, buggy_url = start_shop(tmp, buggy=True)
    safe, safe_url = start_shop(tmp, buggy=False)
    yield {"buggy": buggy_url, "safe": safe_url}
    for p in (buggy, safe):
        p.terminate()
        p.wait()
