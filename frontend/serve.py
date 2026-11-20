"""Static dev server with caching off, so edits always show on reload:  python3 serve.py  (port 3000)"""
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer

class H(SimpleHTTPRequestHandler):
    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

ThreadingHTTPServer(("", 3000), H).serve_forever()
