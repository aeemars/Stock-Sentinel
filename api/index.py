"""Vercel Serverless Function entrypoint for Stock Sentinel FastAPI."""
import sys
import os
from pathlib import Path

# Explicitly anchor working directory and python path to project root
ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))
try:
    os.chdir(str(ROOT_DIR))
except Exception:
    pass

import urllib.parse
from main import app as _app

class VercelPathRewriteASGIApp:
    """ASGI middleware that unwraps Vercel's internal rewrite destination (/api/index.py)
    back to the client's actual requested route using Vercel proxy headers or query params."""

    def __init__(self, asgi_app):
        self.asgi_app = asgi_app

    def __getattr__(self, name):
        return getattr(self.asgi_app, name)

    async def __call__(self, scope, receive, send):
        if scope.get("type") == "http":
            current_path = scope.get("path", "")
            # If Vercel rewrote the incoming request to the serverless function path
            if current_path in ("/api/index.py", "/api/index", "/api", "/api/"):
                headers = dict(scope.get("headers", []))
                raw_uri = (
                    headers.get(b"x-forwarded-uri")
                    or headers.get(b"x-matched-path")
                    or headers.get(b"x-now-route-matches")
                )

                # Check for query param ?path= if headers are not set
                query_string = scope.get("query_string", b"").decode("utf-8", "ignore")
                path_from_query = None
                if "path=" in query_string:
                    params = urllib.parse.parse_qs(query_string)
                    path_from_query = params.get("path", [None])[0]

                target_path = None
                if raw_uri:
                    target_path = urllib.parse.urlparse(raw_uri.decode("utf-8", "ignore")).path
                elif path_from_query:
                    target_path = path_from_query
                    filtered_qs = [
                        qc for qc in query_string.split("&")
                        if not qc.startswith("path=") and qc
                    ]
                    scope["query_string"] = "&".join(filtered_qs).encode("utf-8")

                if target_path and target_path not in ("/api/index.py", "/api/index"):
                    scope["path"] = target_path
                else:
                    scope["path"] = "/"

        await self.asgi_app(scope, receive, send)

app = VercelPathRewriteASGIApp(_app)

