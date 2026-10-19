"""Run before every git commit. Looks for your real API keys in this folder.

    uv run python check_secrets.py

Reads the key values from the harness's .env and searches every file here for
them, plus common key shapes. Prints nothing alarming if the folder is clean.
"""

import os
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
ENV = Path(os.environ.get("TAU2_DIR", Path.home() / "tau2-bench")) / ".env"
SHAPES = re.compile(r"(sk-[A-Za-z0-9_\-]{20,}|sk-ant-[A-Za-z0-9_\-]{20,}|AIza[0-9A-Za-z_\-]{30,}|sk-or-v1-[A-Za-z0-9]{20,})")
SKIP_DIRS = {".git", ".venv", "__pycache__"}


def env_secrets():
    vals = []
    if ENV.exists():
        for line in ENV.read_text().splitlines():
            if "=" in line and not line.strip().startswith("#"):
                v = line.split("=", 1)[1].strip().strip('"').strip("'")
                if len(v) >= 16 and "<" not in v and not v.startswith("http"):
                    vals.append(v)
    return vals


def main():
    secrets = env_secrets()
    hits = []
    for p in HERE.rglob("*"):
        if p.is_dir() or any(part in SKIP_DIRS for part in p.parts) or p.name == ".env":
            continue
        try:
            text = p.read_text(errors="ignore")
        except OSError:
            continue
        rel = p.relative_to(HERE)
        for s in secrets:
            if s in text:
                hits.append(f"{rel}: contains one of your real keys from .env")
        for m in SHAPES.finditer(text):
            hits.append(f"{rel}: something shaped like an API key ({m.group(0)[:8]}...)")
    if (HERE / ".env").exists():
        gitignore = HERE / ".gitignore"
        listed = gitignore.exists() and ".env" in gitignore.read_text(errors="ignore").split()
        if not listed:
            hits.append(".env is in this folder and not listed in .gitignore")
    if hits:
        print("STOP. Do not commit. Fix these first:")
        for h in sorted(set(hits)):
            print("  " + h)
        sys.exit(1)
    print(f"Clean. Checked {len(secrets)} key(s) from {ENV} against every file in {HERE.name}/.")


if __name__ == "__main__":
    main()
