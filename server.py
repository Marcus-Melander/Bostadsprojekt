"""Local server for Stockholm Bostadsplan.

Serves the planner at http://localhost:8765, keeps your plan in data/plan.json
and hands the planner the listings the Booli scraper saved. It listens on this
computer only.

    .venv/bin/python server.py
"""
import json
import os
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

ROOT = Path(__file__).resolve().parent
DATA = ROOT / "data"
PORT = int(os.environ.get("BOSTADSPLAN_PORT", "8765"))
MAX_BODY = 5 * 1024 * 1024


def read(path, default=None):
    try:
        return path.read_bytes()
    except OSError:
        return default


def write_json(path, data):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=1), "utf-8")
    os.replace(tmp, path)


def scraper_running():
    lock = DATA / ".scrape.lock"
    if not lock.exists():
        return False
    try:
        os.kill(int(lock.read_text()), 0)
        return True
    except (ValueError, OSError):
        return False


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body=b"", ctype="application/json"):
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def json(self, code, obj):
        self.send(code, json.dumps(obj, ensure_ascii=False).encode("utf-8"))

    def do_GET(self):
        if not self.host_ok():
            return self.send(421, b"Use http://localhost:%d" % PORT, "text/plain")
        path = self.path.split("?")[0]
        if path in ("/", "/index.html"):
            return self.send(200, read(ROOT / "index.html", b""), "text/html; charset=utf-8")
        if path == "/areas.geojson":
            return self.send(200, read(ROOT / "areas.geojson", b"{}"), "application/geo+json")
        if path == "/api/plan":
            body = read(DATA / "plan.json")
            return self.send(200, body) if body else self.json(404, {"error": "no plan yet"})
        if path == "/api/listings":
            return self.send(200, read(DATA / "listings.json", b'{"items":{},"done":{},"lastRun":null}'))
        if path == "/api/status":
            s = json.loads(read(DATA / "status.json", b"{}") or b"{}")
            s["running"] = scraper_running()
            s["scheduled"] = (Path.home() / "Library/LaunchAgents/se.bostadsplan.scrape.plist").exists()
            if s.get("state") == "running" and not s["running"]:
                s["state"] = "error"
                s["msg"] = "The last run stopped without finishing. See data/scraper.log."
            return self.json(200, s)
        self.send(404, b"Not found", "text/plain")

    def do_PUT(self):
        if self.path != "/api/plan":
            return self.send(404, b"Not found", "text/plain")
        if not self.same_origin():
            return self.json(403, {"error": "wrong origin"})
        n = int(self.headers.get("Content-Length") or 0)
        if n <= 0 or n > MAX_BODY:
            return self.json(413, {"error": "bad size"})
        try:
            body = json.loads(self.rfile.read(n))
        except ValueError:
            return self.json(400, {"error": "not JSON"})
        DATA.mkdir(exist_ok=True)
        if isinstance(body.get("plan"), dict):
            write_json(DATA / "plan.json", body["plan"])
        if isinstance(body.get("scrape"), dict):
            write_json(DATA / "scrape.json", body["scrape"])
        self.json(200, {"ok": True})

    def do_POST(self):
        if self.path != "/api/scrape":
            return self.send(404, b"Not found", "text/plain")
        if not self.same_origin():
            return self.json(403, {"error": "wrong origin"})
        if scraper_running():
            return self.json(409, {"error": "already running"})
        DATA.mkdir(exist_ok=True)
        log = open(DATA / "scraper.out", "ab")
        subprocess.Popen([sys.executable, str(ROOT / "scraper" / "finder.py")], cwd=ROOT,
                         stdout=log, stderr=log, start_new_session=True)
        self.json(202, {"ok": True})

    def host_ok(self):
        # Refuse pages that reach this server under another name (DNS rebinding)
        return (self.headers.get("Host") or "") in (f"localhost:{PORT}", f"127.0.0.1:{PORT}")

    def same_origin(self):
        # Only the planner itself may change things; other sites open in your browser may not
        origin = self.headers.get("Origin")
        return self.host_ok() and origin is None or origin in (f"http://localhost:{PORT}", f"http://127.0.0.1:{PORT}")

    def log_message(self, fmt, *args):
        pass


if __name__ == "__main__":
    DATA.mkdir(exist_ok=True)
    print(f"Bostadsplan on http://localhost:{PORT}")
    ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
