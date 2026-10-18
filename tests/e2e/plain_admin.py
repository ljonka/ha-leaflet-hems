"""Create/remove a temporary admin user through the plain localhost interface.

This script is executed on a Leaflet device (127.0.0.1:2223, no TLS, no auth).
It always performs JSONRPC.Hello first, as required by nymead.
"""

import json
import socket
import sys

HOST = "127.0.0.1"
PORT = 2223


def request(sock, request_id, method, params=None):
    payload = {"id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    sock.sendall(json.dumps(payload).encode() + b"\n")
    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise RuntimeError("connection closed")
        buffer += chunk
    line, _, _ = buffer.partition(b"\n")
    return json.loads(line)


def main():
    action = sys.argv[1]
    username = sys.argv[2]
    password = sys.argv[3] if len(sys.argv) > 3 else ""

    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        hello = request(sock, 1, "JSONRPC.Hello", {"locale": "de_DE"})
        print("hello", hello.get("status"), hello.get("params", {}).get("name"))
        if action == "create":
            response = request(
                sock,
                2,
                "Users.CreateUser",
                {
                    "username": username,
                    "password": password,
                    "displayName": "Home Assistant Discovery E2E",
                },
            )
        elif action == "remove":
            response = request(
                sock, 2, "Users.RemoveUser", {"username": username}
            )
        elif action == "list":
            response = request(sock, 2, "Users.GetUsers", {})
        else:
            raise SystemExit(f"unknown action {action}")
        print("result", json.dumps(response))


if __name__ == "__main__":
    main()
