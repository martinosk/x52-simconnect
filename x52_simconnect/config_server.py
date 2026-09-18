"""The config UI (spec 06): a page on http://127.0.0.1:8052 served by the running bridge, to edit the mode 1
pages and choose what the mode 3 event log shows. Standard library only.

Two halves, so the HTTP threads never touch the apps:

    store = ConfigStore(path, known=...)        # the current Config, its file, and a slot for a new one
    ConfigServer(store, port=8052).start()      # daemon threads; GET / is the page, /api/* is JSON
    ...
    config = store.take()                       # main loop, every tick: a new Config to apply, or None
    store.live = {...}                          # main loop, every tick: what the page mirrors

``store.submit`` (from the UI) validates, writes the file and fills the slot; ``store.take`` also notices a
file edited by hand. A broken file never replaces a working config: it is reported and ignored.

Local only: the server binds to 127.0.0.1, refuses requests whose Host is not a loopback name (DNS rebinding)
and only accepts JSON bodies (a form on another site cannot post one without a CORS preflight, which is
never answered).
"""

import json
import logging
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from . import config as cfg
from . import templates
from .sources import demo_values

log = logging.getLogger(__name__)

DEFAULT_PORT = 8052
FILE_CHECK_SECONDS = 2
MAX_BODY = 256 * 1024
PAGE = Path(__file__).with_name("config_ui.html")


class ConfigStore:
    def __init__(self, path, known=None, units=None, clock=time.time):
        self.path = Path(path)
        self.known = known  # known(simvar) -> bool, or None when the SimVar table is not importable
        self.units = units or (lambda name: None)  # units(simvar) -> "Feet" or None
        self.live = {}  # set by the main loop: mfd lines, mode, values, recent key events
        self.file_error = ""  # why the file on disk is not in use, for the UI
        self._clock = clock
        self._lock = threading.Lock()
        self._pending = None
        self._next_check = 0.0
        self._mtime = self._stat()
        try:
            self.config = cfg.load(self.path, known)
        except cfg.ConfigError as e:
            self.config = cfg.Config()
            self.file_error = str(e)
            log.warning("config %s ignored: %s", self.path, e)

    def _stat(self):
        try:
            return self.path.stat().st_mtime_ns
        except OSError:
            return None

    def submit(self, data):
        """Validate ``data`` (a dict as ``config.to_dict`` makes), save it and queue it for the main loop.
        Raises ``config.ConfigError``."""
        new = cfg.from_dict(data, self.known)
        with self._lock:
            cfg.save(self.path, new)
            self._mtime = self._stat()
            self.config = self._pending = new
            self.file_error = ""
        return new

    def take(self):
        """For the main loop: the config to switch to, or None. Looks at the file every couple of seconds."""
        now = self._clock()
        if now >= self._next_check:
            self._next_check = now + FILE_CHECK_SECONDS
            self._reload_if_edited()
        with self._lock:
            new, self._pending = self._pending, None
        return new

    def _reload_if_edited(self):
        mtime = self._stat()
        if mtime == self._mtime:
            return
        self._mtime = mtime
        try:
            new = cfg.load(self.path, self.known)
        except (cfg.ConfigError, OSError) as e:
            self.file_error = str(e)
            log.warning("config %s ignored: %s", self.path, e)
            return
        with self._lock:
            self.file_error = ""
            if new != self.config:
                self.config = self._pending = new

    # ------------------------------------------------------------------ what the page asks for
    def state(self):
        return {
            "config": cfg.to_dict(self.config),
            "defaults": cfg.to_dict(cfg.Config()),
            "path": str(self.path),
            "file_error": self.file_error,
            "rules": cfg.rule_catalogue(),
            "fields": [
                {"group": group, "label": label, "template": text, "unit": self.units(templates.variables(text)[0])}
                for group, label, text in cfg.FIELDS
            ],
            "filters": [{"name": name, "help": entry[1]} for name, entry in templates.FILTERS.items()],
            "limits": {"title": cfg.TITLE_LEN, "pages": cfg.MAX_PAGES, "line": templates.LINE_LEN},
        }

    def preview(self, pages):
        """Render ``[{"lines": [...]}]`` with the live values (demo values without a sim): per line the
        text, the problem if there is one, and the unit of every SimVar in it."""
        live = self.live.get("values")
        values = dict(demo_values(30), **{k: v for k, v in (live or {}).items() if v is not None})
        out = []
        for page in pages[: cfg.MAX_PAGES]:
            lines = []
            for line in (list(page.get("lines", [])) + ["", "", ""])[:3]:
                line = line if isinstance(line, str) else ""
                error = templates.check(line, self.known)
                try:
                    text = templates.render(line, values)
                    units = {name: self.units(name) for name in templates.variables(line)}
                except templates.TemplateError:  # does not parse; ``error`` says why
                    text, units = "", {}
                lines.append({"text": text, "error": error, "units": units})
            out.append({"lines": lines})
        return {"pages": out, "live": live is not None}


class _Handler(BaseHTTPRequestHandler):
    server_version = "x52-simconnect"
    store: ConfigStore  # set on the subclass ConfigServer makes

    def log_message(self, fmt, *args):  # the bridge's console is for the bridge
        log.debug("config ui: " + fmt, *args)

    def _local(self):
        host = (self.headers.get("Host") or "").rsplit(":", 1)[0].strip("[]").lower()
        if host in ("127.0.0.1", "localhost", "::1"):
            return True
        self._json(403, {"error": "this page is only served to the local machine"})
        return False

    def _json(self, status, body):
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):  # noqa: N802 - http.server's naming
        if not self._local():
            return
        if self.path in ("/", "/index.html"):
            data = PAGE.read_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)
        elif self.path == "/api/state":
            self._json(200, self.store.state())
        elif self.path == "/api/live":
            live = dict(self.store.live)
            live.pop("values", None)
            self._json(200, live)
        else:
            self._json(404, {"error": "no such page"})

    def do_POST(self):  # noqa: N802
        if not self._local():
            return
        if (self.headers.get("Content-Type") or "").split(";")[0].strip() != "application/json":
            return self._json(415, {"error": "send application/json"})
        length = int(self.headers.get("Content-Length") or 0)
        if not 0 < length <= MAX_BODY:
            return self._json(413, {"error": "body missing or too large"})
        try:
            body = json.loads(self.rfile.read(length))
        except ValueError:
            return self._json(400, {"error": "not JSON"})
        if self.path == "/api/config":
            try:
                new = self.store.submit(body)
            except cfg.ConfigError as e:
                return self._json(400, {"errors": e.errors})
            except OSError as e:
                return self._json(500, {"errors": [{"path": "", "message": f"could not write the file: {e}"}]})
            return self._json(200, {"config": cfg.to_dict(new)})
        if self.path == "/api/preview":
            pages = body.get("pages") if isinstance(body, dict) else None
            return self._json(200, self.store.preview(pages if isinstance(pages, list) else []))
        return self._json(404, {"error": "no such page"})


class ConfigServer:
    def __init__(self, store, port=DEFAULT_PORT, host="127.0.0.1"):
        handler = type("Handler", (_Handler,), {"store": store})
        self.httpd = ThreadingHTTPServer((host, port), handler)
        self.httpd.daemon_threads = True
        self.url = f"http://{host}:{self.httpd.server_address[1]}/"
        self._thread = threading.Thread(target=self.httpd.serve_forever, name="config-ui", daemon=True)

    def start(self):
        self._thread.start()
        return self

    def stop(self):
        self.httpd.shutdown()
        self.httpd.server_close()
