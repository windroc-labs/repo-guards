# Rust 模板

[`ci-checks`](../README.md) 语言级各工具的 Rust 配置。文件复制到 repo 根目录；顺序同主表。

## formatter（rustfmt）

| 文件 | 作用 |
|---|---|
| [`rustfmt.toml`](rustfmt.toml) | 格式选项，都是 stable 版可用的；`edition` 不写，`cargo fmt` 从 `Cargo.toml` 读 |
| [`rust-toolchain.toml`](rust-toolchain.toml) | 固定 rustfmt 和 clippy 的版本；`channel` 换成接入时的 stable 版本号。不固定的话，升级后同样的代码可能突然不过 |

`ci.yml` 里一步：

```yaml
- run: cargo fmt --all --check
```

只检查不改写，格式不对就失败。`#[rustfmt::skip]` 算行内豁免。
