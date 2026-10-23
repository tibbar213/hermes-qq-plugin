"""Offline checks for a proposed public source tree; not a guarantee of secret absence."""
import ast
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[1]
issues = []
files = [p for p in ROOT.rglob("*") if p.is_file() and ".git" not in p.parts]
for path in files:
    rel = path.relative_to(ROOT)
    if path.name == ".env" or path.suffix in {".db", ".sqlite", ".sqlite3", ".log", ".pem", ".key"}:
        issues.append(f"Runtime/private file: {rel}")
    if path.suffix in {".pyc", ".zip", ".gz"}:
        issues.append(f"Generated/binary file: {rel}")
    try:
        content = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        issues.append(f"Unexpected binary: {rel}")
        continue
    if path.suffix == ".py":
        ast.parse(content, filename=str(rel))
    if re.search(r"(?:sk-|sk_live_)[A-Za-z0-9_-]{20,}", content):
        issues.append(f"Possible API credential: {rel}")
    if re.search(r"-----BEGIN (?:RSA |EC |OPENSSH )?PRIVATE KEY-----", content):
        issues.append(f"Private key: {rel}")
    if re.search(r"https?://[^\s/'\"]+:[^\s/'\"]+@", content):
        issues.append(f"URL credential: {rel}")
if issues:
    raise SystemExit("\n".join(issues))
print(f"PASS: {len(files)} text files, Python syntax and basic release-content checks")
print("Also compare against real deployment credentials privately; do not print those values.")
