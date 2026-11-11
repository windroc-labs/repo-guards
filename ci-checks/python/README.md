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
