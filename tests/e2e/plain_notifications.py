"""Listen to nymea notifications through the plain localhost interface.

Run on a Leaflet device:

    python3 - 20 < plain_notifications.py
"""

import json
import socket
import sys
import time

HOST = "127.0.0.1"
PORT = 2223
DURATION = float(sys.argv[1]) if len(sys.argv) > 1 else 15.0


def send(sock, request_id, method, params=None):
    payload = {"id": request_id, "method": method}
    if params is not None:
        payload["params"] = params
    sock.sendall(json.dumps(payload).encode() + b"\n")


def read_line(sock):
    buffer = b""
    while b"\n" not in buffer:
        chunk = sock.recv(65536)
        if not chunk:
            raise RuntimeError("connection closed")
        buffer += chunk
    line, _, rest = buffer.partition(b"\n")
    return json.loads(line)


def main():
    with socket.create_connection((HOST, PORT), timeout=10) as sock:
        send(sock, 1, "JSONRPC.Hello", {"locale": "de_DE"})
        hello = read_line(sock)
        print("hello", hello.get("status"), hello.get("params", {}).get("name"))
        send(
            sock,
            2,
            "JSONRPC.SetNotificationStatus",
            {"namespaces": ["Energy", "Hems", "Integrations"]},
        )
        print("notifications", read_line(sock).get("status"))
        send(sock, 3, "Energy.GetPowerBalance")
        balance = read_line(sock)
        print("power balance", json.dumps(balance.get("params", {})))
        send(sock, 4, "Energy.GetRootMeter")
        print("root meter", json.dumps(read_line(sock).get("params", {})))

        sock.settimeout(1.0)
        counts = {}
        samples = {}
        end = time.monotonic() + DURATION
        while time.monotonic() < end:
            try:
                message = read_line(sock)
            except socket.timeout:
                continue
            method = message.get("notification") or message.get("method") or "?"
            counts[method] = counts.get(method, 0) + 1
            samples.setdefault(method, message)
        print("counts", json.dumps(counts, indent=2))
        for method, sample in samples.items():
            print("sample", method, json.dumps(sample)[:1000])


if __name__ == "__main__":
    main()
