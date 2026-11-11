# 配置只在锁住的路径

[`ci-checks`](../README.md) 流程级"配置只在锁住的路径"一项的实现。`check.py` 和 `config-files.json` 原样复制，不改；每个 repo 只改 `config.json`。

1. 把本目录复制进 repo 的 `.github/config-paths/`。
2. 改 `config.json`：`languages` 填用到的语言（`config-files.json` 里的键）；`allowed` 填允许放工具配置和测试钩子的路径，可用通配符。
3. `ci.yml` 里加一步：`python3 .github/config-paths/check.py`。

`config-files.json` 列出各语言哪些文件名算配置。文件名在里面、路径不在 `allowed` 里，就失败。只需要 Python 3 和 git。
