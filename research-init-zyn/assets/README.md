# {{PROJECT_TITLE}}

> **核心研究问题：** {{RESEARCH_QUESTION}}

**研究目标：** {{RESEARCH_OBJECTIVE}}

**当前阶段：** {{PROJECT_STATUS}}

本仓库用于管理算法实现、实验验证、结果分析与论文撰写。初始化仅创建目录、说明文件与科研模板，算法、测试、方法和实验均按研究进度补充。

## 1. 快速开始

环境：Git、uv、Python 3.11+；本次选择的 Python 次版本为 **{{PYTHON_VERSION}}**，记录于 `.python-version`。依赖声明见 `pyproject.toml`，锁定结果见 `uv.lock`。

在项目根目录执行：

```bash
uv sync --locked
uv lock --check
git status --short
```

本机具备对应 Python 与依赖缓存时可离线恢复：

```bash
uv sync --locked --offline --no-python-downloads
```

项目采用 uv 非打包应用（`[tool.uv] package = false`）。`src` 本身作为 Python 包，后续实现模块后从根目录运行 `uv run --locked python -m src.<module>`。

`scripts/train.sh`、`scripts/evaluate.sh` 和 `tools/plot.py` 目前仅提示尚未实现并返回非零状态；不包含演示算法、虚构数据或已完成实验。空测试目录不代表测试通过。

## 2. 项目目录结构

```text
project/
├── .agents/
│   ├── templates/
│   │   ├── Method.md                  # 方法设计模板
│   │   └── Experiment.md              # 实验记录模板
│   └── import-manifest.json           # 仅导入旧文档后生成
├── AGENTS.md                          # 科研、工程与文档规则的唯一维护源
├── CLAUDE.md                          # 引用 AGENTS.md
├── README.md                          # 项目介绍、结构与使用入口
├── pyproject.toml
├── uv.lock
├── .python-version
├── .gitignore
├── docs/
│   ├── 00_Research_Overview.md
│   ├── 01_Benchmark_Protocol.md
│   ├── 02_Related_Work.md
│   ├── 03_Methods_Experiments/         # 初始为空；按需创建 M/EXP
│   ├── 04_Research_Findings.md
│   └── _imports/                      # 仅导入旧文档后生成，保存原件与提取文本
├── src/
│   ├── dataset/                       # 数据加载与预处理
│   ├── models/                        # 完整模型与 Baseline
│   ├── modules/                       # 可复用算法模块
│   ├── training/                      # 训练流程与损失函数
│   ├── evaluation/                    # 评估指标与流程
│   └── utils/                         # 核心代码共用函数
├── configs/                           # 实验配置，初始为空
├── scripts/
│   ├── train.sh
│   └── evaluate.sh
├── data/
│   ├── raw/                           # 原始数据，初始为空
│   └── dataset.json                    # 数据来源、版本与元信息，待填写
├── tools/
│   └── plot.py                        # 独立科研辅助工具，后续实现绘图
├── runs/                              # 初始为空；实际运行后建立 <run-id>/
│                                      # 每次运行可含 logs/、tensorboards/ 等
├── results/
│   └── figures/
├── tests/
│   ├── unit/
│   └── integration/
└── paper/
    ├── manuscript/
    ├── figures/
    └── supplementary/
```

基础目录（含空目录）、空 `__init__.py` 及其他静态文件直接复制自 skill 的 `assets/`；修改项目结构时直接调整模板树即可。`pyproject.toml`、`.python-version`、`uv.lock` 和 `.git/` 动态生成；可选导入产物与方法、实验、Run 目录按实际需要生成。不使用 `.gitkeep`。

`src/utils/` 用于算法共用函数，根目录 `tools/` 用于独立科研辅助工具。

## 3. 开始研究

先完善 [研究总览](docs/00_Research_Overview.md) 与 [基准协议](docs/01_Benchmark_Protocol.md)，再开展方法开发。文档职责、ID 分配、实验与证据管理统一见 [AGENTS.md](AGENTS.md)，README 不重复定义。

原始数据位置与版本记录在 `data/dataset.json`，数据划分与评估协议维护在 Benchmark。每次实际运行的产物放 `runs/<run-id>/`，跨实验汇总放 `results/`，论文材料放 `paper/`。依赖通过 uv 管理，不预装大型框架。

若初始化时导入旧研究文档，相关摘录已归入 `docs/` 的对应章节；原件保存在 `docs/_imports/`，文件哈希及合并位置见 `.agents/import-manifest.json`。历史结果的证据状态以导入记录为准，不视为在新环境中重新验证过。

本次特殊要求与约束见 AGENTS.md；具体研究目标与后续进展以 Research Overview 为准。初始化不创建 Git 提交或远程仓库。

## 4. 文档导航

- [科研与工程规则](AGENTS.md)
- [Research Overview](docs/00_Research_Overview.md)
- [Benchmark Protocol](docs/01_Benchmark_Protocol.md)
- [Related Work](docs/02_Related_Work.md)
- [Methods & Experiments](docs/03_Methods_Experiments/)
- [Research Findings](docs/04_Research_Findings.md)
