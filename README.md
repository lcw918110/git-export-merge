# git-export-merge

将多个 Git 提交（版本快照）做**路径并集导出**：同一相对路径若内容不同则拆成多份文件，并用提交哈希前缀区分。

## 目录结构

```
git-export-merge/
├── README.md
├── config.example.json       # 配置文件示例（复制后修改）
├── docs/
│   └── 设计说明.md
├── src/
│   └── git_export_merge_two_commits.py
└── tests/
    └── test_smoke.py
```

## 环境要求

- Python 3.9+
- 已安装 `git` 命令并在 `PATH` 中

## 快速开始

克隆本仓库后，**进入仓库根目录**（即包含 `README.md` 与 `src` 的目录）：

```bash
git clone https://github.com/lcw918110/git-export-merge.git
cd git-export-merge

python3 src/git_export_merge_two_commits.py --help

# 干跑（不写盘）：将下面 /path/to/repo 换成你要分析的真实仓库路径
python3 src/git_export_merge_two_commits.py \
  --repo /path/to/repo \
  --commit REV1 --commit REV2 \
  -o /path/to/export-dir \
  --dry-run
```

## 配置文件

本仓库根目录提供 **`config.example.json`**（内含 **`_字段说明`** 中文释义，以及以 `_` 开头的旁注键；旁注不参与解析）。请复制为例如 `git_export_merge.config.json` 后按需修改。

**与程序一致的字段一览**（更细的命令行对照见 `docs/设计说明.md` **4.3 节**）：

| 配置键 | 说明 |
|--------|------|
| `repo` | 目标 Git 工作区根；相对路径相对**配置文件所在目录**；未配置且命令行也未指定 `--repo` 时，程序使用**当前工作目录** |
| `commits` | 提交 rev 的 JSON 数组，与多次 `--commit` 等价 |
| `output` | 导出根目录；非 dry-run 时若已存在会先删除 |
| `tag_min_len` / `tag_max_len` | 冲突文件名中哈希前缀长度范围（默认 7～12） |
| `manifest` | 导出说明文件主名（生成 `.txt` 与可选 `.json`） |
| `no_json_manifest` | `true` 则只写 `.txt` |
| `dry_run` | `true` 只统计不写盘 |
| `skip_non_blob` | `true` 跳过非 blob（如 submodule） |
| `progress_every` | 每写多少个文件打一条进度日志，`0` 关闭 |
| `verbose` / `quiet` | 日志级别，与 `-v` / `-q` 对应（详见设计说明 4.3） |
| `commit_first` / `commit_second` | **过渡**，仅当无 `commits` 时拼接；建议改为 `commits` |
| `hash_prefix_len` | **过渡**，映射为 `tag_min_len` |

使用配置文件运行：

```bash
python3 src/git_export_merge_two_commits.py --config ./git_export_merge.config.json
```

也可通过环境变量 **`GIT_EXPORT_MERGE_CONFIG`** 指向你的配置文件路径，省去每次写 `--config`。

**优先级**：命令行里**显式写出**的参数会覆盖配置文件中的同名字段。

## 测试

在仓库根目录执行：

```bash
python3 tests/test_smoke.py
```

测试会创建临时 Git 仓库（两次提交修改同一文件），验证 `--dry-run` 与实导出、冲突拆分行为。

## 设计文档

详见 [docs/设计说明.md](docs/设计说明.md)。

## 版本

以 `src/git_export_merge_two_commits.py` 内 `__version__` 为准。
