"""Connections kept open between calls: a new one adds a TLS handshake, about 0.4 s, to every model call and every spoken line."""
from __future__ import annotations

import http.client
import ssl
import threading
from typing import List, Tuple
from urllib.parse import urlsplit

LIMIT = 4


def connect(host: str, timeout: float) -> http.client.HTTPSConnection:
    return http.client.HTTPSConnection(host, timeout=timeout)


class Pool:
    def __init__(self) -> None:
        self.kept: List[http.client.HTTPSConnection] = []
        self.lock = threading.Lock()

    def send(self, method: str, url: str, data: bytes, headers: dict,
             timeout: float) -> Tuple[http.client.HTTPSConnection, http.client.HTTPResponse]:
        """A kept connection the far end has closed shows only when it is used, and then the next one takes over."""
        address = urlsplit(url)
        target = address.path + ("?" + address.query if address.query else "")
        while True:
            with self.lock:
                connection = self.kept.pop() if self.kept else None
            kept = connection is not None
            if kept:
                connection.sock.settimeout(timeout)
            else:
                connection = connect(address.netloc, timeout)
            try:
                connection.request(method, target, data, headers)
                return connection, connection.getresponse()
            except (ConnectionError, ssl.SSLError):
                connection.close()
                if not kept:
                    raise
            except BaseException:
                connection.close()
                raise

    def finished(self, connection: http.client.HTTPSConnection) -> None:
        """Only for a response read to its end: anything else leaves the connection unusable."""
        with self.lock:
            if connection.sock and len(self.kept) < LIMIT:
                self.kept.append(connection)
                return
        connection.close()
