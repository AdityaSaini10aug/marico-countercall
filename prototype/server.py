#!/usr/bin/env python3
"""Local-only simulated telecalling desk. Run python3 server.py."""

import argparse
import json
import math
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlparse

from agent import process, start
from catalog import (
    LANGUAGES,
    PREFS,
    PRODUCTS,
    RETAILERS,
    ROOT,
    connect,
    initialise,
    stock_map,
)
from language import Model


class Handler(BaseHTTPRequestHandler):
    def send(self, code, body, mime="application/json; charset=utf-8"):
        data = (
            body
            if isinstance(body, bytes)
            else json.dumps(body, ensure_ascii=False).encode()
        )
        self.send_response(code)
        self.send_header("Content-Type", mime)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        path = urlparse(self.path).path
        with connect(self.server.db_path) as db:
            if path == "/api/setup":
                suppressed = {
                    r[0] for r in db.execute("SELECT retailer FROM suppressed")
                }
                return self.send(
                    200,
                    {
                        "retailers": [
                            dict(r, window=PREFS[k]["window"], dnc=k in suppressed)
                            for k, r in RETAILERS.items()
                        ],
                        "languages": LANGUAGES,
                        "mode": self.server.model.mode,
                    },
                )
            if path.startswith("/api/calls/"):
                row = db.execute(
                    "SELECT state FROM calls WHERE id=?", (path.rsplit("/", 1)[-1],)
                ).fetchone()
                if row:
                    return self.send(200, self.view(db, json.loads(row[0])))
                return self.send(404, {"error": "Call not found."})
        asset = {
            "/": ("index.html", "text/html"),
            "/app.js": ("app.js", "application/javascript"),
            "/style.css": ("style.css", "text/css"),
        }.get(path)
        if asset:
            return self.send(
                200,
                (ROOT / "web" / asset[0]).read_bytes(),
                asset[1] + "; charset=utf-8",
            )
        self.send(404, {"error": "Not found."})

    def view(self, db, s):
        stocks = stock_map(db, s["retailer"])
        return dict(
            s,
            products=[dict(p, stock=stocks[k]) for k, p in PRODUCTS.items()],
            events=[
                dict(r)
                for r in db.execute(
                    "SELECT kind,detail FROM events WHERE call_id=? ORDER BY id",
                    (s["id"],),
                )
            ],
        )

    def do_POST(self):
        try:
            # Browser access is same-origin. The server only binds loopback.
            origin = self.headers.get("Origin")
            if origin and urlparse(origin).netloc != self.headers.get("Host"):
                return self.send(403, {"error": "Use the local demo page."})
            if self.headers.get_content_type() != "application/json":
                return self.send(415, {"error": "Use JSON."})
            size = int(self.headers.get("Content-Length", "0"))
            if not 0 < size <= 10000:
                return self.send(400, {"error": "Invalid request size."})
            data = json.loads(self.rfile.read(size))
            if not isinstance(data, dict):
                raise TypeError("Expected an object.")
            with connect(self.server.db_path) as db:
                db.execute("BEGIN IMMEDIATE")
                if self.path == "/api/start":
                    s = start(
                        db,
                        str(data.get("retailer", "")),
                        data.get("clock"),
                        self.server.model,
                    )
                    s.update(version=0, last_request=None)
                elif self.path == "/api/turn":
                    row = db.execute(
                        "SELECT state FROM calls WHERE id=?", (str(data.get("id", "")),)
                    ).fetchone()
                    if not row:
                        return self.send(404, {"error": "Call not found."})
                    s = json.loads(row[0])
                    token = data.get("request")
                    if not isinstance(token, str) or not 1 <= len(token) <= 80:
                        raise ValueError("A turn identifier is required.")
                    if token == s["last_request"]:
                        return self.send(200, self.view(db, s))
                    if data.get("version") != s["version"]:
                        return self.send(
                            409,
                            {"error": "Call updated. Refresh to see the latest turn."},
                        )
                    confidence = data.get("confidence")
                    if confidence is not None and (
                        not isinstance(confidence, (int, float))
                        or not math.isfinite(confidence)
                        or not 0 <= confidence <= 1
                    ):
                        raise ValueError("Invalid confidence.")
                    process(db, s, data.get("text"), self.server.model, confidence)
                    s["version"] += 1
                    s["last_request"] = token
                else:
                    return self.send(404, {"error": "Not found."})
                db.execute(
                    "INSERT OR REPLACE INTO calls VALUES (?,?)",
                    (s["id"], json.dumps(s, ensure_ascii=False)),
                )
                view = self.view(db, s)
                db.commit()
            self.send(200, view)
        except (ValueError, TypeError) as err:
            self.send(400, {"error": str(err)})


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--port", type=int, default=8765)
    parser.add_argument("--state-dir", type=Path, default=ROOT / ".local")
    parser.add_argument("--open", action="store_true")
    args = parser.parse_args()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    server = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    server.db_path = args.state_dir / "calls.sqlite3"
    initialise(server.db_path)
    server.model = Model(args.state_dir)
    url = f"http://127.0.0.1:{server.server_port}/"
    print(f"{server.model.mode}\nOpen {url}", flush=True)
    if args.open:
        import webbrowser

        threading.Timer(0.3, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
