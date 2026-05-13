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

本仓库根目录提供 **`config.example.json`**，请复制为例如 `git_export_merge.config.json`（文件名自定），再修改其中字段：

| 字段 | 说明 |
|------|------|
| `repo` | 要导出的 **Git 仓库根路径**（相对路径相对于**配置文件所在目录**，也可用绝对路径） |
| `commits` | 参与比较的提交 rev 列表（字符串数组），顺序见 `docs/设计说明.md` |
| `output` | 导出目标目录（相对路径同样相对配置文件所在目录） |
| `tag_min_len` / `tag_max_len` | 冲突文件名中提交哈希前缀长度范围 |
| `dry_run` | `true` 时只统计不写盘 |
| 其余 | 见示例文件内注释与 `python3 src/... --help` |

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
