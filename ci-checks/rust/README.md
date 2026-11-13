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
| [`deny-merge/`](deny-merge/) | 把 [`licenses.toml`](../licenses.toml) 的 `allow` 写进 `deny.toml` 的 `licenses.allow`；`deny.toml` 自己有 `licenses.allow`（项目在它那一节覆盖了）就用它的。测试：在该目录跑 `python3 -m unittest deny_merge_test` |

`sources.allow-git`、`sources.allow-registry`、`bans.build.allow-build-scripts` 和 `licenses.exceptions`（默认空）都在 `deny.toml` 里，项目在覆盖文件的 `["ci-checks/rust/deny.toml"]` 节改（见[覆盖默认配置](../README.md#覆盖默认配置)）。Dependency rules 步合并后跑 cargo-deny 0.20.2。包的白名单不在这里，由 Dependency allowlist 步查，只查 PR 新增或变了的 crate，见[依赖白名单](../README.md#依赖白名单)。

`allow-build-scripts` 的共用白名单只收基础 crate：大多数项目的依赖树里都有（crates.io 近 90 天下载量约 1.5 亿以上，或 `syn` 1、`generic-array` 0.14 这类仍被大量锁住的旧版本），`build.rs` 只探测编译器和目标平台、设 cfg 或链接路径，不编译原生代码（没有 `cc`、`cmake`、`bindgen` 这类构建依赖）。名单按包名匹配所有版本。`ring`、`openssl-sys` 这类要编译原生代码的，和只在某类项目里出现的（如 `httparse`、`wasm-bindgen`），由项目在覆盖文件里写；覆盖的列表整个替换共用名单，所以连共用名单一起写。

## 覆盖率（cargo-llvm-cov）

| 文件 | 作用 |
|---|---|
| [`coverage.toml`](coverage.toml) | `toolchain`：分支覆盖率（`--branch`）只有 nightly 支持，覆盖率几步用这个固定的 nightly，带 `llvm-tools-preview`，其余步仍用 [`rust-toolchain.toml`](rust-toolchain.toml)；`ignore-filename-regex`：排除 `tests/`、`benches/`、`examples/` 和 `*_test.rs`（cargo-llvm-cov 没有配置文件，`rust.yml` 把它传给 `--ignore-filename-regex`） |
| [`coverage-lcov/`](coverage-lcov/) | 把 cargo-llvm-cov 的 LCOV 报告合并（同一文件同一行、同一分支的次数相加，绝对路径改成相对 `--root`，不要函数记录，因为函数的符号名随目标平台变），写成 LCOV（分片时交给 Coverage job）或 Cobertura（给 Coverage gate 和 CRAP report）。不分片时也走这一步，所以分片和不分片算法相同。只用标准库。测试：在该目录跑 `python3 -m unittest coverage_lcov_test` |

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

`rust.yml` 的 Coverage 和 Coverage gate 两步跑覆盖率（判定规则见[覆盖率](../README.md#覆盖率)）：Coverage 在 PR 上跑 cargo-llvm-cov 0.9.1（`--workspace --all-features --branch --lcov`，加上 `unit-tests` 选的测试目标），用 `coverage-lcov` 转成 Cobertura，Coverage gate 判定。之后的 CRAP report 用同一份报告只出报告，见 [CRAP 分数](../README.md#crap-分数)。加在测试模块之外的 `coverage(off)` 算行内豁免。

覆盖率只量单元测试。`unit-tests` 输入写 repo 的单元测试目标，空格分开，如 `--lib`、`--bins`、`--bin=<名字>`、`--test=<名字>`，原样传给 cargo-llvm-cov。默认 `--lib --bins`，就是 `src` 里的测试模块。只有 bin 的 crate 写 `--bins`：选中的包里一个 lib 都没有时，cargo 对 `--lib` 报错。单元测试放在 `tests/unit.rs` 这类目标里的，再加 `--test=unit`。目标后面可以接 `--` 和测试名过滤，原样传给测试程序：`--test=unit -- input::` 只跑 `unit` 里名字含 `input::` 的测试。分片（见[覆盖率](../README.md#覆盖率)）时用它把一个测试目标按测试名拆成几片。

单元测试只在 Coverage 步里跑一遍，有测试失败这一步就失败；`rust.yml` 不另跑测试。集成测试（没写进 `unit-tests` 的测试目标）、doctest 和 example 由 repo 自己的 job 跑。

## 接口快照

[第 1 项接口快照](../../architecture/README.md#第-1-项接口快照)的 Rust 生成器：[`api-snapshot/`](api-snapshot/)，一个带 `Cargo.lock` 的 cargo 小程序，读 rustdoc JSON，用 `rustdoc-types` 0.61.0（写死 `=`）解析。比对和输出见[接口快照比对](../README.md#接口快照比对)。

| 文件 | 作用 |
|---|---|
| [`api-snapshot.toml`](api-snapshot.toml) | `toolchain`：rustdoc JSON 只有 nightly 能出，API snapshot job 用这个固定的 nightly，和覆盖率的 nightly 分开设。每版 `rustdoc-types` 只读一种 JSON 格式，所以升级时两个一起改，所有启用的调用方要重新生成快照。调用方在覆盖文件的 `["ci-checks/rust/api-snapshot.toml"]` 节改了它，格式对不上时 Generate 退出 2 |

`rust.yml` 的 API snapshot job 设了 `api-snapshot.rust` 才跑这几步：Build the API snapshot tool 装上面的 nightly，在 `/` 下构建生成器（调用方的 `.cargo/config.toml` 管不到它）；Generate 在 `working-directory` 里对 workspace 每个有库 target 的成员（`lib`、`rlib`、`dylib`、`cdylib`、`staticlib`、`proc-macro`）打开全部 feature 跑 `cargo check --lib`，再跑 `cargo rustdoc --lib` 出 JSON，写成条目。构建失败、feature 全开编不过、JSON 格式不对，都退出 2。

条目：

- `unit`：包名。`parent`、`name`：条目的公开路径（从 crate 根算起，不带 crate 名），`parent` 是除最后一段外的部分，crate 根下为 `""`。经 `pub use` 能从几条公开路径到达的条目，每条路径一个条目；最短的那条（一样长取字典序小的）是它的规范路径。
- `kind`：rustdoc 报告的种类，如 `function`、`struct`、`trait`、`impl`、`assoc_type`、`proc_derive`。
- trait 的方法、关联常量和关联类型：上级是 trait 的规范路径。
- 实现：每个 `impl` 一个条目，上级是实现它的类型的规范路径，名字和签名都是 impl 头（`impl core::convert::From<u32> for mycrate::Id`）；impl 里的方法等以这个头为上级，所以 `From<u32>` 和 `From<String>` 的 `from`、类型自己的 `len` 和 trait 实现里的 `len` 键都不同。同一类型的几个固有 `impl` 块合成一个。
- 别的 crate 的条目被 `pub use` 出来时，种类照 rustdoc 对目标的报告，签名是 `pub use <路径>`；glob 的名字是 `<源路径>::*`。显式的 `pub use` 和本模块的条目照 Rust 的规则按命名空间（类型、值、宏）遮住 glob 带进来的同名条目，被遮住的不进快照；glob 带进来的条目只要主命名空间（类型的类型、函数的值）被占，就整个不进，即使元组结构体的构造函数还看得见。
- 签名：泛型参数和约束、where 子句（rustdoc 把写在参数上的约束也并进 where）、公开字段（有私有字段时写 `/* private fields */`）、枚举成员和判别值（算出的值）、`const`/`async`/`unsafe`/`extern`、trait 方法的默认实现（`{ ... }`）。本 crate 的类型用规范路径，别的 crate 的类型用 rustdoc 记的定义路径（`core::result::Result`），所以改 `use` 写法、把代码挪到别的文件再 `pub use` 出来，签名都不变。参数只记类型不记名字：调用方依赖不到参数名。

不进快照：文档注释、源文件位置、私有和 `#[doc(hidden)]` 条目、blanket impl、不稳定的自动 trait（`Freeze`、`UnsafeUnpin`）；自动 trait 的 impl（`Send`、`Sync`、`Unpin`、`UnwindSafe`、`RefUnwindSafe`）不带 where 子句，因为 rustdoc 是从私有字段的类型推出来的。宏的签名是 rustdoc 给的各分支模式，关联常量的默认值是 rustdoc 印出的文字，改它们的写法也算签名变化。既没挂在本 crate 公开类型上、也没挂在公开 trait 上的 impl（如别的 crate 的 trait 为 `&Id` 实现）查不到。升级 nightly 时 rustdoc 记的标准库路径可能变，快照要重新生成。

冻结范围（`api-snapshot-scope.rust`）：条目怎样归到模块，包括 impl 的归属，见[冻结范围](../README.md#冻结范围)。生成器还没接上，设了也照旧写全部条目。

测试：在 `api-snapshot/` 跑 `RUSTUP_TOOLCHAIN=<api-snapshot.toml 的 toolchain> cargo test --locked`（要先装这个 nightly 和 `python3`），fixture 项目在 `api-snapshot/tests/fixtures/`。
