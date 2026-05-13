#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""git-export-merge 冒烟测试：临时仓库 + 脚本 dry-run / 实导出。"""

from __future__ import annotations

import shutil
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "src" / "git_export_merge_two_commits.py"


def _run_git(repo: Path, args: list[str]) -> None:
    subprocess.run(["git", "-C", str(repo), *args], check=True)


def _make_two_commit_repo() -> tuple[Path, str, str]:
    d = Path(tempfile.mkdtemp(prefix="gem-smoke-"))
    _run_git(d, ["init"])
    _run_git(d, ["config", "user.email", "smoke@test.local"])
    _run_git(d, ["config", "user.name", "smoke"])
    (d / "a.txt").write_text("version-one\n", encoding="utf-8")
    _run_git(d, ["add", "a.txt"])
    _run_git(d, ["commit", "-m", "first"])
    h1 = subprocess.check_output(["git", "-C", str(d), "rev-parse", "HEAD"], text=True).strip()
    (d / "a.txt").write_text("version-two\n", encoding="utf-8")
    _run_git(d, ["add", "a.txt"])
    _run_git(d, ["commit", "-m", "second"])
    h2 = subprocess.check_output(["git", "-C", str(d), "rev-parse", "HEAD"], text=True).strip()
    return d, h1, h2


def _run_script(argv: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, str(SCRIPT), *argv],
        cwd=str(ROOT),
        text=True,
        capture_output=True,
    )


class SmokeTests(unittest.TestCase):
    def test_help_exits_zero(self) -> None:
        p = _run_script(["--help"])
        self.assertEqual(p.returncode, 0, p.stderr)
        self.assertIn("commits", p.stdout)

    def test_dry_run_two_commits_conflict(self) -> None:
        repo, h1, h2 = _make_two_commit_repo()
        try:
            p = _run_script(
                [
                    "--repo",
                    str(repo),
                    "--commit",
                    h1,
                    "--commit",
                    h2,
                    "-o",
                    str(repo / "out"),
                    "--dry-run",
                ]
            )
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            combined = (p.stdout or "") + (p.stderr or "")
            self.assertIn("多版本路径=1", combined)
        finally:
            shutil.rmtree(repo, ignore_errors=True)

    def test_export_writes_variants(self) -> None:
        repo, h1, h2 = _make_two_commit_repo()
        out = repo / "export"
        try:
            p = _run_script(
                [
                    "--repo",
                    str(repo),
                    "--commit",
                    h1,
                    "--commit",
                    h2,
                    "-o",
                    str(out),
                    "--no-json-manifest",
                ]
            )
            self.assertEqual(p.returncode, 0, p.stderr + p.stdout)
            tagged = sorted(out.glob("a.__*.txt"))
            self.assertEqual(len(tagged), 2, list(out.iterdir()))
            self.assertTrue((out / "_导出说明.txt").is_file())
        finally:
            shutil.rmtree(repo, ignore_errors=True)


if __name__ == "__main__":
    unittest.main()
