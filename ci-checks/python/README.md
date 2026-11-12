# Python 模板

[`ci-checks`](../README.md) 语言级各工具的 Python 配置，由 reusable workflow [`python.yml`](../../.github/workflows/python.yml) 从 repo-guards 读取，不用合并进 repo 的 `pyproject.toml`；接入见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)。`python.yml` 把 [`pyproject.toml`](pyproject.toml) 的 `dev` 组装进单独的环境，项目本身用 `uv sync --locked` 只装 wheel。各节写明它由哪一步跑；顺序同主表。

## formatter（ruff format）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.ruff]`、`[tool.ruff.format]` 是格式选项；`[dependency-groups]` 的 `dev` 组固定 ruff 的版本。不固定的话，升级后同样的代码可能突然不过 |

`python.yml` 的 Format 步只检查不改写，PR 改过的文件格式不对就失败（见[只查改动行](../README.md#只查改动行)）。`# fmt: off` / `# fmt: on` 和 `# fmt: skip` 算行内豁免。

## 注释预算

[`comment-budget/`](comment-budget/) 是 [`ci-checks`](../README.md#语言级) 第 3 项"注释预算"的 Python 实现：一个只用标准库的脚本，要 Python 3.11 及以上（读配置用 `tomllib`）。

| 项 | 内容 |
|---|---|
| 量什么 | 每个改动的 `.py` 文件：注释字符数对比代码字符数，空白不算。改动文件 = 和 `--base` 的 merge-base 比 diff，删掉的不算 |
| 注释 | `#` 注释（用 `tokenize` 找），以及 docstring 和其他单独成句的字符串（用 `ast` 找）。赋值、传参、返回里的字符串不算；第一行的 `#!` shebang 算代码。解析不了的文件算出错 |
| 测试 | 路径匹配 `test_paths` 的文件，整个文件算测试 |
| 上限 | 每个文件：源码代码字符的 `source_percent`% 加测试代码字符的 `test_percent`%，再加 `allowance_chars` 个字符的固定额度。超了就失败 |
| 参数 | `--base`（默认 `origin/main`）、`--config`（配置文件路径，相对当前目录；默认 repo 根目录下的 `.github/checks/python/comment-budget.toml`）、`--all`（查全部已跟踪文件，不看 diff） |
| 输出 | 超标文件逐个列出；最后给源码和测试各自的总比例。退出码：0 通过，1 超标，2 出错（含配置文件缺失、未知键、缺键、类型不对） |

设置全在配置文件里，每个键都必须写。模板 [`comment-budget.toml`](comment-budget.toml) 是默认值：

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `source_percent` | 整数 | 10 | 源码注释上限，占源码代码字符的百分比 |
| `test_percent` | 整数 | 5 | 测试注释上限，占测试代码字符的百分比 |
| `allowance_chars` | 整数 | 300 | 每个文件的固定额度（字符） |
| `test_paths` | 字符串数组 | `["**/tests/**", "**/test_*.py", "**/*_test.py"]` | 整个文件算测试的路径 glob，相对 repo 根目录。`**` 跨目录，`*` 不跨 |

格式是 TOML，只认这四个顶层键；整数不能为负。

`python.yml` 的 Comment budget 步用本目录的检查器和配置，对 PR 的 base 跑。超标时用删注释来过，不要靠加代码压低比例。

## 类型检查（pyright）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.pyright]` 开 `typeCheckingMode = "strict"`，再加 strict 不开的 `reportUnnecessaryTypeIgnoreComment`，对应 [类型检查规则](../README.md#类型检查规则) 的 Python 一列；`[dependency-groups]` 的 `dev` 组同样固定 pyright 的版本 |

`python.yml` 的 Type check 步只查 `source-dirs` 输入里的目录（默认 `src`）。`# type: ignore`、`# pyright: ignore` 算行内豁免。

## 死代码（vulture、deptry）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.vulture]`：`paths` 由 `source-dirs` 输入代替，`exclude` 排除测试文件（为什么见[死代码](../README.md#死代码)）；`min_confidence = 60` 报全部类型（未用的函数、类、变量是 60%，未用的 import 是 90%）。deptry 不用配置：依赖从 repo 的 `pyproject.toml` 读，只扫 `source-dirs`。两者的版本固定在 `dev` 组 |

`python.yml` 的 Dead code 步跑 vulture，Unused dependencies 步跑 deptry。

vulture 的 `# noqa` 和白名单文件、deptry 的 `ignore` 配置算行内豁免。
