from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]


def test_owned_runtime_config_contains_no_credential_literal() -> None:
    roots = (
        ROOT / "infra" / "deployment",
        ROOT / "infra" / "images",
        ROOT / "infra" / "operations",
    )
    text = "\n".join(
        path.read_text(encoding="utf-8")
        for root in roots
        for path in sorted(root.glob("local*" if root.name == "operations" else "**/*"))
        if path.is_file()
    )
    forbidden_patterns = (
        r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----",
        r"AKIA[0-9A-Z]{16}",
        r"AIza[0-9A-Za-z_-]{35}",
        r"postgres(?:ql)?://[^\s:/]+:[^\s@/]+@",
        r"redis://:[^\s@/]+@",
    )
    for pattern in forbidden_patterns:
        assert re.search(pattern, text) is None
