# git-export-merge

将多个 Git 提交（版本快照）做**路径并集导出**：同一相对路径若内容不同则拆成多份文件，并用提交哈希前缀区分。

## 目录结构

```
git-export-merge/
├── README.md                 # 本文件
├── docs/
│   └── 设计说明.md           # 目标、语义、配置优先级与实现要点
├── src/
│   └── git_export_merge_two_commits.py
├── examples/
│   └── git_export_merge.config.example.json
└── tests/
    └── test_smoke.py         # 冒烟测试（临时仓库 + dry-run / 实导出）
```

## 环境要求

- Python 3.9+
- 已安装 `git` 命令并在 `PATH` 中

## 快速开始

```bash
cd /Users/Licw/Desktop/git/git-export-merge

# 查看帮助
python3 src/git_export_merge_two_commits.py --help

# 干跑（不写盘）
python3 src/git_export_merge_two_commits.py \
  --repo /path/to/your/repo \
  --commit REV1 --commit REV2 \
  -o /path/to/export-dir \
  --dry-run
```

## 配置文件

复制 `examples/git_export_merge.config.example.json` 并按需修改。相对路径 `repo`、`output` 相对于**配置文件所在目录**解析。

命令行一旦显式传入某参数，即覆盖配置文件中的同名字段。

也可通过环境变量 `GIT_EXPORT_MERGE_CONFIG` 指定默认配置文件路径。

## 测试

在项目根目录执行：

```bash
python3 tests/test_smoke.py
```

测试会创建临时 Git 仓库（两次提交修改同一文件），验证脚本的 `--dry-run` 与实导出、冲突拆分行为。

## 设计文档

详见 [docs/设计说明.md](docs/设计说明.md)。

## 版本

以 `src/git_export_merge_two_commits.py` 内 `__version__` 为准。
