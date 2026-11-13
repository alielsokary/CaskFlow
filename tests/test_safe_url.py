import urllib.error
import urllib.request

import pytest

import safe_url


def resolver(table):
    return lambda host: table.get(host, [])


@pytest.mark.parametrize("url", [
    "http://127.0.0.1/x", "http://169.254.169.254/latest", "https://10.0.0.5", "https://internal.corp/",
    "ftp://example.com/", "file:///etc/passwd", "https://", "javascript:alert(1)",
])
def test_private_or_odd_urls_are_rejected(url):
    table = {"internal.corp": ["192.168.1.2"]}
    assert not safe_url.is_public_url(url, resolve=resolver(table))


def test_public_url_is_allowed():
    assert safe_url.is_public_url("https://example.com/a", resolve=resolver({"example.com": ["93.184.216.34"]}))


def test_host_with_any_private_address_is_rejected():
    table = {"mixed.example": ["93.184.216.34", "127.0.0.1"]}
    assert not safe_url.is_public_url("https://mixed.example", resolve=resolver(table))


def test_redirects_to_private_hosts_are_refused():
    handler = safe_url.PublicRedirectHandler(resolve=resolver({"ok.example": ["93.184.216.34"]}))
    request = urllib.request.Request("https://ok.example/")
    with pytest.raises(urllib.error.HTTPError):
        handler.redirect_request(request, None, 302, "Found", {}, "http://169.254.169.254/latest")
    assert handler.redirect_request(request, None, 302, "Found", {}, "https://ok.example/b") is not None
