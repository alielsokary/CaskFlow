#!/usr/bin/env python3
"""Guards for URLs that come from third-party taps: public hosts only, on every redirect too."""
from __future__ import annotations

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
        addresses = [ipaddress.ip_address(host)]
    except ValueError:
        addresses = [ipaddress.ip_address(address.split("%")[0]) for address in resolve(host)]
    return bool(addresses) and all(address.is_global for address in addresses)


class PublicRedirectHandler(urllib.request.HTTPRedirectHandler):
    def __init__(self, resolve: Resolver = _resolve):
        super().__init__()
        self._resolve = resolve

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        if not is_public_url(newurl, resolve=self._resolve):
            raise urllib.error.HTTPError(newurl, code, "redirect to a non-public address refused", headers, fp)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def public_opener(resolve: Resolver = _resolve) -> urllib.request.OpenerDirector:
    return urllib.request.build_opener(PublicRedirectHandler(resolve))
