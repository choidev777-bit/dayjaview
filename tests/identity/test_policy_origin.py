from __future__ import annotations

import pytest

from packages.identity import IdentityPolicy


def test_policy_accepts_https_and_local_http_origins_only() -> None:
    assert (
        IdentityPolicy(app_base_url="https://dayjaview.vercel.app").allowed_origin
        == "https://dayjaview.vercel.app"
    )
    assert (
        IdentityPolicy(app_base_url="http://localhost:5173").allowed_origin
        == "http://localhost:5173"
    )
    assert (
        IdentityPolicy(app_base_url="http://127.0.0.1:8000").allowed_origin
        == "http://127.0.0.1:8000"
    )
    with pytest.raises(ValueError):
        IdentityPolicy(app_base_url="http://dayjaview.vercel.app")
    with pytest.raises(ValueError):
        IdentityPolicy(app_base_url="http://localhost.evil.example")
