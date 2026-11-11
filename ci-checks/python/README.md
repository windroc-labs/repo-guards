# Python 模板

[`ci-checks`](../README.md) 语言级各工具的 Python 配置。文件里的段落合并进 repo 根目录的 `pyproject.toml`；顺序同主表。

## formatter（ruff format）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.ruff]`、`[tool.ruff.format]` 是格式选项；`[dependency-groups]` 的 `dev` 组固定 ruff 的版本，换成接入时的最新版本号。不固定的话，升级后同样的代码可能突然不过 |

`ci.yml` 里一步（之前用 `uv sync --group dev` 或 `pip install --group dev` 装好 `dev` 组）：

```yaml
- run: ruff format --check .
```

只检查不改写，格式不对就失败。`# fmt: off` / `# fmt: on` 和 `# fmt: skip` 算行内豁免。

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

`comment-budget/` 整个目录复制到 `.github/checks/python/comment-budget/`，`comment-budget.toml` 复制到 `.github/checks/python/comment-budget.toml`。配置文件在 `.github/` 下，CODEOWNERS 锁住，改上限要 owner 批准。`ci.yml` 里一步：

```yaml
- env:
    BASE_REF: ${{ github.event_name == 'pull_request' && format('origin/{0}', github.base_ref) || github.event.before }}
  run: python3 .github/checks/python/comment-budget/comment_budget.py --base "$BASE_REF"
```

checkout 要拉到 base 的历史（`fetch-depth: 0`），否则找不到 merge-base。超标时用删注释来过，不要靠加代码压低比例。

## 死代码（vulture、deptry）

| 文件 | 作用 |
|---|---|
| [`pyproject.toml`](pyproject.toml) | `[tool.vulture]`：`paths` 换成发布代码目录，`exclude` 排除测试文件，只有测试调用的函数也会报；`min_confidence = 60` 报全部类型（未用的函数、类、变量是 60%，未用的 import 是 90%）。deptry 不用配置：依赖从 `pyproject.toml` 读，默认不扫根目录的 `tests`。两者的版本固定在 `dev` 组 |

`ci.yml` 里一步（之前装好 `dev` 组）：

```yaml
- run: vulture && deptry .
```

vulture 的 `# noqa` 和白名单文件、deptry 的 `ignore` 配置算行内豁免。

## 重复代码（jscpd）

| 文件 | 作用 |
|---|---|
| [`../.jscpd.json`](../.jscpd.json) | 三种语言共用。`minTokens`、`minLines` 是门槛，短于它的重复不算；`ignore` 排除测试文件；`baseline` 是重复基线的路径，相对配置文件所在目录；`failOnNewClones` 为 0：不在基线里的重复都失败 |

复制到 `.github/checks/jscpd.json`，基线 `.github/checks/jscpd-baseline.json` 和它放在同一目录，CODEOWNERS 锁住。基线要 jscpd 5.1.0 及以上（Rust 引擎；4.x 的 Node 引擎没有 `--baseline`），PyPI 上的 `jscpd` 是同一个二进制，版本固定在 `dev` 组。接入时生成基线，之后只能变小：

```sh
jscpd --config .github/checks/jscpd.json --update-baseline .
```

`ci.yml` 里一步（之前装好 `dev` 组）：

```yaml
- run: jscpd --config .github/checks/jscpd.json .
```

基线文件不存在时直接失败。`ignorePattern` 算行内豁免。
