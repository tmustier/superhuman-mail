#!/bin/bash
set -euo pipefail
ROOT=$(cd "$(dirname "$0")/../.." && pwd)
cd "$ROOT"
python3 - <<'PY'
from __future__ import annotations
import re
import subprocess
from pathlib import Path

paths = subprocess.check_output(
    ["git", "ls-files", "--cached", "--others", "--exclude-standard", "-z"]
).decode().split("\0")
allowed_domains = {
    "example.com", "example.test", "x.com", "y.com", "co.com", "outlook.com",
    "we.are.superhuman.com", "superhuman.com", "acme.com", "vendor.com",
}
email = re.compile(r"(?<![\w.+-])([\w.+-]+)@([A-Za-z0-9.-]+\.[A-Za-z]{2,})(?![\w.-])")
rules = {
    "private absolute user path": re.compile(r"/(?:Users|home)/[^/@\s]+/"),
    "machine hostname": re.compile(r"\b(?!localhost\b)[A-Za-z0-9-]+\.local\b", re.I),
    "concrete Slack principal": re.compile(r"\bslack:U[A-Z0-9]{8,}\b"),
    "private key material": re.compile(r"-----BEGIN (?:OPENSSH |RSA |EC )?PRIVATE KEY-----"),
    "credential assignment": re.compile(r"(?i)\b(?:token|secret|password|api[_-]?key)\s*[:=]\s*['\"][^@\s<]{12,}['\"]"),
}
violations: list[str] = []
for raw in paths:
    if not raw or raw == "authority/release/public-source-scrub.sh":
        continue
    path = Path(raw)
    try:
        data = path.read_bytes()
    except OSError:
        continue
    if b"\0" in data:
        continue
    text = data.decode("utf-8", errors="replace")
    for line_no, line in enumerate(text.splitlines(), 1):
        for match in email.finditer(line):
            if match.group(2).lower() not in allowed_domains:
                violations.append(f"{raw}:{line_no}: non-example email domain")
        for label, pattern in rules.items():
            if label == "credential assignment" and re.search(r"(?i)\b(?:test|fixture|runtime|example)[-_ ]", line):
                continue
            if pattern.search(line):
                violations.append(f"{raw}:{line_no}: {label}")
if violations:
    print("public-source scrub failed:")
    print("\n".join(sorted(set(violations))))
    raise SystemExit(1)
print(f"public-source scrub passed ({len([p for p in paths if p])} files)")
PY
