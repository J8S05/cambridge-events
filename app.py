#!/usr/bin/env python3
"""
Run:  python3 app.py        -> opens http://localhost:8765
      python3 app.py --port 9000 --no-open

Serves the calendar UI and keeps events fresh: re-scrapes on start if the data
is older than 6 hours, and whenever you press "Refresh" in the app.
"""
import argparse
import json
import threading
import time
import uuid
import webbrowser
from urllib.parse import parse_qs, urlparse
from datetime import datetime
from functools import partial
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import scraper

HERE = Path(__file__).resolve().parent
DATA = HERE / "events.json"
MANUAL = HERE / "termcards.json"
STALE_SECONDS = 6 * 3600
state = {"running": False, "log": [], "last": None}
lock = threading.Lock()


def do_refresh():
    with lock:
        if state["running"]:
            return False
        state["running"], state["log"] = True, []

    def log(msg):
        print(msg, flush=True)
        state["log"].append(msg)

    def work():
        try:
            scraper.run(log)
            state["last"] = datetime.now().isoformat(timespec="minutes")
        except Exception as e:  # noqa
            log(f"Refresh failed: {e}")
        finally:
            state["running"] = False
    threading.Thread(target=work, daemon=True).start()
    return True


class Handler(SimpleHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _json(self, obj, code=200):
        body = json.dumps(obj).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path.startswith("/api/status"):
            return self._json({"running": state["running"], "log": state["log"][-8:]})
        if self.path.startswith("/events.json"):
            self.send_response(200)
            body = DATA.read_bytes() if DATA.exists() else b'{"events":[]}'
            self.send_header("Content-Type", "application/json")
            self.send_header("Cache-Control", "no-store")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)
            return
        return super().do_GET()

    def _body(self):
        n = int(self.headers.get("Content-Length") or 0)
        return json.loads(self.rfile.read(n) or b"{}")

    def do_POST(self):
        if self.path.startswith("/api/refresh"):
            return self._json({"started": do_refresh(), "running": True})
        if self.path.startswith("/api/manual"):
            ev = self._body()
            if not ev.get("title") or not ev.get("start"):
                return self._json({"error": "title and start are required"}, 400)
            keep = {k: str(ev.get(k) or "").strip() for k in
                    ("title", "start", "end", "venue", "speaker", "organiser", "url", "description", "booking")}
            if ev.get("access") in scraper.ACCESS_LABELS:
                keep["access"] = ev["access"]
            if ev.get("topics"):
                keep["topics"] = [t for t in ev["topics"] if t in scraper.TOPICS]
            keep["id"] = "manual-" + uuid.uuid4().hex[:10]
            with lock:
                items = json.loads(MANUAL.read_text()) if MANUAL.exists() else []
                items.append(keep)
                MANUAL.write_text(json.dumps(items, ensure_ascii=False, indent=1))
                scraper.refresh_manual()
            return self._json({"ok": True, "id": keep["id"]})
        self.send_error(404)

    def do_DELETE(self):
        if self.path.startswith("/api/manual"):
            q = parse_qs(urlparse(self.path).query)
            target = (q.get("id") or [""])[0]
            with lock:
                items = json.loads(MANUAL.read_text()) if MANUAL.exists() else []
                items = [i for i in items if i.get("id") != target]
                MANUAL.write_text(json.dumps(items, ensure_ascii=False, indent=1))
                scraper.refresh_manual()
            return self._json({"ok": True})
        self.send_error(404)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--port", type=int, default=8765)
    ap.add_argument("--no-open", action="store_true")
    args = ap.parse_args()
    if not DATA.exists() or time.time() - DATA.stat().st_mtime > STALE_SECONDS:
        print("Event data missing or older than 6h - fetching in the background...")
        do_refresh()
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), partial(Handler, directory=str(HERE)))
    url = f"http://localhost:{args.port}/"
    print(f"Cambridge Politics & Law calendar running at {url}  (Ctrl+C to stop)")
    if not args.no_open:
        threading.Timer(0.8, lambda: webbrowser.open(url)).start()
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
