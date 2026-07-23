from __future__ import annotations

import pytest

from fetch_homepages import fetch_one
import fetch_homepages


class _Response:
    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def read(self, limit):
        assert limit == fetch_homepages.MAX_BODY
        return b"<title>Example</title><meta name='description' content='Secure fetch'>"


def test_fetch_uses_default_tls_verification(monkeypatch):
    calls = []

    def fake_urlopen(request, **kwargs):
        calls.append(kwargs)
        return _Response()

    monkeypatch.setattr(fetch_homepages, "urlopen", fake_urlopen)
    result = fetch_one("example", "https://example.com")

    assert result["title"] == "Example"
    assert result["meta_desc"] == "Secure fetch"
    assert calls == [{"timeout": fetch_homepages.TIMEOUT}]


def test_invalid_url_is_rejected_without_a_request(monkeypatch):
    monkeypatch.setattr(
        fetch_homepages,
        "urlopen",
        lambda *args, **kwargs: pytest.fail("urlopen should not be called"),
    )
    assert fetch_one("bad", "file:///tmp/example")["error"] == "invalid_url"
