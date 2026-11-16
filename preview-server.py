"""Serve only the public preview assets on the local network."""
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit, parse_qs
import mimetypes

ROOT = Path(__file__).resolve().parent
ALLOWED = {"/": "index.html", "/index.html": "index.html", "/styles.css": "styles.css", "/app.js": "app.js", "/common.js": "common.js", "/data/web-preview.json": "data/web-preview.json"}
for asset in (ROOT / "assets").iterdir():
    if asset.is_file() and asset.suffix in {".html", ".pdf", ".png", ".jpg", ".svg"}:
        ALLOWED["/assets/" + asset.name] = "assets/" + asset.name

class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        requested = urlsplit(self.path)
        if requested.path == "/article.html":
            query = parse_qs(requested.query)
            category = query.get("category", ["all"])[0]
            if category not in {"all", "code", "art", "design", "use", "business", "science", "human", "policy"}:
                category = "all"
            self.send_response(302)
            self.send_header("Location", "/?category=" + category)
            self.end_headers()
            return
        relative = ALLOWED.get(requested.path)
        if relative is None:
            self.send_error(404)
            return
        path = ROOT / relative
        body = path.read_bytes()
        mime = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        self.send_response(200)
        self.send_header("Content-Type", mime + "; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body)

if __name__ == "__main__":
    print("Local preview listening on port 8767 (Wi-Fi access enabled)", flush=True)
    ThreadingHTTPServer(("0.0.0.0", 8767), Handler).serve_forever()
