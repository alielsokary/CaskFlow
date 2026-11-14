#!/usr/bin/env python3
"""Guards for URLs that come from third-party taps: public hosts only, on every redirect too."""
# The opener re-resolves and checks the host when it connects and connects to that checked
# address, so a DNS answer that changes after `is_public_url` (rebinding) can't reach a
# private address.
from __future__ import annotations

import functools
import http.client
import ipaddress
import socket
import urllib.error
import urllib.request
from typing import Callable
from urllib.parse import urlparse

Resolver = Callable[[str], list[str]]


def _resolve(host: str) -> list[str]:
    try:
        return [info[4][0] for info in socket.getaddrinfo(host, None)]
    except OSError:
        return []


def is_public_url(url: str, *, schemes: tuple[str, ...] = ("http", "https"), resolve: Resolver = _resolve) -> bool:
    """True only for the allowed schemes and a host whose every address is globally routable."""
    parsed = urlparse(url or "")
    host = parsed.hostname
    if parsed.scheme not in schemes or not host:
        return False
    try:
        _checked_address(host, resolve)
    except (OSError, ValueError):
        return False
    return True


def _checked_address(host: str, resolve: Resolver) -> str:
    try:
        addresses = [host] if ipaddress.ip_address(host) else []
    except ValueError:
        addresses = [address.split("%")[0] for address in resolve(host)]
    if not addresses or not all(ipaddress.ip_address(address).is_global for address in addresses):
        raise OSError(f"refusing to connect to non-public host {host!r}")
    return addresses[0]


class _PinnedConnection:
    """Connects only to an address `_checked_address` approved at connect time."""

    def __init__(self, *args, resolve: Resolver = _resolve, **kwargs):
        """Keep the resolver hosts are checked with."""
        super().__init__(*args, **kwargs)
        self._resolve_host = resolve

    def _open_socket(self) -> socket.socket:
        address = _checked_address(self.host, self._resolve_host)
        return socket.create_connection((address, self.port), self.timeout, self.source_address)


class _PublicHTTPConnection(_PinnedConnection, http.client.HTTPConnection):
    def connect(self):
        self.sock = self._open_socket()


class _PublicHTTPSConnection(_PinnedConnection, http.client.HTTPSConnection):
    def connect(self):
        self.sock = self._context.wrap_socket(self._open_socket(), server_hostname=self.host)


class _Resolving:
    """A urllib handler that hands its resolver to the connections it opens."""

    def __init__(self, resolve: Resolver):
        """Keep the resolver for this handler's connections."""
        super().__init__()
        self._resolve = resolve


class _PublicHTTPHandler(_Resolving, urllib.request.HTTPHandler):
    def http_open(self, req):
        return self.do_open(functools.partial(_PublicHTTPConnection, resolve=self._resolve), req)


class _PublicHTTPSHandler(_Resolving, urllib.request.HTTPSHandler):
    def https_open(self, req):
        return self.do_open(functools.partial(_PublicHTTPSConnection, resolve=self._resolve), req, context=self._context)


class PublicRedirectHandler(_Resolving, urllib.request.HTTPRedirectHandler):
    def __init__(self, resolve: Resolver = _resolve):
        """Refuse redirects to non-public hosts, checked with `resolve`."""
        super().__init__(resolve)

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_url(newurl, resolve=self._resolve):
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-public address refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_opener(resolve: Resolver = _resolve) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(
        PublicRedirectHandler(resolve), _PublicHTTPHandler(resolve), _PublicHTTPSHandler(resolve)
    )
