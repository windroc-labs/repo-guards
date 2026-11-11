# 流程级检查模板

[`ci-checks`](../README.md) 流程级的"行内豁免"和"配置只在锁住的路径"两项。`check.py` 和 `languages/` 各 repo 原样复制，不改；每个 repo 只改 `config.json`。

1. 把本目录复制进 repo 的 `.github/ci-checks/`。
2. 改 `config.json`：
   - `languages`：用到的语言（`languages/` 下的文件名）。
   - `allowed_config_paths`：允许放工具配置的路径，可用通配符。
   - `exemptions`：代码里每一处豁免一条，写 `tool`、`path`、`code`（规则编号，没有编号写 `*`）、`scope`（所在函数，如 `Foo.bar`；不在函数里写 `<module>`）、`count`、`reason`。`python3 .github/ci-checks/check.py exemptions --print-inventory` 打印现有豁免，补上原因即可。
3. `ci.yml` 里加一个 job：

```yaml
  process-checks:
    runs-on: <runner>
    steps:
      - uses: actions/checkout@v4
      - run: python3 .github/ci-checks/check.py exemptions
      - run: python3 .github/ci-checks/check.py config-paths
```

代码里的豁免没登记、次数对不上、没写原因，或者登记了但代码里已经没有，都会失败。所在函数按缩进推断，依赖 formatter 统一的格式。只需要 Python 3 和 git，不装依赖。
