#!/usr/bin/env python3
# -*- coding: utf-8 -*-
r"""
将**多个 Git 提交**（同一列表 ``commits``）做**路径并集**导出。

关系说明（不再有 first / second）
----------------------------------
``commits`` 里每一项都是一次「版本快照」（commit）。脚本会：

- 取所有列出提交的**路径并集**；
- 对某个相对路径 ``p``：若各提交里该路径对应的 **blob 内容（oid）一致**，则只写**一份**到 ``p``；
- 若 **oid 不一致**，则为每个不同内容各写一份，文件名在主干与扩展名之间插入
  ``.__<短标签>__``；短标签默认取**该内容首次出现的提交**的哈希前缀，并在同一路径下自动加长直到**不重复**。

因此：**不再需要** ``commit_first`` / ``commit_second`` 两套概念，**一个有序列表即可**（顺序只影响「同一 oid 首次出现」用于打标签时的选取，不影响是否冲突）。

配置与兼容
----------
- JSON：``--config`` / ``--config=PATH`` 或环境变量 ``GIT_EXPORT_MERGE_CONFIG``；命令行显式项覆盖文件。
- 主字段为 ``commits``（数组）。若未提供 ``commits``，可读旧键 ``commit_first`` + ``commit_second`` 拼成列表（**过渡**）；详见 ``docs/设计说明.md`` 第五节。
- 命令行旧参数 ``--commit-first`` / ``--commit-second`` 仍可用但会弃用警告，请改用 ``--commit``。

依赖：Python 3.9+、本机 ``git`` 命令。

示例（在仓库根目录执行）::

    python3 src/git_export_merge_two_commits.py \\
        --repo /path/to/repo --commit REV1 --commit REV2 --commit REV3 -o ./dist/export

    python3 src/git_export_merge_two_commits.py \\
        --config ./config.example.json
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
import subprocess
import sys
from collections import defaultdict
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, DefaultDict, Mapping, MutableMapping, Sequence

__version__ = "1.2.0"

ENV_CONFIG = "GIT_EXPORT_MERGE_CONFIG"


class ExportMergeError(RuntimeError):
    """可预期的业务错误（参数、仓库、对象类型等）。"""


@dataclass(frozen=True)
class TreeEntry:
    """``git ls-tree`` 中的一条记录（仅关心 blob / gitlink 等）。"""

    mode: str
    kind: str
    oid: str


@dataclass
class ConflictRecord:
    """同一路径下出现多种 blob 时的导出结果说明。"""

    original_path: str
    variants: list[tuple[str, str]]  # (导出相对路径, 选用该 blob 的代表提交完整哈希)


@dataclass
class SkippedPath:
    path: str
    reason: str


@dataclass
class ExportStats:
    paths_union: int = 0
    paths_single_blob: int = 0
    paths_multi_blob: int = 0
    conflicts: list[ConflictRecord] = field(default_factory=list)
    skipped: list[SkippedPath] = field(default_factory=list)
    files_written: int = 0


def _run_git(
    repo: Path, args: Sequence[str], *, text: bool = False
) -> subprocess.CompletedProcess[bytes | str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args],
        check=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=text,
    )


def resolve_commit(repo: Path, rev: str) -> str:
    """将任意 rev 解析为完整 object 名（40 位 hex）。"""
    p = _run_git(repo, ["rev-parse", "--verify", f"{rev}^{{commit}}"], text=True)
    out = (p.stdout or "").strip()
    if not out:
        raise ExportMergeError(f"无法解析提交: {rev!r}")
    return out


def ensure_git_repo(repo: Path) -> None:
    p = subprocess.run(
        ["git", "-C", str(repo), "rev-parse", "--is-inside-work-tree"],
        stdout=subprocess.PIPE,
        stderr=subprocess.DEVNULL,
        text=True,
    )
    if p.returncode != 0 or (p.stdout or "").strip() != "true":
        raise ExportMergeError(f"不是 Git 工作区: {repo}")


def ls_tree(repo: Path, commit: str) -> dict[str, TreeEntry]:
    """path -> TreeEntry。路径使用 Git 原生分隔符（/）。"""
    out = _run_git(repo, ["ls-tree", "-r", "-z", commit]).stdout
    d: dict[str, TreeEntry] = {}
    i = 0
    while i < len(out):
        j = out.find(b"\x00", i)
        if j == -1:
            break
        line = out[i:j]
        i = j + 1
        tab = line.find(b"\t")
        if tab == -1:
            continue
        meta, pathb = line[:tab], line[tab + 1 :]
        parts = meta.split()
        if len(parts) < 3:
            continue
        mode, kind, oid = parts[0].decode(), parts[1].decode(), parts[2].decode()
        path = pathb.decode("utf-8", errors="surrogateescape")
        d[path] = TreeEntry(mode=mode, kind=kind, oid=oid)
    return d


def split_stem_ext(basename: str) -> tuple[str, str]:
    if basename.startswith(".") and basename.count(".") == 1:
        return basename, ""
    if "." in basename:
        stem, ext = basename.rsplit(".", 1)
        return stem, "." + ext
    return basename, ""


def variant_rel_path(rel_path: str, tag: str) -> str:
    """``path/foo.ext`` -> ``path/foo.__<tag>__.ext``"""
    dirname, base = os.path.split(rel_path)
    stem, ext = split_stem_ext(base)
    return os.path.join(dirname, f"{stem}.__{tag}__{ext}")


def unique_commit_tag(commit_full: str, used: set[str], min_len: int, max_len: int) -> str:
    """用提交哈希前缀生成标签，在 ``used`` 内不重复；必要时自动加长到 ``max_len``。"""
    lo = min(40, max(4, min_len))
    hi = min(40, max(lo, max_len))
    for n in range(lo, hi + 1):
        cand = commit_full[:n]
        if cand not in used:
            used.add(cand)
            return cand
    if commit_full not in used:
        used.add(commit_full)
        return commit_full
    i = 0
    while True:
        cand = f"{commit_full}_{i}"
        if cand not in used:
            used.add(cand)
            return cand
        i += 1


class BatchCatFile:
    """通过 ``git cat-file --batch`` 顺序读取 blob（含记录尾换行处理）。"""

    def __init__(self, repo: Path) -> None:
        self._repo = repo
        self._p = subprocess.Popen(
            ["git", "-C", str(repo), "cat-file", "--batch"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
        )
        if not self._p.stdin or not self._p.stdout:
            raise ExportMergeError("无法启动 git cat-file --batch")

    def close(self) -> None:
        if self._p.stdin and not self._p.stdin.closed:
            self._p.stdin.close()
        try:
            self._p.wait(timeout=120)
        except subprocess.TimeoutExpired:
            self._p.kill()

    def __enter__(self) -> BatchCatFile:
        return self

    def __exit__(self, exc_type, exc, tb) -> None:  # noqa: ANN001
        self.close()

    def read_blob(self, oid: str) -> bytes:
        stdin = self._p.stdin
        stdout = self._p.stdout
        assert stdin and stdout
        stdin.write(oid.encode("ascii") + b"\n")
        stdin.flush()
        hdr = stdout.readline()
        if not hdr:
            raise EOFError(f"cat-file 无响应: {oid}")
        hdr_s = hdr.decode("utf-8", errors="replace").rstrip("\n")
        parts = hdr_s.split(maxsplit=2)
        if len(parts) >= 2 and parts[1] == "missing":
            raise FileNotFoundError(hdr_s)
        if len(parts) != 3:
            raise ExportMergeError(f"意外的 cat-file 头: {oid}: {hdr_s!r}")
        _sha, typ, size_s = parts[0], parts[1], parts[2]
        if typ != "blob":
            raise ExportMergeError(f"对象 {oid} 类型为 {typ}，期望 blob")
        size = int(size_s)
        data = stdout.read(size)
        if len(data) != size:
            raise EOFError(f"blob 截断 {oid}: {len(data)} != {size}")
        sep = stdout.read(1)
        if sep != b"\n":
            raise ExportMergeError(
                f"blob 记录后缺少换行分隔符: {oid}, 读到 {sep!r}, 头={hdr_s!r}"
            )
        return data


def _is_under(path: Path, parent: Path) -> bool:
    try:
        path.resolve().relative_to(parent.resolve())
        return True
    except ValueError:
        return False


def validate_output_dir(repo: Path, output: Path) -> None:
    out = output.resolve()
    repo_r = repo.resolve()
    if out == repo_r or _is_under(out, repo_r / ".git"):
        raise ExportMergeError(f"拒绝在仓库或 .git 内导出: {output}")
    git_dir = repo_r / ".git"
    if git_dir.exists() and _is_under(out, git_dir):
        raise ExportMergeError(f"拒绝导出到 .git 目录下: {output}")


def build_out_map_union(
    resolved_commits: Sequence[str],
    trees: Sequence[Mapping[str, TreeEntry]],
    *,
    tag_min_len: int,
    tag_max_len: int,
    skip_non_blob: bool,
) -> tuple[DefaultDict[str, list[str]], ExportStats]:
    """
    多提交路径并集：同路径多 oid 时拆成多文件，标签为提交哈希前缀（自动去重）。
    """
    out_map: DefaultDict[str, list[str]] = defaultdict(list)
    stats = ExportStats()

    all_paths: set[str] = set()
    for t in trees:
        all_paths |= set(t.keys())
    stats.paths_union = len(all_paths)

    for p in sorted(all_paths):
        # (commit_full, oid) 按 commits 顺序记录该路径上出现过的 blob
        occurrences: list[tuple[str, str]] = []
        for h, t in zip(resolved_commits, trees):
            if p not in t:
                continue
            ent = t[p]
            if ent.kind != "blob":
                msg = f"非 blob（{ent.kind}），跳过"
                stats.skipped.append(SkippedPath(path=p, reason=msg))
                if not skip_non_blob:
                    raise ExportMergeError(f"{p}: {msg}（使用 --skip-non-blob 可跳过）")
                continue
            occurrences.append((h, ent.oid))

        if not occurrences:
            continue

        # 每个 oid 保留「列表中首次出现」的代表提交（用于打标签）
        oid_order: list[str] = []
        oid_commit: dict[str, str] = {}
        for h, oid in occurrences:
            if oid not in oid_commit:
                oid_commit[oid] = h
                oid_order.append(oid)

        if len(oid_order) == 1:
            stats.paths_single_blob += 1
            out_map[oid_order[0]].append(p)
            continue

        stats.paths_multi_blob += 1
        tag_used: set[str] = set()
        variants: list[tuple[str, str]] = []
        for oid in oid_order:
            h = oid_commit[oid]
            tag = unique_commit_tag(h, tag_used, tag_min_len, tag_max_len)
            dest = variant_rel_path(p, tag)
            out_map[oid].append(dest)
            variants.append((dest, h))
        stats.conflicts.append(ConflictRecord(p, variants))

    return out_map, stats


def write_manifest_txt(
    path: Path,
    *,
    commits_resolved: Sequence[str],
    commits_raw: Sequence[str],
    tag_min_len: int,
    tag_max_len: int,
    stats: ExportStats,
) -> None:
    lines = [
        "导出规则：commits 列表中每个提交视为一个版本；取所有版本的路径并集。",
        "对同一相对路径：若各版本 blob（内容）一致则只写一份；若不一致则为每个不同 blob 各写一份，",
        f"文件名插入 .__<提交哈希前缀>__；前缀长度在 {tag_min_len}~{tag_max_len} 间自动选择以保证同路径下不重复。",
        "",
        f"commits（原始 rev，共 {len(commits_raw)} 个）:",
        *[f"  - {r}" for r in commits_raw],
        "",
        "commits（解析后完整哈希）:",
        *[f"  - {h}" for h in commits_resolved],
        "",
        f"路径并集: {stats.paths_union}",
        f"单内容路径: {stats.paths_single_blob}",
        f"多内容路径(冲突拆分): {stats.paths_multi_blob}",
        f"跳过: {len(stats.skipped)}",
        f"写出文件数(blob): {stats.files_written}（不含本说明 .txt/.json）",
    ]
    for c in stats.conflicts:
        lines += ["", f"原路径: {c.original_path}"]
        for dest, h in c.variants:
            lines.append(f"  - {dest}  (代表提交 {h})")
    if stats.skipped:
        lines += ["", "跳过条目:"]
        for s in stats.skipped:
            lines.append(f"  - {s.path}: {s.reason}")
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def write_manifest_json(path: Path, payload: dict[str, Any]) -> None:
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")


def export_blobs(
    repo: Path,
    output: Path,
    out_map: Mapping[str, Sequence[str]],
    stats: ExportStats,
    *,
    log: logging.Logger,
    progress_every: int,
) -> None:
    stats.files_written = 0
    with BatchCatFile(repo) as reader:
        for oid in sorted(out_map.keys()):
            data = reader.read_blob(oid)
            for rel in out_map[oid]:
                dest = output / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                dest.write_bytes(data)
                stats.files_written += 1
                if progress_every > 0 and stats.files_written % progress_every == 0:
                    log.info("已写入 %s 个文件", stats.files_written)


# ----- 配置文件与命令行优先级 -----


def extract_config_path(argv: Sequence[str]) -> Path | None:
    a = list(argv)
    i = 0
    while i < len(a):
        if a[i] == "--config" and i + 1 < len(a):
            return Path(a[i + 1]).expanduser()
        if a[i].startswith("--config="):
            return Path(a[i].split("=", 1)[1]).expanduser()
        i += 1
    env = os.environ.get(ENV_CONFIG, "").strip()
    if env:
        return Path(env).expanduser()
    return None


def load_json_config(path: Path) -> dict[str, Any]:
    """读取 JSON 配置。顶层键若以 ``_`` 开头，仅作文档旁注；``config_defaults_for_parser`` 不读取，导出 manifest 的 ``config_keys`` 也不列出。"""
    if not path.is_file():
        raise ExportMergeError(f"配置文件不存在: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
        data = json.loads(raw)
    except json.JSONDecodeError as e:
        raise ExportMergeError(f"配置文件 JSON 无效: {path}: {e}") from e
    if not isinstance(data, dict):
        raise ExportMergeError(f"配置文件顶层必须是 JSON 对象: {path}")
    return data


def resolve_config_paths_relative(cfg: MutableMapping[str, Any], config_path: Path) -> None:
    base = config_path.resolve().parent
    for key in ("repo", "output"):
        v = cfg.get(key)
        if not v or not isinstance(v, str):
            continue
        p = Path(v)
        if not p.is_absolute():
            cfg[key] = str((base / p).resolve())


def as_commit_list(value: Any, *, field: str) -> list[str]:
    if isinstance(value, str):
        s = value.strip()
        if not s:
            raise ExportMergeError(f"配置项 {field} 不能为空字符串")
        return [s]
    if isinstance(value, list):
        out = [str(x).strip() for x in value if str(x).strip()]
        if not out:
            raise ExportMergeError(f"配置项 {field} 不能为空列表")
        return out
    raise ExportMergeError(f"配置项 {field} 必须是字符串或字符串数组")


def commits_from_config(cfg: Mapping[str, Any]) -> list[str] | None:
    """从配置构造 commits；优先 ``commits``，否则兼容 ``commit_first`` + ``commit_second``。"""
    if "commits" in cfg and cfg["commits"] is not None:
        return as_commit_list(cfg["commits"], field="commits")
    if "commit_first" in cfg or cfg.get("commit_second"):
        parts: list[str] = []
        if "commit_first" in cfg:
            parts.extend(as_commit_list(cfg["commit_first"], field="commit_first"))
        sec = cfg.get("commit_second")
        if sec is not None and str(sec).strip():
            parts.append(str(sec).strip())
        return parts or None
    return None


def argv_flag_explicit(argv: Sequence[str], *, longs: tuple[str, ...], shorts: tuple[str, ...] = ()) -> bool:
    a = list(argv)
    for token in a:
        for L in longs:
            if token == L or token.startswith(L + "="):
                return True
        for S in shorts:
            if token == S:
                return True
            if token.startswith(S) and len(token) > len(S) and not token.startswith(S + "-"):
                return True
    return False


def extract_append_option_cli(argv: Sequence[str], flag: str) -> list[str]:
    """解析 ``--flag REV`` / ``--flag=REV``（不含 argparse append 合并）。"""
    a = list(argv)
    out: list[str] = []
    i = 0
    eq = f"{flag}="
    while i < len(a):
        tok = a[i]
        if tok == flag:
            if i + 1 >= len(a):
                raise ExportMergeError(f"`{flag}` 后缺少 rev 参数")
            out.append(str(a[i + 1]).strip())
            i += 2
            continue
        if tok.startswith(eq):
            out.append(tok.split("=", 1)[1].strip())
        i += 1
    return out


def extract_one_cli(argv: Sequence[str], flag: str) -> str | None:
    """解析单个 ``--flag REV`` / ``--flag=REV``。"""
    a = list(argv)
    eq = f"{flag}="
    i = 0
    while i < len(a):
        tok = a[i]
        if tok == flag:
            if i + 1 >= len(a):
                raise ExportMergeError(f"`{flag}` 后缺少 rev 参数")
            return str(a[i + 1]).strip()
        if tok.startswith(eq):
            return tok.split("=", 1)[1].strip()
        i += 1
    return None


def collect_cli_explicit_flags(argv: Sequence[str]) -> set[str]:
    a = argv
    found: set[str] = set()
    if argv_flag_explicit(a, longs=("--repo",)):
        found.add("repo")
    if argv_flag_explicit(a, longs=("--commit",)):
        found.add("commits")
    if argv_flag_explicit(a, longs=("--commit-first",)):
        found.add("commit_first")
    if argv_flag_explicit(a, longs=("--commit-second",)):
        found.add("commit_second")
    if argv_flag_explicit(a, longs=("--output",), shorts=("-o",)):
        found.add("output")
    if argv_flag_explicit(a, longs=("--tag-min-len",)):
        found.add("tag_min_len")
    if argv_flag_explicit(a, longs=("--tag-max-len",)):
        found.add("tag_max_len")
    if argv_flag_explicit(a, longs=("--hash-prefix-len",)):
        found.add("hash_prefix_len")
    if argv_flag_explicit(a, longs=("--manifest",)):
        found.add("manifest")
    if argv_flag_explicit(a, longs=("--no-json-manifest",)):
        found.add("no_json_manifest")
    if argv_flag_explicit(a, longs=("--dry-run",)):
        found.add("dry_run")
    if argv_flag_explicit(a, longs=("--skip-non-blob",)):
        found.add("skip_non_blob")
    if argv_flag_explicit(a, longs=("--progress-every",)):
        found.add("progress_every")
    if argv_flag_explicit(a, longs=("--verbose",), shorts=("-v",)):
        found.add("verbose")
    if argv_flag_explicit(a, longs=("--quiet",), shorts=("-q",)):
        found.add("quiet")
    return found


def config_defaults_for_parser(cfg: Mapping[str, Any], explicit: set[str]) -> dict[str, Any]:
    out: dict[str, Any] = {}
    if "repo" not in explicit and cfg.get("repo") is not None:
        out["repo"] = Path(str(cfg["repo"]))

    if (
        "commits" not in explicit
        and "commit_first" not in explicit
        and "commit_second" not in explicit
    ):
        c = commits_from_config(cfg)
        if c is not None:
            out["commits"] = c

    if "output" not in explicit and cfg.get("output") is not None:
        out["output"] = Path(str(cfg["output"]))

    # 标签长度：新键 tag_min_len / tag_max_len；兼容旧 hash_prefix_len 作为 min
    if "tag_min_len" not in explicit and "hash_prefix_len" not in explicit:
        if "tag_min_len" in cfg:
            out["tag_min_len"] = int(cfg["tag_min_len"])
        elif "hash_prefix_len" in cfg:
            out["tag_min_len"] = int(cfg["hash_prefix_len"])
    if "tag_max_len" not in explicit and "tag_max_len" in cfg:
        out["tag_max_len"] = int(cfg["tag_max_len"])

    if "manifest" not in explicit and "manifest" in cfg:
        out["manifest"] = str(cfg["manifest"])
    if "no_json_manifest" not in explicit and "no_json_manifest" in cfg:
        out["no_json_manifest"] = bool(cfg["no_json_manifest"])
    if "dry_run" not in explicit and "dry_run" in cfg:
        out["dry_run"] = bool(cfg["dry_run"])
    if "skip_non_blob" not in explicit and "skip_non_blob" in cfg:
        out["skip_non_blob"] = bool(cfg["skip_non_blob"])
    if "progress_every" not in explicit and "progress_every" in cfg:
        out["progress_every"] = int(cfg["progress_every"])
    if "verbose" not in explicit and "verbose" in cfg:
        out["verbose"] = int(cfg["verbose"])
    if "quiet" not in explicit and "quiet" in cfg:
        out["quiet"] = bool(cfg["quiet"])
    return out


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        description="多提交路径并集导出：同路径内容不同则拆成多文件，文件名用提交哈希前缀区分。",
        formatter_class=argparse.ArgumentDefaultsHelpFormatter,
    )
    p.add_argument(
        "--config",
        type=Path,
        default=None,
        metavar="PATH",
        help=f"JSON 配置文件（未指定时可读环境变量 {ENV_CONFIG}）。其它命令行显式参数优先于文件",
    )
    p.add_argument(
        "--repo",
        type=Path,
        default=None,
        help="Git 仓库根目录；默认当前工作目录",
    )
    p.add_argument(
        "--commit",
        dest="commits",
        action="append",
        default=None,
        metavar="REV",
        help="参与导出的提交，按顺序列出；可重复指定。与配置文件中的 commits 数组等价",
    )
    p.add_argument(
        "--commit-first",
        action="append",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--commit-second",
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--output",
        "-o",
        type=Path,
        default=None,
        help="导出根目录（存在则先删除，除非 --dry-run）",
    )
    p.add_argument(
        "--tag-min-len",
        type=int,
        default=7,
        metavar="N",
        help="冲突文件名中提交哈希前缀的最小长度（若碰撞会自动加长）",
    )
    p.add_argument(
        "--tag-max-len",
        type=int,
        default=12,
        metavar="N",
        help="冲突文件名中提交哈希前缀的最大长度（仍碰撞则使用完整 40 位或加后缀）",
    )
    p.add_argument(
        "--hash-prefix-len",
        type=int,
        default=argparse.SUPPRESS,
        help=argparse.SUPPRESS,
    )
    p.add_argument(
        "--manifest",
        default="_导出说明",
        help="导出目录内说明文件主名（不含扩展名）；同时写入 .txt 与 .json",
    )
    p.add_argument(
        "--no-json-manifest",
        action="store_true",
        help="不写 JSON 说明，仅写 .txt",
    )
    p.add_argument(
        "--dry-run",
        action="store_true",
        help="只解析树、统计并打印，不写任何文件",
    )
    p.add_argument(
        "--skip-non-blob",
        action="store_true",
        help="跳过 submodule(gitlink) 等非 blob 路径并记入说明；默认遇到即失败",
    )
    p.add_argument(
        "--progress-every",
        type=int,
        default=5000,
        metavar="N",
        help="每写出 N 个文件打一条日志；0 表示关闭",
    )
    p.add_argument(
        "-v",
        "--verbose",
        action="count",
        default=0,
        help="提高日志详细度：默认已为 INFO；重复 -v 时第二次起为 DEBUG（与 configure_logging 一致）",
    )
    p.add_argument(
        "-q",
        "--quiet",
        action="store_true",
        help="仅警告及以上",
    )
    p.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return p


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    argv = list(sys.argv[1:] if argv is None else argv)

    cfg_path = extract_config_path(argv)
    cfg: dict[str, Any] = {}
    if cfg_path is not None:
        cfg_path = cfg_path.expanduser().resolve()
        cfg = dict(load_json_config(cfg_path))
        resolve_config_paths_relative(cfg, cfg_path)

    explicit = collect_cli_explicit_flags(argv)
    defaults = config_defaults_for_parser(cfg, explicit)

    parser = build_parser()
    parser.set_defaults(**defaults)
    args = parser.parse_args(argv)

    # 兼容旧参数名 hash_prefix_len -> tag_min_len
    if "hash_prefix_len" in explicit and "tag_min_len" not in explicit:
        hp = getattr(args, "hash_prefix_len", None)
        if hp is not None:
            args.tag_min_len = int(hp)

    # 显式 --commit：只取命令行（避免 append 与 set_defaults 合并）
    if "commits" in explicit:
        vals = extract_append_option_cli(argv, "--commit")
        if not vals:
            raise ExportMergeError("`--commit` 未解析到任何 rev")
        args.commits = vals
    elif "commit_first" in explicit or "commit_second" in explicit:
        if "commits" in explicit:
            raise ExportMergeError("不要同时使用 `--commit` 与 `--commit-first`/`--commit-second`")
        first = extract_append_option_cli(argv, "--commit-first") if "commit_first" in explicit else []
        sec = extract_one_cli(argv, "--commit-second") if "commit_second" in explicit else None
        if "commit_first" in explicit and not first:
            raise ExportMergeError("`--commit-first` 未解析到任何 rev")
        if "commit_second" in explicit and not sec:
            raise ExportMergeError("`--commit-second` 缺少 rev")
        args.commits = list(first)
        if sec:
            args.commits.append(sec)
        args._deprecated_legacy_cli = True  # noqa: SLF001

    if not args.commits:
        raise ExportMergeError(
            "必须提供至少一个提交：配置文件 commits（或兼容 commit_first+commit_second），或命令行 --commit / 旧参数"
        )
    if not args.output:
        raise ExportMergeError("必须提供导出目录 output（配置文件或 -o/--output）")

    if args.tag_max_len < args.tag_min_len:
        raise ExportMergeError("`--tag-max-len` 不得小于 `--tag-min-len`")

    args._config_path = str(cfg_path) if cfg_path else None  # noqa: SLF001
    args._config_used_keys = sorted(k for k in cfg if not k.startswith("_"))  # noqa: SLF001
    args._cli_explicit = sorted(explicit)  # noqa: SLF001
    return args


def configure_logging(verbose: int, quiet: bool) -> logging.Logger:
    if quiet:
        level = logging.WARNING
    elif verbose >= 2:
        level = logging.DEBUG
    elif verbose == 1:
        level = logging.INFO
    else:
        level = logging.INFO
    logging.basicConfig(
        level=level,
        format="%(levelname)s %(message)s",
    )
    return logging.getLogger("export_merge")


def main(argv: Sequence[str] | None = None) -> int:
    try:
        args = parse_args(argv)
    except ExportMergeError as e:
        print(f"参数错误: {e}", file=sys.stderr)
        return 2

    log = configure_logging(args.verbose, args.quiet)

    repo = (args.repo or Path.cwd()).resolve()
    output = args.output.expanduser().resolve()

    try:
        ensure_git_repo(repo)

        resolved = [resolve_commit(repo, rev) for rev in args.commits]
        validate_output_dir(repo, output)

        trees = [ls_tree(repo, h) for h in resolved]

        out_map, stats = build_out_map_union(
            resolved,
            trees,
            tag_min_len=args.tag_min_len,
            tag_max_len=args.tag_max_len,
            skip_non_blob=args.skip_non_blob,
        )

        if getattr(args, "_deprecated_legacy_cli", False):  # noqa: SLF001
            log.warning(
                "`--commit-first`/`--commit-second` 已弃用，请改用 `--commit`；当前语义为「多版本路径并集」"
            )

        log.info(
            "提交数=%s 并集路径=%s 多版本路径=%s 跳过=%s",
            len(resolved),
            stats.paths_union,
            stats.paths_multi_blob,
            len(stats.skipped),
        )
        if getattr(args, "_config_path", None):  # noqa: SLF001
            log.info("配置文件: %s", args._config_path)  # noqa: SLF001

        if args.dry_run:
            log.info("干跑结束，未写盘。")
            return 0

        if output.exists():
            shutil.rmtree(output)
        output.mkdir(parents=True, exist_ok=True)

        export_blobs(
            repo,
            output,
            out_map,
            stats,
            log=log,
            progress_every=args.progress_every,
        )

        manifest_base = (
            args.manifest.strip().replace(os.sep, "_").replace("..", "_") or "manifest"
        )
        txt_path = output / f"{manifest_base}.txt"
        write_manifest_txt(
            txt_path,
            commits_resolved=resolved,
            commits_raw=list(args.commits),
            tag_min_len=args.tag_min_len,
            tag_max_len=args.tag_max_len,
            stats=stats,
        )
        if not args.no_json_manifest:
            json_path = output / f"{manifest_base}.json"
            write_manifest_json(
                json_path,
                {
                    "version": __version__,
                    "merge_model": "path_union_multi_blob_split",
                    "commits": resolved,
                    "commits_raw": list(args.commits),
                    "tag_min_len": args.tag_min_len,
                    "tag_max_len": args.tag_max_len,
                    "config_path": getattr(args, "_config_path", None),  # noqa: SLF001
                    "config_keys": getattr(args, "_config_used_keys", []),  # noqa: SLF001
                    "cli_explicit": getattr(args, "_cli_explicit", []),  # noqa: SLF001
                    "paths_union": stats.paths_union,
                    "paths_single_blob": stats.paths_single_blob,
                    "paths_multi_blob": stats.paths_multi_blob,
                    "conflicts": [
                        {
                            "original": c.original_path,
                            "variants": [{"path": d, "commit": h} for d, h in c.variants],
                        }
                        for c in stats.conflicts
                    ],
                    "skipped": [{"path": s.path, "reason": s.reason} for s in stats.skipped],
                    "files_written": stats.files_written,
                },
            )

        log.info("完成：写出文件=%s（含说明）。目录=%s", stats.files_written, output)
        return 0
    except ExportMergeError as e:
        log.error("%s", e)
        return 2
    except subprocess.CalledProcessError as e:
        log.error("git 失败: %s", e)
        if e.stderr:
            sys.stderr.write(
                e.stderr.decode("utf-8", errors="replace")
                if isinstance(e.stderr, bytes)
                else str(e.stderr)
            )
        return 3
    except (OSError, EOFError, FileNotFoundError) as e:
        log.error("%s: %s", type(e).__name__, e)
        return 4


if __name__ == "__main__":
    raise SystemExit(main())
