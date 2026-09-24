import http.client
import threading
import unittest
from email.message import Message
from unittest import mock

import server
from gpu_watch.security import is_same_origin, parse_allowed_networks, SlidingWindowRateLimiter


class V2SecurityTests(unittest.TestCase):
    def test_malformed_origin_is_rejected_without_parser_exception(self):
        for origin in ["http://[::1", "https://[not-an-ip]", "https://example.test:bad", "https://example.test:99999"]:
            with self.subTest(origin=origin):
                self.assertFalse(is_same_origin({"Host": "example.test", "Origin": origin}))

    def test_url_paths_and_control_characters_are_not_serialized_origins(self):
        for origin in ["https://example.test/path", "https://example.test/", "https://example.test?x=1",
                       "https://example.test#fragment", "https://exa\nmple.test", "https://exa\tmple.test"]:
            with self.subTest(origin=origin):
                self.assertFalse(is_same_origin({"Host": "example.test", "Origin": origin}))

    def test_multiple_origin_headers_are_rejected(self):
        headers = Message()
        headers["Host"] = "example.test"
        headers["Origin"] = "https://example.test"
        headers["Origin"] = "https://evil.test"
        self.assertFalse(is_same_origin(headers))

    def test_normal_origin_forms_and_originless_operator_requests_remain_supported(self):
        self.assertTrue(is_same_origin({"Host": "example.test", "Origin": "https://example.test"}))
        self.assertTrue(is_same_origin({"Host": "example.test:8787", "Origin": "http://example.test:8787"}))
        self.assertTrue(is_same_origin({"Host": "[::1]:8787", "Origin": "http://[::1]:8787"}))
        self.assertTrue(is_same_origin({"Host": "example.test"}))
        self.assertFalse(is_same_origin({"Host": "example.test", "Origin": "null"}))
        self.assertFalse(is_same_origin({"Host": "example.test", "Origin": "https://evil.test"}))

    def test_malformed_origin_does_not_reach_rate_limiter_or_mutation(self):
        handler = object.__new__(server.Handler)
        handler.headers = {"Host": "example.test", "Origin": "http://[::1"}
        handler.client_ip = lambda: "127.0.0.1"
        handler.send_json = mock.Mock()
        handler.rate_limiter = mock.Mock()
        self.assertFalse(handler.mutation_allowed("announcement-create", 5, 600))
        self.assertEqual(handler.send_json.call_args.args[1], 403)
        handler.rate_limiter.allow.assert_not_called()

    def test_malformed_request_targets_return_http_errors_without_handler_tracebacks(self):
        import socket
        import sys
        config = {"allowed_networks":parse_allowed_networks("127.0.0.0/8"),
                  "trusted_proxy_networks":parse_allowed_networks("127.0.0.0/8"),
                  "http_read_timeout_seconds":2}
        handler_class = type("AuditHandler", (server.Handler,), {
            "config":config,"rate_limiter":SlidingWindowRateLimiter(),
            "log_message":server.Handler.log_message})
        httpd = server.DashboardHTTPServer(("127.0.0.1",0), handler_class)
        port = httpd.server_address[1]
        config["allowed_hosts"] = {f"127.0.0.1:{port}"}
        failures = []
        httpd.handle_error = lambda *args: failures.append(type(sys.exc_info()[1]).__name__)
        worker = threading.Thread(target=httpd.serve_forever,daemon=True)
        worker.start()
        host = f"Host: 127.0.0.1:{port}\r\nConnection: close\r\n"
        cases = [
            ("GET http://[::1 HTTP/1.1\r\n" + host + "\r\n", 400),
            ("GET http://[bad] HTTP/1.1\r\n" + host + "\r\n", 400),
            ("GET http://[::1 NOTHTTP\r\n" + host + "\r\n", 400),
            ("GET /favicon.ico HTTP/nonsense\r\n" + host + "\r\n", 400),
            ("GET http://[::1 HTTP/1.1\r\n" + host + "".join(f"X-Test-{i}: 1\r\n" for i in range(101)) + "\r\n", 431),
            ("GET /favicon.ico HTTP/1.1\r\n" + host + "\r\n", 204),
        ]
        try:
            for raw, expected in cases:
                with self.subTest(request=raw.splitlines()[0]):
                    with socket.create_connection(("127.0.0.1",port),timeout=3) as connection:
                        connection.sendall(raw.encode("ascii"))
                        response = b""
                        while True:
                            chunk = connection.recv(4096)
                            if not chunk:
                                break
                            response += chunk
                    # Malformed protocol versions receive the base handler's
                    # HTTP/0.9-style error body; valid versions include status.
                    if response.startswith(b"HTTP/"):
                        self.assertEqual(int(response.split(b" ",2)[1]), expected)
                    else:
                        self.assertIn(f"Error code: {expected}".encode(), response)
            self.assertEqual(failures, [])
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)

    def test_malformed_origin_receives_http403_and_server_keeps_serving(self):
        config = {"allowed_networks":parse_allowed_networks("127.0.0.0/8"),
                  "trusted_proxy_networks":parse_allowed_networks("127.0.0.0/8"),
                  "http_read_timeout_seconds":2}
        handler_class = type("AuditHandler", (server.Handler,), {
            "config":config,"rate_limiter":SlidingWindowRateLimiter(),
            "log_message":lambda self,*args:None})
        httpd = server.DashboardHTTPServer(("127.0.0.1",0), handler_class)
        port = httpd.server_address[1]
        config["allowed_hosts"] = {f"127.0.0.1:{port}"}
        worker = threading.Thread(target=httpd.serve_forever,daemon=True)
        worker.start()
        try:
            connection = http.client.HTTPConnection("127.0.0.1",port,timeout=3)
            connection.request("POST","/api/announcements",body="{}",
                headers={"Content-Type":"application/json","Origin":"http://[::1"})
            response = connection.getresponse()
            self.assertEqual(response.status,403)
            response.read();connection.close()
            connection = http.client.HTTPConnection("127.0.0.1",port,timeout=3)
            connection.request("GET","/favicon.ico")
            response = connection.getresponse()
            self.assertEqual(response.status,204)
            response.read();connection.close()
        finally:
            httpd.shutdown();httpd.server_close();worker.join(3)


if __name__ == "__main__":
    unittest.main()
