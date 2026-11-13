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

状态指本 repo 的实现：✅ 已实现，⚠️ 部分实现，空着为未实现。

| # | 检查 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 | 状态 |
|---|---|---|---|---|---|---|
| 1 | 接口快照 | 用公开 API 工具从构建产物生成完整签名（参数类型、返回类型、默认值），和快照逐项比对：多、少、签名变了都失败。不靠数个数 | 接口快照 | 偷偷增删或改对外接口 | 每个 PR | ⚠️ |
| 2 | [分层规则](#第-2-项分层规则) | 按规则配置里写的"哪层可以 import 哪层"使用别的层，配置之外的跨层 import 失败 | 各语言工具的配置（Rust：各 crate 的 `Cargo.toml`） | 绕开接口 | 每个 PR | ⚠️ |
| 3 | 分层覆盖全部模块 | 每个源码模块在架构规则里恰好归一层；没归层的失败 | 架构规则配置 | 新模块不归层，分层规则管不到 | 每个 PR |  |
| 4 | 实现名不越过工厂 | 具体实现的名字只出现在它自己的包、工厂和它自己的测试里；工厂之上不对接口对象做向下转型或按类型分支 | 架构规则配置 | 上层直接用具体实现，绕开接口 | 每个 PR |  |
| 5 | 副作用只在适配层 | 时钟、随机数、文件系统、网络、子进程、硬件 API 只在指定的适配模块里用 | 架构规则配置 | 副作用散在业务代码里，测试替换不了，结果随环境变 | 每个 PR |  |
| 6 | 测试边界 | 测试只 import 本单元的出口、下层接口和测试支持代码；只 mock 下层接口，不 mock 被测单元，也不 mock CI 工具 | 架构规则配置 | mock 掉被测代码或 CI 工具，测试测不到真东西 | 每个 PR |  |
| 7 | 契约测试矩阵 | 静态找出每个接口的全部实现（含 fake 和测试替身），每个都要出现在契约测试的结果里；接口快照里的每个接口都有一套契约测试；只跑契约测试，就要执行到每个接口方法 | 契约测试目录、接口快照 | 新实现不跑契约、接口没有契约、契约漏测方法、改弱契约 | 每个 PR |  |
| 8 | schema 和迁移兼容（按项目需要） | 新 schema 能读旧数据；迁移能从上一个发布版本升级 | 已发布的 schema 快照 | 破坏已有数据 | 每个 PR |  |
| 9 | 体积和性能预算（按项目需要） | 包体积、关键路径耗时不超预算 | 预算文件 | 体积和性能悄悄变差 | 每个 PR |  |
| 10 | 属性契约测试（按项目需要） | 契约测试用随机生成的输入跑，每次用新种子并写进日志 | 契约测试目录 | 只对固定样例特判 | 每个 PR |  |

### 第 1 项接口快照

由四个语言 workflow（[`ci-checks/`](../ci-checks/README.md#每个-repo-自己要做的)）的 API snapshot job 跑，`ci.yml` 不用改。比对规则和输出见 [接口快照比对](../ci-checks/README.md#接口快照比对)。

- 启用：在 `.github/checks/repo-guards.toml` 的 `[project."<working-directory>"]` 节里给语言设快照路径，如 `api-snapshot.rust = ".github/checks/api-rust.json"`。路径相对 repo 根目录，要放在 `.github/` 下（已由 CODEOWNERS 锁住），每种语言、每个 `working-directory` 各一份；路径的规定见[覆盖默认配置](../ci-checks/README.md#覆盖默认配置)。没设就不启用。改这个设置按第 21 项单独提一个只改 `.github/` 的 PR。
- 只冻结一部分：在同一节设 `api-snapshot-scope.<语言>`，列出要冻结的模块前缀，如 `api-snapshot-scope.rust = ["kvm_op::driver"]`，项目自己的代码不用改。范围内仍是多、少、变都失败，范围外的条目不进快照。没设就冻结整个项目的公开接口。写法和生成器的约定见[冻结范围](../ci-checks/README.md#冻结范围)；改它也按第 21 项单独提 PR。
- 第一次：文件还不存在，job 失败，并把完整快照作为 artifact 上传。把它原样放到配置的路径，提交进同一个 PR。
- 改接口时：生成结果和快照对不上，job 失败，job summary 列出多、少、变的条目，并写明 artifact 名字和该放的路径。接口改动是有意的，就从这次运行的 artifact 下载新快照，原样放到配置的路径，和代码在同一个 PR 里提交；快照在 `.github/` 下，owner 审 PR 时逐条看快照的 diff。不是有意的，就改回代码。
- 现状：Python 从构建出的 wheel 生成，见 [接口快照：Python 生成](../ci-checks/README.md#接口快照python-生成)，构建不出 wheel 的项目查不到；Go 的生成已接上（见[接口快照生成](../ci-checks/go/README.md#接口快照生成api-snapshot)）；Rust 的也已接上（规则和限制见 [Rust 模板](../ci-checks/rust/README.md#接口快照)）；TypeScript 从内存里的 tsc 构建输出生成，见[接口快照：TypeScript 生成](../ci-checks/README.md#接口快照typescript-生成)。冻结范围四种语言的生成器都已接上，各自读这个设置。

### 第 2 项分层规则

规则写在各语言现成工具自己的配置里，由工具检查；repo-guards 不自己扫描，`.github/checks/repo-guards.toml` 里没有分层设置。Python、TypeScript、Go 在语言 workflow 的 Checks job 的 Layer rules 步跑（约定见下），`ci.yml` 不用改；Rust 不另外检查。

| 语言 | 工具 | 规则写在 |
|---|---|---|
| Python | import-linter | `pyproject.toml` 的 `[tool.importlinter]`，或 `.importlinter` |
| TypeScript | dependency-cruiser | `.dependency-cruiser.json`，只认 JSON（见 [TypeScript 模板](../ci-checks/typescript/README.md#分层规则dependency-cruiser)） |
| Go | depguard，经 golangci-lint 跑 | `.golangci.yml` |
| Rust | 不另外检查 | 各 crate 的 `Cargo.toml` |

- 规则：写"哪层可以 import 哪层"，不是从上到下排好的层序，用工具自己的写法。
- 全量检查：每次运行查整个项目，不只挡新增。已有的违规在工具自己的配置里豁免，或者不写那条规则。
- Rust：一层是一个 crate。哪层可以用哪层，就是各 crate 的 `Cargo.toml` 里的 `[dependencies]`、`[dev-dependencies]`、`[build-dependencies]`；cargo 只让 crate 用它声明过的 crate。把各 crate 的 `Cargo.toml` 放进 CODEOWNERS（写 `Cargo.toml`，不带 `/`，任何深度都匹配），改层之间的依赖就要 owner 批准。cargo 挡不住的：`#[path = "…"]` 指向别的 crate 的源码文件、`include!`、指向别的 crate 的符号链接，这些靠 owner 审 PR 时看出来。
- 锁住规则：工具的配置文件都进 CODEOWNERS，文件名见[各语言的配置文件名](../ci-checks/README.md#codeowners)（`Cargo.toml`、`pyproject.toml`、`.importlinter`、`.dependency-cruiser*`、`.golangci.yml` 都在里面）。豁免只写在锁住的文件里；工具另有基线文件的（如 dependency-cruiser 的 `.dependency-cruiser-known-violations.json`），基线文件也要锁住。
- Layer rules 步的约定（Python、TypeScript、Go 共用，各语言接入工具时照做）：
  - 位置：Checks job 里紧跟 Inline exemptions，和后面的步一样带 `if: ${{ !cancelled() && steps.inline-exemptions.outcome == 'success' }}`；失败不挡同一 job 后面的步。
  - 找配置：只在 `working-directory` 里找本语言工具的配置，不往上级、下级目录找。
  - 没有配置：打印一行提示，写明工具名和找过的文件，然后通过。不要求项目加配置。
  - 有配置：用这份配置对整个项目跑一次工具；工具的退出码不是 0 就失败，配置被工具拒绝也失败。
  - 工具版本：由 repo-guards 写死，和项目的依赖分开装（[第 3 层](../ci-checks/README.md#流程级)第 17 项）。
  - 不执行项目代码，没有例外：Checks job 后面的步信任 repo-guards 的文件和工具，先跑的项目代码能改掉它们。工具要执行项目代码才能跑的（如要 import 项目的包），不能放进 Checks job，要单独一个 job，像 API snapshot 一样只有 `contents: read`、不用 secrets。用 JavaScript 写的配置（如 `.dependency-cruiser.js`）加载时就会运行，所以它和它加载的每个文件都算项目代码，除非都是锁住的文件名。
  - 只改规则也要跑：这一步和工具读的每个文件（如 `.importlinter`、`.golangci.yml`、`.golangci.yaml`）都加进 [`language_gate.py`](../ci-checks/language-gate/language_gate.py) 里本语言的 `PATTERNS`，`language_gate_test` 里加一条对应的测试。
  - 各语言接入时自己定：怎样算有配置（如 `.golangci.yml` 要配了 depguard 才算）、怎样装工具；并把本语言加进 [`inline_exemptions_test.py`](../ci-checks/inline-exemptions/inline_exemptions_test.py) 的 `LAYER_RULES`。
- 现状：TypeScript 已接上 dependency-cruiser（见 [TypeScript 模板](../ci-checks/typescript/README.md#分层规则dependency-cruiser)）；Python、Go 还没接上工具，workflow 里还没有 Layer rules 步；Rust 不需要这一步。
