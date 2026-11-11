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

## 注释预算

[`comment-budget/`](comment-budget/) 是 linter 规则表里"注释预算"的 Rust 实现：一个不带依赖的 cargo 小程序，自带词法分析。

| 项 | 内容 |
|---|---|
| 量什么 | 每个改动的 `.rs` 文件：注释字符数对比代码字符数，空白不算。改动文件 = 和 `--base` 的 merge-base 比 diff，删掉的不算 |
| 注释 | `//`、`/* */`（可嵌套）和文档注释 `///`、`//!`、`/** */`、`/*! */`。字符串、raw 字符串、byte 字符串、字符字面量里的不算；第一行的 `#!` shebang 算代码 |
| 测试 | `tests/` 目录下的文件、`*_test.rs`，以及 `#[cfg(test)] mod 名字 { … }` 行内模块的内容。`#[cfg(test)] mod 名字;` 指向的文件不认，除非路径本身符合上面两条 |
| 上限 | 每个文件：源码代码字符的 10% 加测试代码字符的 5%，再加 300 个字符的固定额度。超了就失败 |
| 参数 | `--base`（默认 `origin/main`）、`--source-limit`（默认 10）、`--test-limit`（默认 5）、`--all`（查全部已跟踪文件，不看 diff） |
| 输出 | 超标文件逐个列出；最后给源码和测试各自的总比例。退出码：0 通过，1 超标，2 出错 |

整个目录复制到 `.github/checks/rust/comment-budget/`，`ci.yml` 里一步：

```yaml
- env:
    BASE_REF: ${{ github.event_name == 'pull_request' && format('origin/{0}', github.base_ref) || github.event.before }}
  run: cargo run --release --locked --manifest-path .github/checks/rust/comment-budget/Cargo.toml -- --base "$BASE_REF"
```

checkout 要拉到 base 的历史（`fetch-depth: 0`），否则找不到 merge-base。超标时用删注释来过，不要靠加代码压低比例。
