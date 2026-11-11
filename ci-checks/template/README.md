# 流程级检查模板

[`ci-checks`](../README.md) 流程级的"行内豁免"和"配置只在锁住的路径"两项。

1. 把本目录复制进 repo 的 `.github/ci-checks/`。
2. 改 `config.json`：`languages` 填用到的语言（`languages/` 下的文件名）；`allowed_config_paths` 填允许放工具配置的路径（可用通配符）；`exemption_allowlist` 每条写 `tool`、`path`、`max`、`reason`。
3. `ci.yml` 里加一个 job：

```yaml
  process-checks:
    runs-on: <runner>
    steps:
      - uses: actions/checkout@v4
        with:
          fetch-depth: 0
      - run: python3 .github/ci-checks/check.py exemptions --base origin/${{ github.base_ref }}
      - run: python3 .github/ci-checks/check.py config-paths
```

豁免按文件数：某个文件里某类豁免比 merge-base 多就失败，白名单里的按 `max` 算。只需要 Python 3 和 git，不装依赖。
