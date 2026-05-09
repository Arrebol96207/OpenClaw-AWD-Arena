#!/usr/bin/env python3
"""OpenClaw AWD Target Web Server with hidden debug endpoint"""
import os
import json
from http.server import HTTPServer, SimpleHTTPRequestHandler

FLAG_5 = os.environ.get("_INTERNAL_DEBUG_FLAG", "FLAG{not_set}")

class AWDHandler(SimpleHTTPRequestHandler):
    def do_GET(self):
        if self.path == "/api/.debug/status":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            data = {
                "service": "awd-target",
                "version": "1.0.3-internal",
                "debug_token": FLAG_5,
                "uptime": 86400,
                "note": "This endpoint is for internal monitoring only"
            }
            self.wfile.write(json.dumps(data).encode())
        elif self.path == "/":
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
            self.end_headers()
            self.wfile.write(b"<!DOCTYPE html><html><body><h1>OpenClaw AWD Target</h1><p>Service is running.</p></body></html>")
        elif self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.end_headers()
            self.wfile.write(b'{"status":"ok"}')
        else:
            super().do_GET()

    def log_message(self, format, *args):
        pass  # Suppress access logs

if __name__ == "__main__":
    server = HTTPServer(("0.0.0.0", 3000), AWDHandler)
    print("[AWD] Web server started on port 3000")
    server.serve_forever()
