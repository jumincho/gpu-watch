"""Regression checks for request authority and body integrity at the backend."""
import io
import socket
import threading
import unittest
from email.message import Message
import server
from gpu_watch.security import SlidingWindowRateLimiter, parse_allowed_networks

class RequestBoundaryTests(unittest.TestCase):
    def test_truncated_json_body_is_rejected_even_if_prefix_is_valid_json(self):
        handler = object.__new__(server.Handler)
        handler.headers = Message()
        handler.headers["Content-Type"] = "application/json"
        handler.headers["Content-Length"] = "5"
        handler.rfile = io.BytesIO(b"{}")
        with self.assertRaises(ValueError):
            handler.read_json_body()

    def test_duplicate_host_is_rejected_before_serving_even_when_first_is_allowed(self):
        config = {
            "allowed_networks": parse_allowed_networks("127.0.0.0/8"),
            "trusted_proxy_networks": parse_allowed_networks("127.0.0.0/8"),
            "http_read_timeout_seconds": 2,
        }
        handler = type("RequestBoundaryHandler", (server.Handler,), {
            "config": config, "rate_limiter": SlidingWindowRateLimiter(),
            "log_message": lambda self, *args: None,
        })
        httpd = server.DashboardHTTPServer(("127.0.0.1", 0), handler)
        port = httpd.server_address[1]
        authority = f"127.0.0.1:{port}"
        config["allowed_hosts"] = {authority}
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            for duplicate in ("attacker.invalid", authority):
                with self.subTest(duplicate=duplicate):
                    raw = (f"GET /favicon.ico HTTP/1.1\r\nHost: {authority}\r\n"
                           f"Host: {duplicate}\r\nConnection: close\r\n\r\n")
                    with socket.create_connection(("127.0.0.1", port), timeout=3) as conn:
                        conn.sendall(raw.encode("ascii"))
                        response = conn.recv(4096)
                    self.assertEqual(int(response.split(b" ", 2)[1]), 403)
        finally:
            httpd.shutdown()
            httpd.server_close()
            thread.join(3)

if __name__ == "__main__":
    unittest.main()
