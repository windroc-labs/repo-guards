# 代码结构

第 4 层：项目自己的架构设计，和照着设计做的检查。项目做完这一层的设计才适用。

## 设计规则

- 每层一个出口。
- 具体实现（比如某款芯片）通过工厂交出接口对象，上层叫不出实现的名字。
- 每个接口一套契约测试，所有实现跑同一套。

## CODEOWNERS

| 项 | 要求 | 防什么 |
|---|---|---|
| CODEOWNERS | 下表每项启用的检查，"标准答案"一列里的文件（接口快照、架构规则配置、契约测试目录等）都进 CODEOWNERS。配置文件用不带 `/` 的文件名模式，任何深度都匹配。实现文件不锁 | 不锁的话，worker 改接口、改分层规则、改契约测试都不用 owner 批准 |

## 检查

和第 3 层的检查一起在 repo 的 `.github/workflows/ci.yml` 里跑。比对方式、"频率"一列和"按项目需要"的含义同[第 3 层](../ci-checks/README.md)。

| # | 检查 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 |
|---|---|---|---|---|---|
| 1 | 接口快照 | 用公开 API 工具从构建产物生成完整签名（参数类型、返回类型、默认值），和快照逐项比对：多、少、签名变了都失败。不靠数个数 | 接口快照 | 偷偷增删或改对外接口 | 每个 PR |
| 2 | [分层规则](#第-2-项分层规则) | 只经接口使用下层，不越层，没有循环依赖 | 架构规则配置 | 越层调用、绕开接口 | 每个 PR |
| 3 | 分层覆盖全部模块 | 每个源码模块在架构规则里恰好归一层；没归层的失败 | 架构规则配置 | 新模块不归层，分层规则管不到 | 每个 PR |
| 4 | 实现名不越过工厂 | 具体实现的名字只出现在它自己的包、工厂和它自己的测试里；工厂之上不对接口对象做向下转型或按类型分支 | 架构规则配置 | 上层直接用具体实现，绕开接口 | 每个 PR |
| 5 | 副作用只在适配层 | 时钟、随机数、文件系统、网络、子进程、硬件 API 只在指定的适配模块里用 | 架构规则配置 | 副作用散在业务代码里，测试替换不了，结果随环境变 | 每个 PR |
| 6 | 测试边界 | 测试只 import 本单元的出口、下层接口和测试支持代码；只 mock 下层接口，不 mock 被测单元，也不 mock CI 工具 | 架构规则配置 | mock 掉被测代码或 CI 工具，测试测不到真东西 | 每个 PR |
| 7 | 契约测试矩阵 | 静态找出每个接口的全部实现（含 fake 和测试替身），每个都要出现在契约测试的结果里；接口快照里的每个接口都有一套契约测试；只跑契约测试，就要执行到每个接口方法 | 契约测试目录、接口快照 | 新实现不跑契约、接口没有契约、契约漏测方法、改弱契约 | 每个 PR |
| 8 | schema 和迁移兼容（按项目需要） | 新 schema 能读旧数据；迁移能从上一个发布版本升级 | 已发布的 schema 快照 | 破坏已有数据 | 每个 PR |
| 9 | 体积和性能预算（按项目需要） | 包体积、关键路径耗时不超预算 | 预算文件 | 体积和性能悄悄变差 | 每个 PR |
| 10 | 属性契约测试（按项目需要） | 契约测试用随机生成的输入跑，每次用新种子并写进日志 | 契约测试目录 | 只对固定样例特判 | 每个 PR |

### 第 1 项接口快照

由四个语言 workflow（[`ci-checks/`](../ci-checks/README.md#每个-repo-自己要做的)）的 API snapshot job 跑，`ci.yml` 不用改。比对规则和输出见 [接口快照比对](../ci-checks/README.md#接口快照比对)。

- 启用：在 `.github/checks/repo-guards.toml` 的 `[project."<working-directory>"]` 节里给语言设快照路径，如 `api-snapshot.rust = ".github/checks/api-rust.json"`。路径相对 repo 根目录，要放在 `.github/` 下（已由 CODEOWNERS 锁住），每种语言、每个 `working-directory` 各一份；路径的规定见[覆盖默认配置](../ci-checks/README.md#覆盖默认配置)。没设就不启用。改这个设置按第 21 项单独提一个只改 `.github/` 的 PR。
- 第一次：文件还不存在，job 失败，并把完整快照作为 artifact 上传。把它原样放到配置的路径，提交进同一个 PR。
- 改接口时：生成结果和快照对不上，job 失败，job summary 列出多、少、变的条目，并写明 artifact 名字和该放的路径。接口改动是有意的，就从这次运行的 artifact 下载新快照，原样放到配置的路径，和代码在同一个 PR 里提交；快照在 `.github/` 下，owner 审 PR 时逐条看快照的 diff。不是有意的，就改回代码。
- 现状：Python 从构建出的 wheel 生成，见 [接口快照：Python 生成](../ci-checks/README.md#接口快照python-生成)，构建不出 wheel 的项目查不到；Go 的生成已接上（见[接口快照生成](../ci-checks/go/README.md#接口快照生成api-snapshot)）；Rust 的也已接上（规则和限制见 [Rust 模板](../ci-checks/rust/README.md#接口快照)）；别的语言还没接上，设了路径时 job 在 Generate 步退出 2。

### 第 2 项分层规则

由语言 workflow 的 Checks job 里的 Layer rules 步跑，紧跟 Inline exemptions，`ci.yml` 不用改。现在 `python.yml`、`typescript.yml`、`go.yml` 和 `rust.yml` 有这一步。

- 启用：在 `.github/checks/repo-guards.toml` 的 `[project."<working-directory>"]` 节写 `layers`，测试代码写进 `test-paths`（见[覆盖默认配置](../ci-checks/README.md#覆盖默认配置)）。没写 `layers`，或没有本语言的层时，这一步打印一行提示，列出设了 `layers` 的项目键，然后通过。

```toml
[project."."]
test-paths = ["tests/**"]
layers = [
  { name = "api", paths = ["src/api/**"], exit = "src/api" },
  { name = "core", paths = ["src/core/**"], exit = "src/core" },
  { name = "store", paths = ["src/store/**"], exit = "src/store/__init__.py" },
]
```

- 配置：`layers` 从上层到下层排。`paths`、`exit`、`test-paths` 都相对 `working-directory`；glob 里 `**` 跨目录，`*` 不跨。`exit` 是层的出口，一个路径：Python 是模块文件或包目录（指它的 `__init__.py`），包目录下的 Python 文件都要被本层 `paths` 匹配；TypeScript 是模块文件；Go 是包目录，目录里（不含子目录）的 Go 文件都要被本层 `paths` 匹配；Rust 是模块文件，如 `src/core/mod.rs`。一层属于它的 `exit` 的语言，每种语言按列出的顺序只取自己的层，所以一个 `layers` 里可以有几种语言各一套；别的语言的层不看，写错了也不让本语言这一步失败。`exit` 不存在、看不出是哪种语言的层，每种语言都报错。Python 的层 `paths` 要在 `source-dirs` 里。配置无效时退出 2，写明层名和键：一个文件被同一语言的两层匹配；`exit` 不存在、不是 Rust、Python、TypeScript 或 Go 的模块、不被本层 `paths` 匹配、在 `test-paths` 里、不是一个字符串；层名重复；层里有未知键；路径为空、是绝对路径或带 `..`。
- 规则：每次运行只查本 `working-directory` 下本语言的文件，只判起点和终点都在层里、都不是测试代码的 import；Go 的 `_test.go` 总是测试代码，不用写进 `test-paths`。第 N 层到第 N+1 层只能 import 它的出口，否则是"绕开出口"；到第 N+2 层及更下层是"越层"；到上层是"向上"；同一层随便 import，但同一层文件之间的 import 环是"循环"。终点在项目里但不属于任何层的，和 `working-directory` 之外的 crate，都计入"未归层"数，不失败；标准库和第三方包不管。
- 什么算 import（Python）：所有 `import`、`from … import` 语句，在函数里、`if TYPE_CHECKING:` 下的也算；`importlib.import_module`、`__import__` 不算。模块按 `source-dirs` 和相对 import 解析到项目里的文件；`from 包 import 名字` 里名字是子模块时算那个子模块，否则算包的 `__init__.py`。一个语句算 1 条，指向几个文件时每个文件各 1 条。只用 `ast` 解析，不 import、不执行项目代码；解释器是 uv 自己装的 Python 3.14，在 `/` 下查找（在检出目录里查找时，uv 会运行项目的 `.venv` 里的 python，连带它的 `.pth` 文件），带 `-I -S` 运行，不读 site-packages；CI 里装在这一步自己的目录（`UV_PYTHON_INSTALL_DIR`），前面的 Install 步按项目的 uv 配置装进共用目录的 Python 不会被选中。
- 什么算 import（TypeScript）：`import`（含 `import type` 和只为副作用的 `import "x"`）、`export … from`、`import x = require("x")`、参数是一个字符串字面量的 `require("x")`、类型里的 `import("x")`，在函数里的也算；动态 `import()` 不算。一处算 1 条。按文件所属的 tsconfig 解析：离文件最近的 `tsconfig.json`；它不含这个文件时（如只写 `references` 的根配置），用它 `references` 里含这个文件的那个；按其中的 `paths`、`baseUrl`、`extends`、`moduleResolution` 和 `package.json` 的 `imports` 解析到项目里的文件。解析不了的（包名、`node_modules` 里的包、`working-directory` 之外的 tsconfig 写的别名）当外部包，不管。只用 TypeScript 编译器做语法分析和模块解析，不读 `node_modules`、不读 `working-directory` 之外的文件、不加载 tsconfig 的插件、不执行项目代码。
- 什么算 import（Go）：每个 import 项（`import (…)` 里的一行）算 1 条，标在它那行；别名、`.`、`_` 引入的也算。import path 按 `working-directory` 下各个 `go.mod` 的 `module` 解析：取模块路径最长的那个匹配，剩下的部分是模块目录下的包目录；那个目录里有 Go 文件、且离它最近的 `go.mod` 就是这个模块的，才算项目里的包，否则（标准库、第三方包、解析不到的路径）不管。目标是包目录，按包里第一个不是测试代码的文件判层；计数按（发起文件、目标包目录）。Go 不查"循环"：编译器已经不许包之间有环，只读 import、不看 build 约束时，不会一起编译的文件还会凑出假环。Go 工具跳过的文件（`testdata/`、`vendor/` 下的，以 `_` 或 `.` 开头的目录和文件）不算 Go 源码，那里的 `go.mod` 也不读。只用 `go/parser` 读 import，不构建、不执行项目代码；扫描器是 repo-guards 自己的 Go 程序，每次运行先用 `go build` 编译一次，带 `GOWORK=off`、`GOFLAGS=`、`GOENV=off`，不受项目和本机的 Go 设置影响。
- 什么算 import（Rust）：
  - 每个 `use`（含 `pub use`）、`extern crate` 算 1 条，一个 `use` 的分组指向几个文件时每个文件各 1 条。
  - 不经 `use` 的完整路径每处算 1 条，如 `crate::core::inner::f()`：两段以上，以 `crate`、`self`、`super`、`::`、子模块名或依赖的 crate 名开头。以 `use` 引入的名字开头的路径，走到 `use` 指向的文件之外才算，如 `use crate::core;` 之后的 `core::inner::f()`。
  - 不算：`mod x;` 声明，属性（`#[…]`）和可见性（`pub(crate)`）里的路径，宏里的代码。
  - `#[cfg(test)]`（含 `cfg(all(test, …))`）的项、语句、match 分支、字段和枚举成员，`#![cfg(test)]` 的文件，是测试代码，不查。`#[cfg(test)] mod x;` 指向的文件只在声明它的文件在层里时才算测试代码。
  - 路径按 Cargo 的布局解析到模块文件：`crate::a::b` 是 crate 根文件所在目录下的 `a/b.rs` 或 `a/b/mod.rs`，没有就往短里找；行内模块算所在文件，`#[path]` 不看。
  - 别的 crate 按 `Cargo.toml` 里带 `path` 的依赖解析，含 `workspace = true` 和 `package` 改名。`working-directory` 之外的 crate 算未归层，标准库和 registry 上的 crate 不管。
  - 只用 `syn` 解析，不构建、不运行项目代码。扫描器是 repo-guards 自己的 crate，`cargo run --release` 在 `/` 下构建并运行它；工具链用这个 job 的 `RUSTUP_TOOLCHAIN`，没设时用 repo-guards 固定的版本。
- 只挡新增：HEAD 和它与 `BASE_REF` 的 merge-base 各算一次，两边都用 HEAD 的配置；两边的文件都从 git 对象读，不读工作区，符号链接和 submodule 不读。绕开出口、越层、向上按（发起文件、目标文件）比条数，HEAD 比 merge-base 多就失败，相等或减少都通过；改名或移动过的文件在 merge-base 一边是 0 条。循环看边：一个环里有 merge-base 没有的边（发起文件、目标文件）才失败，已有的边上再加 import 不算。
- 输出：条数增加的文件对，HEAD 上它的每处引用一条 `::error` 注解，写明规则、起止层名、两边的条数和要减到不超过几条，绕开出口的还写应走的出口。新环里每条新边的每处引用一条"循环"注解，写出从这条边的终点起、按 import 方向的整个环。job summary 列出所有违反层规则的文件对（含条数减少和减到 0 的）、新增的循环边数和未归层 import 数。
- 退出码：0 通过；1 有新增违规；2 配置无效、HEAD 上层里的文件解析不了（写明文件）或跑不了。merge-base 上层里的文件解析不了时只打印提示，那个文件在 merge-base 一边当作没有 import。失败不挡同一 job 后面的步。
- 本地：跑过 `fetch_ci_checks.py` 后，在 repo 根目录跑 `python=$(cd / && uv python install --no-config -q 3.14 && uv python find --no-config --managed-python 3.14)`，再跑 `"$python" -I -S .repo-guards/ci-checks/layer-rules/layer_rules.py python --working-directory <working-directory>`，同 CI 的这一步（CI 另设 `UV_PYTHON_INSTALL_DIR`）；TypeScript 先跑一次 `npm ci --prefix .repo-guards/ci-checks/typescript/layer-rules --omit=dev --ignore-scripts` 装扫描器的依赖，再把命令里的 `python` 换成 `typescript`，要 Node 22.18 及以上；Go 把 `python` 换成 `go`，要装好 `go`；Rust 换成 `rust`，要装好 cargo。`--base` 默认是 `origin/HEAD`；给和 CI 的 `BASE_REF` 同一个 commit 时，输出和退出码同 CI。工作区有未提交的改动时先提示，检查的仍是 HEAD commit。
- 实现：共用部分 [`ci-checks/layer-rules/layer_rules.py`](../ci-checks/layer-rules/layer_rules.py)（只用标准库）读配置、读两边的文件、判定、输出；各语言的扫描器只报每个 import 语句解析到的项目文件（`working-directory` 之外的 crate 报成 `..` 或以 `../` 开头的目录，算未归层；Go 可以报包目录，按包里第一个不是测试代码的文件判层；一个包的文件分在两层算配置无效），解析要读的文件（`go.mod`、`tsconfig*.json`、`Cargo.toml`）由共用部分一起交给它，协议写在 Python 扫描器 [`ci-checks/python/layer-rules/imports.py`](../ci-checks/python/layer-rules/imports.py) 的开头。测试：`ci-checks/layer-rules/` 先在 `../typescript/layer-rules` 跑 `npm ci --ignore-scripts`，再跑 `python3 -m unittest layer_rules_test`；`ci-checks/python/layer-rules/` 跑 `python3 -m unittest imports_test`；TypeScript 扫描器是 [`ci-checks/typescript/layer-rules/imports.ts`](../ci-checks/typescript/layer-rules/imports.ts)，依赖在它自己的 `package.json` 里固定（`typescript` 6.0.3），在该目录跑 `npm test` 和 `npx tsc`；Go 扫描器在 [`ci-checks/go/layer-rules/`](../ci-checks/go/layer-rules/)，在该目录跑 `go test ./...`；Rust 扫描器在 [`ci-checks/rust/layer-rules/`](../ci-checks/rust/layer-rules/)，在该目录跑 `cargo test --locked`，`layer_rules_test` 里 Rust 的场景要有 cargo。
