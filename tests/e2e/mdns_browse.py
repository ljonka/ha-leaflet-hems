import socket
import time

from zeroconf import ServiceBrowser, ServiceListener, Zeroconf


class Listener(ServiceListener):
    def __init__(self) -> None:
        self.services = []

    def add_service(self, zc, type_, name):
        info = zc.get_service_info(type_, name)
        if info:
            self.services.append((name, info))

    def update_service(self, zc, type_, name):
        self.add_service(zc, type_, name)

    def remove_service(self, zc, type_, name):
        pass


zc = Zeroconf()
listener = Listener()
browser = ServiceBrowser(zc, "_jsonrpc._tcp.local.", listener)
time.sleep(8)
for name, info in listener.services:
    props = {
        key.decode() if isinstance(key, bytes) else key: (
            value.decode(errors="ignore") if isinstance(value, bytes) else value
        )
        for key, value in (info.properties or {}).items()
    }
    addresses = [socket.inet_ntoa(addr) for addr in info.addresses]
    print(name)
    print("  server:", info.server)
    print("  addresses:", addresses)
    print("  port:", info.port)
    print("  properties:", props)
browser.cancel()
zc.close()
