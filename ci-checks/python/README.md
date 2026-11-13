# Python 模板

[`ci-checks`](../README.md) 语言级各工具的 Python 配置，由 reusable workflow [`python.yml`](../../.github/workflows/python.yml) 从 repo-guards 读取，不用合并进 repo 的 `pyproject.toml`；接入见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)。`python.yml` 把 [`pyproject.toml`](pyproject.toml) 的 `dev` 组装进单独的环境。项目有 `uv.lock` 就用 `uv sync --locked` 装；没有就用 `uv venv` 建 `.venv`，再 `uv pip install -r requirements.txt`，不要求项目有 `pyproject.toml`。之后各步都用这个 `.venv`。各节写明它由哪一步跑；顺序同主表。各步在哪个 job 见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)下的 job 表。

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

`python.yml` 的 Type check 步只查 `source-dirs`（覆盖文件里 `[project."<working-directory>"]` 节的 `source-dirs`，默认 `["src"]`，见[覆盖默认配置](../README.md#覆盖默认配置)） 里的目录。`# type: ignore`、`# pyright: ignore` 算行内豁免。

## 死代码（vulture、deptry）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.vulture]`：`paths` 由 `source-dirs`代替，`exclude` 排除测试文件（为什么见[死代码](../README.md#死代码)）；`min_confidence = 60` 报全部类型（未用的函数、类、变量是 60%，未用的 import 是 90%）。deptry 不用配置：依赖从 repo 的 `pyproject.toml` 读，没有就读 `requirements.txt`，只扫 `source-dirs`。两者的版本固定在 `dev` 组 |

`python.yml` 的 Dead code 步跑 vulture，Unused dependencies 步跑 deptry。

vulture 的 `# noqa` 和白名单文件、deptry 的 `ignore` 配置算行内豁免。

## 许可证（pip-licenses）

[`license-check/`](license-check/) 判定每个包的许可证是否被 [`licenses.toml`](../licenses.toml) 的 `allow` 满足：一个只用标准库的脚本，要 Python 3.11 及以上。

| 项 | 内容 |
|---|---|
| 输入 | `pip-licenses --from all --format json` 的输出。每个包按顺序取一种：PEP 639 的 `License-Expression`；没有就取 trove classifier 名（多个用 `; ` 隔开，有一个满足就过）；再没有就取 `License` 元数据，整段当一个许可证，不拆 |
| 判定 | classifier 名先按 `python-classifiers` 换成 SPDX，再当 SPDX 表达式算：`OR` 有一边在 `allow` 里就过，`AND` 要每边都在，`AND` 比 `OR` 先结合，可加括号；`<id> WITH <exception>` 整体要在 `allow` 里。标识符和运算符不分大小写；解析不了的（如自由文本）不过 |
| 参数 | `license_check.py LICENSES_TOML PIP_LICENSES_JSON` |
| 输出 | 不过的包逐个列出（名字、版本、许可证）。退出码：0 通过，1 有包不过，2 出错（文件缺失、JSON 或 TOML 不对、缺键） |
| 测试 | 在该目录跑 `python3 -m unittest license_check_test` |

`python.yml` 的 Licenses 步用 pip-licenses 列出项目环境里的包，跳过项目自己（有 `pyproject.toml` 时）和[`licenses.toml`](../licenses.toml) 的 `exceptions`（默认空，项目在覆盖文件的 `["ci-checks/licenses.toml"]` 节写，见[覆盖默认配置](../README.md#覆盖默认配置)）里的包，再交给本脚本判定。`allow` 里不写 `OR`、`AND` 表达式；带例外的许可证整体写一条，如 `Apache-2.0 WITH LLVM-exception`。

## 依赖白名单和漏洞（pylock.toml）

Export the resolved dependencies 步把项目解析好的依赖写成 `pylock.toml`：有 `uv.lock` 用 `uv export --frozen --format pylock.toml --no-emit-workspace`（默认的 extra 和依赖组，不含项目自己）；没有就用 `uv pip compile requirements.txt --universal --format pylock.toml`，按所有平台解析（`.venv` 的 Python 是最低版本），带平台条件的依赖（如 `foo; sys_platform == "win32"`）也在里面。merge-base 的副本照样导出。Vulnerabilities 步用 `pip-audit --locked` 查 PR 这份。Dependency allowlist 步比的是 `uv.lock` 本身（全部 extra 和依赖组都在里面，`editable`、`virtual` 来源的项目自己不算），没有 `uv.lock` 就比 `pylock.toml`，规则见[依赖白名单](../README.md#依赖白名单)。`requirements.txt` 里没写死的版本按当时的 index 解析，两份同时解析，所以多出来的包只来自 PR 的改动。

## 覆盖率（coverage.py）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.coverage.run]`：`branch = true` 算分支；`relative_files = true` 让报告里的路径相对项目目录（`working-directory`），diff-cover 也在那里跑，才对得上；`omit` 排除测试文件和 protobuf 生成文件。`source` 由 `source-dirs`代替，没被 import 的发布文件也进分母。coverage.py 的版本固定在 `dev` 组 |

`python.yml` 的 Coverage 和 Coverage gate 两步跑覆盖率（判定规则见[覆盖率](../README.md#覆盖率)）：Coverage 在 PR 上用项目自己的环境跑 `coverage run -m pytest`，pytest 来自 repo 自己的 lockfile 或 `requirements.txt`，没装 pytest 的 `requirements.txt` 项目用共用 `dev` 组固定的版本；`pytest` 不带参数，repo 自己的 pytest 配置（`testpaths`、`addopts`）选中的测试就是覆盖率量的测试，所以这份配置只选单元测试，集成测试由 repo 自己的 job 按路径或标记另跑（如 `pytest tests/integration`，命令行给的路径优先于 `testpaths`）；插件自动加载是关掉的（`PYTEST_DISABLE_PLUGIN_AUTOLOAD`，见[依赖检查规则](../README.md#依赖检查规则)），要用的插件在 repo 自己的 pytest 配置里用 `-p` 列出；Coverage gate 判定。之后的 CRAP report 用同一份报告只出报告，见 [CRAP 分数](../README.md#crap-分数)。`# pragma: no cover` 算行内豁免。

## 分层规则（import-linter）

[架构第 2 项](../../architecture/README.md#第-2-项分层规则)的 Python 实现。规则用 [import-linter](https://import-linter.readthedocs.io/) 自己的写法，写在项目目录（`working-directory`）里：

| 配置放哪 | 写法 |
|---|---|
| `.importlinter` | INI：`[importlinter]` 节写 `root_package` 或 `root_packages`，每条规则一个 `[importlinter:contract:<id>]` 节 |
| `pyproject.toml` | TOML：`[tool.importlinter]` 表，规则写在 `[[tool.importlinter.contracts]]` 里 |

两个都有时读 `.importlinter`，和 import-linter 自己的顺序一样。只看项目目录，不往上级、下级目录找；`setup.cfg` 不读。"哪层可以 import 哪层"用 `forbidden`、`layers`、`independence` 等规则写。已有的违规用规则自己的 `ignore_imports` 豁免，或者不写那条规则。

`python.yml` 的 Layer rules 步跑 [`layer-rules/layer_rules.py`](layer-rules/layer_rules.py)：

- 没有配置：打印一行 `layer rules (python): no import-linter config in .importlinter or pyproject.toml [tool.importlinter]; skipped`，通过。
- 有配置：对整个项目跑一次 import-linter，规则不满足或配置被拒绝都失败（退出 1）；`pyproject.toml` 不是合法 TOML、装不上工具退出 2。
- 找包：在项目目录和 `source-dirs`（见[覆盖默认配置](../README.md#覆盖默认配置)）里找 `root_packages`，src 布局不用装项目。
- 不执行项目的代码：脚本用 `python -I` 跑，不经 import-linter 的命令行（它把当前目录放到 `sys.path` 最前面），找到的项目模块都没有 loader，import 它们不会运行。所以 `contract_types` 只能用 import-linter 自带的规则类型，指向项目代码的会失败。
- 输出包在 `::stop-commands::` 里，PR 的文件名和源码行当不了 workflow 命令。
- 版本：import-linter 固定在 [`layer-rules/requirements.in`](layer-rules/requirements.in)，[`requirements.txt`](layer-rules/requirements.txt) 带 hash（在该目录跑 `uv pip compile requirements.in -o requirements.txt --generate-hashes --universal --python-version 3.14` 生成），装进单独的 Python 3.14 环境，不和项目的依赖混装。
- 测试：在该目录建好装了 `requirements.txt` 的 Python 3.14 环境，用它跑 `python -m unittest layer_rules_test`。

## 读 pyproject.toml（pyproject-config）

[`pyproject-config/`](pyproject-config/) 是 `python.yml` 读 TOML 用的脚本，只用标准库，要 Python 3.11 及以上。

| 项 | 内容 |
|---|---|
| `pin PYPROJECT PREFIX` | 打印 `dev` 组里第一个以 `PREFIX` 开头的条目，如 `pytest==`；Unused dependencies 步和 Coverage 步用它取 deptry、coverage.py、pytest 的固定版本 |
| `project-name PYPROJECT` | 打印 `project.name`，没有就打印空行；Licenses 步用它跳过项目自己 |
| `pyright-config PYPROJECT OUTPUT [DIR ...]` | 把 `[tool.pyright]` 写成 JSON，`extraPaths` 换成各 `DIR` 的绝对路径；Type check 步用它 |
| 退出码 | 0 成功；2 出错（参数不对、文件缺失、TOML 不对、缺键、`pin` 找不到） |
| 测试 | 在该目录跑 `python3 -m unittest pyproject_config_test` |
