# Rust 模板

[`ci-checks`](../README.md) 语言级各工具的 Rust 配置，由 reusable workflow [`rust.yml`](../../.github/workflows/rust.yml) 从 repo-guards 读取，不用复制；接入见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)。各节写明它由哪一步跑；顺序同主表。各步在哪个 job 见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)下的 job 表。

## formatter（rustfmt）

| 文件 | 作用 |
|---|---|
| [`rustfmt.toml`](rustfmt.toml) | 格式选项，都是 stable 版可用的；`edition` 不写，`cargo fmt` 从 `Cargo.toml` 读 |
| [`rust-toolchain.toml`](rust-toolchain.toml) | 固定工具链版本，`rust.yml` 按它装 rustc、rustfmt 和 clippy。不固定的话，升级后同样的代码可能突然不过 |

`rust.yml` 的 Format 步只检查不改写，PR 改过的文件格式不对就失败（见[只查改动行](../README.md#只查改动行)）。`#[rustfmt::skip]` 算行内豁免。

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

`rust.yml` 的 Comment budget 步用本目录的检查器和配置，对 PR 的 base 跑。超标时用删注释来过，不要靠加代码压低比例。

## 类型检查（编译器）

没有模板：类型检查就是编译器本身，不靠配置；`deprecated` 默认只是警告，改动行上的警告让 `rust.yml` 的 Lint 步（clippy）和 Type check and dead code 步失败（见[只查改动行](../README.md#只查改动行)）。

## 死代码（编译器、cargo-machete）

不用模板。`rust.yml` 的 Type check and dead code 步跑不带 `--tests` 的 `cargo check`（为什么见[死代码](../README.md#死代码)）。Unused dependencies 步跑 cargo-machete 0.9.2，不用配置。

`#[allow(dead_code)]` 和 `[package.metadata.cargo-machete] ignored` 算行内豁免。

## 依赖检查（cargo-deny）

| 文件 | 作用 |
|---|---|
| [`deny.toml`](deny.toml) | 共用规则：只准 crates.io，未知的 git 和 registry 都拒；带 `build.rs` 的 crate 只准共用白名单 `bans.build.allow-build-scripts` 里的（见下）；许可证只准 [`licenses.toml`](../licenses.toml) 的 `allow`；漏洞和 yanked 的版本都失败 |
| [`deny-merge/`](deny-merge/) | 把 [`licenses.toml`](../licenses.toml) 的 `allow` 和 repo 自己的白名单并进 `deny.toml`；白名单由 [`allowlist.py`](../allowlist/allowlist.py) 读和校验，只准往[依赖白名单](../README.md#依赖白名单)的五个列表里加，写别的键就失败。测试：在该目录跑 `python3 -m unittest deny_merge_test` |

repo 自己的白名单是 `rust.yml` 的 `deny-allowlist` 输入（默认 `.github/checks/deny-allowlist.toml`），Python 和 TypeScript 读同一个文件，见[依赖白名单](../README.md#依赖白名单)；文件可以不存在，等于空。Dependency rules 步合并后跑 cargo-deny 0.20.2。`bans.allow` 用 `name@version` 写死版本；它为空时 cargo-deny 不限制包名。

`allow-build-scripts` 的共用白名单只收基础 crate：大多数项目的依赖树里都有（crates.io 近 90 天下载量约 1.5 亿以上，或 `syn` 1、`generic-array` 0.14 这类仍被大量锁住的旧版本），`build.rs` 只探测编译器和目标平台、设 cfg 或链接路径，不编译原生代码（没有 `cc`、`cmake`、`bindgen` 这类构建依赖）。名单按包名匹配所有版本。`ring`、`openssl-sys` 这类要编译原生代码的，和只在某类项目里出现的（如 `httparse`、`wasm-bindgen`），由项目写进自己的白名单；项目白名单接在共用名单后面，重复写共用名单里的包也不报错。

## 覆盖率（cargo-llvm-cov）

| 文件 | 作用 |
|---|---|
| [`coverage.toml`](coverage.toml) | `toolchain`：分支覆盖率（`--branch`）只有 nightly 支持，覆盖率几步用这个固定的 nightly，带 `llvm-tools-preview`，其余步仍用 [`rust-toolchain.toml`](rust-toolchain.toml)；`ignore-filename-regex`：排除 `tests/`、`benches/`、`examples/` 和 `*_test.rs`（cargo-llvm-cov 没有配置文件，`rust.yml` 把它传给 `--ignore-filename-regex`） |

`src` 里的测试模块正则排除不了，每个都要加 `coverage(off)`，crate 根也要开这个 nightly 特性，还要在 `Cargo.toml` 里声明 `coverage_nightly` 这个 cfg，否则 stable 下的 Lint 步报 `unexpected_cfgs`：

```rust
// lib.rs 或 main.rs 开头
#![cfg_attr(coverage_nightly, feature(coverage_attribute))]

#[cfg(test)]
#[cfg_attr(coverage_nightly, coverage(off))]
mod tests { /* … */ }
```

```toml
# Cargo.toml
[lints.rust]
unexpected_cfgs = { level = "warn", check-cfg = ["cfg(coverage_nightly)"] }
```

`rust.yml` 的 Coverage 和 Coverage gate 两步跑覆盖率（判定规则见[覆盖率](../README.md#覆盖率)）：Coverage 先在 merge-base 的副本、再在 PR 上跑 cargo-llvm-cov 0.9.1（`--workspace --all-features --branch`，加上 `unit-tests` 选的测试目标），Coverage gate 判定。之后的 CRAP report 用同一份报告只出报告，见 [CRAP 分数](../README.md#crap-分数)。加在测试模块之外的 `coverage(off)` 算行内豁免。

覆盖率只量单元测试。`unit-tests` 输入写 repo 的单元测试目标，空格分开，只收 `--lib`、`--bins`、`--bin=<名字>`、`--test=<名字>`；别的写法（包括 `--tests` 和会放宽锁住配置的 cargo-llvm-cov 选项）让 Coverage 步失败。默认 `--lib --bins`，就是 `src` 里的测试模块。只有 bin 的 crate 写 `--bins`：选中的包里一个 lib 都没有时，cargo 对 `--lib` 报错。单元测试放在 `tests/unit.rs` 这类目标里的，再加 `--test=unit`。

单元测试只在 Coverage 步里跑一遍，有测试失败这一步就失败；`rust.yml` 不另跑测试。集成测试（没写进 `unit-tests` 的测试目标）、doctest 和 example 由 repo 自己的 job 跑。
