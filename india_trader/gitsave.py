"""Saves changed data files back into the GitHub repository, so the next run remembers them."""
import os
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent


def commit_paths(paths: list[Path | str], message: str) -> None:
    if not os.environ.get("GITHUB_ACTIONS"):
        return
    subprocess.run(["git", "add", *[str(p) for p in paths]], cwd=ROOT, capture_output=True)
    if subprocess.run(["git", "diff", "--cached", "--quiet"], cwd=ROOT).returncode == 0:
        return
    for cmd in (["git", "commit", "-q", "-m", message], ["git", "pull", "-q", "--rebase"],
                ["git", "push", "-q"]):
        r = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True)
        if r.returncode != 0:
            print(f"git {cmd[1]} failed: {r.stderr.strip()[:300]}")
            return
