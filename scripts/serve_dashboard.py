#!/usr/bin/env python3
"""
Dev server for the EcoLink dashboard.
Reads AMAZON_LOCATION_API_KEY from .env and injects it into index.html
as a <meta> tag so config.js can read it without the key being committed.

Usage:
    python scripts/serve_dashboard.py
Then open: http://localhost:8000/dashboard/index.html
"""
import os, re
from http.server import HTTPServer, SimpleHTTPRequestHandler
from pathlib import Path

ROOT = Path(__file__).parent.parent

def load_env():
    env_file = ROOT / '.env'
    if not env_file.exists():
        return {}
    vals = {}
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if line and not line.startswith('#') and '=' in line:
            k, _, v = line.partition('=')
            vals[k.strip()] = v.strip()
    return vals

ENV = load_env()
API_KEY = ENV.get('AMAZON_LOCATION_API_KEY', '')

META_TAG = f'<meta name="amazon-location-key" content="{API_KEY}">'

class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'frontend'), **kwargs)

    def do_GET(self):
        if self.path.rstrip('/') in ('', '/dashboard', '/dashboard/index.html', '/dashboard/'):
            path = ROOT / 'frontend' / 'dashboard' / 'index.html'
            content = path.read_text()
            content = content.replace('</head>', f'  {META_TAG}\n</head>', 1)
            encoded = content.encode()
            self.send_response(200)
            self.send_header('Content-Type', 'text/html; charset=utf-8')
            self.send_header('Content-Length', str(len(encoded)))
            self.end_headers()
            self.wfile.write(encoded)
        else:
            super().do_GET()

    def log_message(self, fmt, *args):
        pass  # suppress per-request noise

if __name__ == '__main__':
    port = 8000
    print(f'EcoLink dashboard → http://localhost:{port}/dashboard/index.html')
    print(f'Amazon Location key: {"set ✓" if API_KEY else "NOT SET — add AMAZON_LOCATION_API_KEY to .env"}')
    HTTPServer(('', port), Handler).serve_forever()
