"""Read-only loopback telemetry server; no third-party web dependencies."""
from collections import deque
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json
from pathlib import Path
import threading
import time
from urllib.parse import urlsplit, parse_qs
from project_info import project_info

ROOT = Path(__file__).resolve().parent


class ActivityMonitor:
    def __init__(self, port=8470, html_file="activity_monitor.html", command_callback=None, serve_http=True):
        self.lock = threading.Lock()
        self.started = time.perf_counter()
        self.state = {"status": "loading", "project":project_info(), "metadata": {}, "latest": None,
                      "history": [], "events": [], "wall_seconds": 0, "revision": 0}
        self.history = deque(maxlen=250)
        self.events = deque(maxlen=50000)
        monitor = self
        if not serve_http:
            return

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self):
                request_path = urlsplit(self.path).path
                if self.path in ("/", "/brain"):
                    data = (ROOT / (html_file if self.path == "/" else "activity_monitor.html")).read_bytes()
                    content_type = "text/html; charset=utf-8"
                elif self.path == "/api/state":
                    data = json.dumps(monitor.snapshot(), ensure_ascii=False).encode("utf-8")
                    content_type = "application/json; charset=utf-8"
                elif request_path == "/licenses":
                    data = (ROOT / "web" / "许可与来源.html").read_bytes()
                    content_type = "text/html; charset=utf-8"
                elif request_path in ('/assets/annotations.js', '/assets/annotations.css',
                                       '/assets/neural_activity.js', '/assets/neural_activity.css',
                                       '/assets/neural_activity_core.js', '/assets/activity_catalog.json'):
                    data = (ROOT / request_path.lstrip('/').replace('assets/', 'web/', 1)).read_bytes()
                    content_type = ('text/javascript; charset=utf-8' if request_path.endswith(('.js', '.mjs')) else
                                    'application/json; charset=utf-8' if request_path.endswith('.json') else
                                    'text/css; charset=utf-8')
                elif request_path.startswith('/api/annotations/'):
                    from neuron_annotations import annotations
                    from prepare_annotations import REPORT_FILE, DATA
                    store = annotations()
                    content_type = 'application/json; charset=utf-8'
                    if request_path == '/api/annotations/summary':
                        result = store.summary
                    elif request_path == '/api/annotations/search':
                        query = parse_qs(urlsplit(self.path).query)
                        param = lambda name, default='': query.get(name, [default])[0]
                        try:
                            limit, offset = int(param('limit', '20')), int(param('offset', '0'))
                            unknown = param('unknown_only', 'false').lower()
                            if not 1 <= limit <= 100 or not 0 <= offset <= 200000 or unknown not in ('true', 'false'):
                                raise ValueError('Invalid search parameters')
                            if len(param('q')) > 120 or len(param('super_class')) > 80 or len(param('nt')) > 80:
                                raise ValueError('Search string too long')
                            result = store.search(param('q'), param('super_class'), param('nt'), unknown == 'true', limit, offset)
                        except ValueError as exc:
                            self.send_error(400, str(exc))
                            return
                    elif request_path.startswith('/api/annotations/neuron/'):
                        result = store.get(request_path.rsplit('/', 1)[-1])
                        if result is None:
                            self.send_error(404, 'ID is not in the current brain model')
                            return
                    elif request_path in ('/api/annotations/report', '/api/annotations/unknown.csv'):
                        path = REPORT_FILE if request_path.endswith('report') else DATA / 'unknown_neuron_types.csv'
                        if not path.exists():
                            self.send_error(404)
                            return
                        data = path.read_bytes()
                        if request_path.endswith('.csv'):
                            content_type = 'text/csv; charset=utf-8'
                    else:
                        self.send_error(404)
                        return
                    if request_path not in ('/api/annotations/report', '/api/annotations/unknown.csv'):
                        data = json.dumps(result, ensure_ascii=False).encode('utf-8')
                elif self.path == "/favicon.ico":
                    self.send_response(204)
                    self.end_headers()
                    return
                else:
                    self.send_error(404)
                    return
                self.send_response(200)
                self.send_header("Content-Type", content_type)
                self.send_header("Cache-Control", "no-store")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                try:
                    self.wfile.write(data)
                except (BrokenPipeError, ConnectionResetError):
                    pass

            def log_message(self, *_):
                pass

            def do_POST(self):
                if self.path != "/api/command" or command_callback is None:
                    self.send_error(404)
                    return
                if self.headers.get("Origin") not in (None, monitor.url.rstrip("/")):
                    self.send_error(403)
                    return
                try:
                    length = int(self.headers.get("Content-Length", "0"))
                    if not 0 < length <= 4096:
                        raise ValueError("Invalid command size")
                    message = json.loads(self.rfile.read(length))
                    command_callback(message)
                    body = b'{"accepted":true}'
                    self.send_response(202)
                except (ValueError, KeyError, TypeError) as exc:
                    body = json.dumps({"error":str(exc)}).encode()
                    self.send_response(400)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length",str(len(body)))
                self.end_headers()
                self.wfile.write(body)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_port}/"
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def habitat_update(self, habitat):
        with self.lock:
            self.state["habitat"] = habitat
            self.state["revision"] += 1

    def ready(self, metadata):
        with self.lock:
            self.state["metadata"] = metadata
            self.state["status"] = "running"
            self.state["revision"] += 1

    def update(self, chunk, habitat=None):
        with self.lock:
            self.state["latest"] = chunk
            self.history.append({key: chunk[key] for key in
                                 ("brain_time_ms", "chunk_ms", "p9_hz", "chunk_spikes", "active_neurons")})
            self.events.extend(chunk["activity"]["events"])
            cutoff = chunk["brain_time_ms"] - 1000
            while self.events and self.events[0][0] < cutoff:
                self.events.popleft()
            if habitat is not None:
                self.state["habitat"] = habitat
            self.state["revision"] += 1

    def finish(self, status="finished"):
        with self.lock:
            self.state["status"] = status
            self.state["revision"] += 1

    def snapshot(self):
        with self.lock:
            return {**self.state, "history": list(self.history), "events": list(self.events),
                    "wall_seconds": time.perf_counter() - self.started}

    def close(self):
        self.server.shutdown()
        self.server.server_close()
