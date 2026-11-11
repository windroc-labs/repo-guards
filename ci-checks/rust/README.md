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

[`comment-budget/`](comment-budget/) 是 [`ci-checks`](../README.md#语言级) 第 3 项"注释预算"的 Rust 实现：一个不带依赖的 cargo 小程序，自带词法分析。

| 项 | 内容 |
|---|---|
| 量什么 | 每个改动的 `.rs` 文件：注释字符数对比代码字符数，空白不算。改动文件 = 和 `--base` 的 merge-base 比 diff，删掉的不算 |
| 注释 | `//`、`/* */`（可嵌套）和文档注释 `///`、`//!`、`/** */`、`/*! */`。字符串、raw 字符串、byte 字符串、字符字面量里的不算；第一行的 `#!` shebang 算代码 |
| 测试 | 路径匹配 `test_paths` 的文件，以及 `#[cfg(test)] mod 名字 { … }` 行内模块的内容（这条固定，不可配）。`#[cfg(test)] mod 名字;` 指向的文件不认，除非路径本身匹配 `test_paths` |
| 上限 | 每个文件：源码代码字符的 `source_percent`% 加测试代码字符的 `test_percent`%，再加 `allowance_chars` 个字符的固定额度。超了就失败 |
| 参数 | `--base`（默认 `origin/main`）、`--config`（配置文件路径，相对当前目录；默认 repo 根目录下的 `.github/checks/rust/comment-budget.toml`）、`--all`（查全部已跟踪文件，不看 diff） |
| 输出 | 超标文件逐个列出；最后给源码和测试各自的总比例。退出码：0 通过，1 超标，2 出错（含配置文件缺失、未知键、缺键、类型不对） |

设置全在配置文件里，每个键都必须写。模板 [`comment-budget.toml`](comment-budget.toml) 是默认值：

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `source_percent` | 整数 | 10 | 源码注释上限，占源码代码字符的百分比 |
| `test_percent` | 整数 | 5 | 测试注释上限，占测试代码字符的百分比 |
| `allowance_chars` | 整数 | 300 | 每个文件的固定额度（字符） |
| `test_paths` | 字符串数组 | `["**/tests/**", "**/*_test.rs"]` | 整个文件算测试的路径 glob，相对 repo 根目录。`**` 跨目录，`*` 不跨 |

格式是 TOML 的一个子集：顶层 `键 = 值`，值只有整数和字符串数组，`#` 注释，空行。

`comment-budget/` 整个目录复制到 `.github/checks/rust/comment-budget/`，`comment-budget.toml` 复制到 `.github/checks/rust/comment-budget.toml`。配置文件在 `.github/` 下，CODEOWNERS 锁住，改上限要 owner 批准。`ci.yml` 里一步：

```yaml
- env:
    BASE_REF: ${{ github.event_name == 'pull_request' && format('origin/{0}', github.base_ref) || github.event.before }}
  run: cargo run --release --locked --manifest-path .github/checks/rust/comment-budget/Cargo.toml -- --base "$BASE_REF"
```

checkout 要拉到 base 的历史（`fetch-depth: 0`），否则找不到 merge-base。超标时用删注释来过，不要靠加代码压低比例。

## 死代码（编译器、cargo-machete）

不用模板。编译器的 `dead_code` 只有在 CI 用 `-D warnings` 跑时才让构建失败；不带 `--tests`，`#[cfg(test)]` 的代码不参与编译，只有测试调用的函数也会报。cargo-machete 不用配置。`ci.yml` 里两步：

```yaml
- env:
    RUSTFLAGS: -D warnings
  run: cargo check --workspace --all-features
- run: cargo install cargo-machete --version 0.9.2 --locked && cargo machete
```

`#[allow(dead_code)]` 和 `[package.metadata.cargo-machete] ignored` 算行内豁免。

## 重复代码（jscpd）

| 文件 | 作用 |
|---|---|
| [`../.jscpd.json`](../.jscpd.json) | 三种语言共用。`minTokens`、`minLines` 是门槛，短于它的重复不算；`ignore` 排除测试文件；`baseline` 是重复基线的路径，相对配置文件所在目录；`failOnNewClones` 为 0：不在基线里的重复都失败 |

复制到 `.github/checks/jscpd.json`，基线 `.github/checks/jscpd-baseline.json` 和它放在同一目录，CODEOWNERS 锁住。基线要 jscpd 5.1.0 及以上（Rust 引擎；4.x 的 Node 引擎没有 `--baseline`），版本固定为 5.4.3。接入时生成基线，之后只能变小：

```sh
jscpd --config .github/checks/jscpd.json --update-baseline .
```

`ci.yml` 里一步：

```yaml
- run: cargo install jscpd --version 5.4.3 --locked && jscpd --config .github/checks/jscpd.json .
```

基线文件不存在时直接失败。`ignorePattern` 算行内豁免。
