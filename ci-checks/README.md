# CI 检查

第 3 层：不依赖项目设计的检查，每个 repo 的 `.github/workflows/ci.yml` 都要跑。依赖项目设计的架构检查在第 4 层，见 [`architecture/`](../architecture/README.md)。

每项检查拿代码和一份"标准答案"比对，标准答案用 CODEOWNERS 锁住，改它要 owner 批准（见下文 CODEOWNERS）。CI 只比对，不重新生成标准答案。目前每个 repo 在自己的 `ci.yml` 里实现这些检查。

检查按实现方式分两组：语言级（按工具分）、流程级。"频率"一列：每个 PR，或定期。定期结果必须强制，不能只当建议：比如失败自动开 issue，挡住下一次发布。标"按项目需要"的检查适用才加。

## 语言级

现成工具加配置：一行一类工具，对应一份锁住的配置。最后三列是 Rust、Python、TypeScript 默认选用的工具；每种语言的模板配置待做。

| # | 工具 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 | Rust | Python | TypeScript |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | formatter | 用检查模式跑，格式不一致就失败；CI 从不改写代码 | formatter 配置 | 放宽规则 | 每个 PR | rustfmt | ruff format | prettier |
| 2 | linter | 下文 [linter 规则](#linter-规则)全部开着；现有超标的记进 lint 基线，基线只能变小 | lint 配置、lint 基线、escape hatch 白名单 | 放宽规则；见下文各条 | 每个 PR | clippy | ruff | eslint（typescript-eslint） |
| 3 | 类型检查 | 最严格模式；不准隐式 any 或 unknown；公开函数的参数和返回值都写类型；可能为空的值先处理再用 | 类型检查配置 | 放宽规则 | 每个 PR | 编译器 | pyright | tsc |
| 4 | 死代码 | 没有用不到的代码。文件内的由 linter 或编译器查；公开了但没人调用的要专门工具 | 检查配置 | 代码质量，不防作弊：工具把测试里的调用也算作使用 | 每个 PR | 编译器 `dead_code`、cargo-machete（没用的依赖） | ruff、vulture | tsc、knip |
| 5 | 重复代码 | 没有大段重复 | 检查配置 | 代码质量，不防作弊：改个名字就躲过 | 每个 PR | jscpd | jscpd | jscpd |
| 6 | 依赖检查 | lockfile 和 base 比 diff：新增或变了的包必须在依赖白名单里；不准 path、git、URL 依赖，不准 override 或 patch，不准 submodule，不准新的 vendor 目录；安装脚本关掉；测试插件自动加载关掉；做漏洞和许可证扫描 | 依赖白名单 | 引入改过的包、在安装或测试时改结果的插件、有漏洞或许可证不合的包 | 每个 PR | cargo-deny | pip-audit | npm audit |
| 7 | 覆盖率 | 总覆盖率不低于门槛，也不低于 merge-base。PR 改动的行被测试执行到的比例不低于门槛（diff-cover，三种语言通用）；门槛不设 100%：100% 会逼出空测试，断言由变异测试把关 | 覆盖率门槛 | 少写测试、删测试；新代码不测，靠旧代码撑总覆盖率 | 每个 PR | cargo-llvm-cov | coverage.py | vitest 的 v8 coverage |
| 8 | CRAP 分数（按项目需要） | 每个函数的 CRAP 分数（圈复杂度和覆盖率合算）不超过上限；现在超标的记进 CRAP 基线，基线只能变小 | CRAP 基线 | 复杂的函数不测 | 每个 PR | 没有现成工具，用覆盖率报告和复杂度结果算 | 同左 | 同左 |
| 9 | 变异测试 | 每个 PR 只变异改动的行，有变异体存活就失败；定期跑全部代码，存活的补测试。变异白名单里的除外 | 变异白名单 | 测试执行了代码，但不检查结果 | 每个 PR（改动行）、定期（全部） | cargo-mutants | mutmut | Stryker |
| 10 | sanitizer | 定期：全部测试在内存、线程、未定义行为 sanitizer 下跑。按项目需要：每个 PR 跑轻量版（sanitizer 和竞态检测） | 豁免白名单 | 平时测试暴露不出的内存和并发错误 | 定期；按项目需要每个 PR | ASan、TSan、Miri | 不适用 | 不适用 |
| 11 | 文档测试（按项目需要） | 文档里的示例能跑，结果和文档一致 | 文档 | 文档和代码脱节 | 每个 PR | `cargo test --doc` | doctest | 无标准工具 |

覆盖率只说明代码被执行过，不说明测试检查了结果：空测试也能把覆盖率刷上去。识破空测试要靠变异测试（工具在临时副本里把代码改坏，测试没失败就说明没检查结果；第 9 项），或者[顶层 README](../README.md) 第 5 层的隐藏验收测试。

### linter 规则

linter 配置至少开着这些规则。这里写的是意图，各语言模板落成具体的规则名；某条在某种语言里不适用（比如 Rust 惯用同名重新绑定，"不遮蔽变量"不适用），模板里注明。

| 类 | 规则 | 防什么 |
|---|---|---|
| 正确性 | 没有用不到的变量和 import；不吞异常（空 catch、裸 except）；不遮蔽变量；浮点数不做相等比较；不用已废弃的 API | 常见 bug |
| 可读性 | 函数长度、参数个数、嵌套深度、圈复杂度或认知复杂度、文件长度都有上限；命名风格统一 | 难读难改的代码 |
| 注释预算 | 每个改动的文件（和 base 的 merge-base 比 diff）：注释字符数（含文档注释、docstring；不算空白）不超过代码字符数的 10%，测试代码 5%，另加 300 字符固定额度。代码自己说明自己。每种语言用该语言自己写一份实现，放在 `ci-checks/<语言>/` 下，见 [Rust](rust/README.md#注释预算) | 注释堆砌 |
| 危险写法 | 库代码不用崩溃式捷径（unwrap、panic 之类）；测试里不 sleep、不读真实时间 | 崩溃、时好时坏的测试 |
| escape hatch | 动态 import、eval 和动态执行、反射访问私有符号、unsafe 或 FFI、不检查的强制转换成 any 类类型：每处都要在 escape hatch 白名单里（路径、符号、原因） | 用 escape hatch 绕开类型检查和分层规则 |
| 不检测测试环境 | 发布代码不读 CI 或测试标记（CI 环境变量、测试框架是否已加载、测试模块以外的测试专用编译开关），不按主机名、用户、当前目录分支，不 import mock 库：放进禁用 API 列表 | 在测试环境走特判，生产走另一条路 |
| 不新增占位 | 发布代码里不新增 TODO、FIXME 或"未实现"占位（和 base 比 diff） | 留占位冒充完成 |
| 测试质量 | 每个测试有断言；没有恒真或常量断言；没有 focus 或禁用的测试；测试里没有宽泛的 catch；测试里没有行内覆盖重试次数、超时、随机种子、样例数 | 空测试、只跑部分测试、吞掉失败、在测试里局部放宽 |

## 流程级

本 repo 只定要求，各 repo 用自己的语言和工具实现，比如用语言自带的语法解析找豁免和所在函数。实现放在 `.github/` 下，由 CODEOWNERS 锁住。

| # | 检查 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 |
|---|---|---|---|---|---|
| 12 | 行内豁免 | 各工具的豁免都算：lint、类型检查、覆盖率排除、跳过标记、死代码和重复代码的忽略、sanitizer 抑制、"生成文件"头。每一处豁免都要在豁免白名单里登记（工具、文件、规则编号、所在函数、次数、原因）；没登记的、次数对不上的、登记了但代码里已经没有的，都失败 | 豁免白名单 | 在代码里局部关掉检查 | 每个 PR |
| 13 | 测试结果状态门 | 结果文件存在，且是这次运行生成的；里面每个测试都是 passed：skipped、expected-fail、重试过、flaky 的数量为 0，状态白名单里的除外。另跑一次只收集不执行（collect-only），测试数要一致。测试 ID 和 merge-base 比，删掉或改名的 ID 让检查失败，删除名单里的除外 | 状态白名单、删除名单 | 加跳过标记、标成预期失败、重试到通过、改构建配置让测试不跑、删测试或改名、拿旧结果文件冒充 | 每个 PR |
| 14 | 先失败后通过 | PR 新增或改过的测试拿去跑 base 的源码：PR 改了行为时，至少一个失败；在 head 上全部通过 | `.github/` 里的 CI 文件 | 测试没测到这次改的行为 | 每个 PR |
| 15 | 隔离再跑 | 必跑的测试（时间允许就全部测试）再跑一遍：去掉 CI 环境变量、断网、随机时区、locale 和时钟、换一个 checkout 路径、打乱顺序；每个测试有超时 | `.github/` 里的 CI 文件 | linter 规则"不检测测试环境"没抓到的测试环境特判；测试依赖顺序、网络或机器；测试卡住 | 每个 PR |
| 16 | 工具版本 | 检查工具按它们自己锁定的清单安装，版本写死，和 PR 的依赖分开。跑检查工具的 job 不安装也不运行 PR 代码；运行 PR 代码的 job 只产出结果文件，由另一个 job 解析判定 | 检查工具清单 | PR 的代码或依赖改掉检查工具或它的结果；工具升级让检查悄悄变松 | 每个 PR |
| 17 | 生成内容 | 生成文件用锁住的生成器重新生成，逐字节相同；带"生成"头的文件必须在生成路径名单里；二进制、压缩过的代码、大段编码数据、符号链接只能出现在二进制白名单里；golden 和快照文件锁住，测试从不以更新模式运行 | 生成路径名单、生成器配置、二进制白名单、golden 和快照目录 | 把逻辑藏进"生成"文件、二进制或编码数据躲过检查；改 golden 文件迁就错误输出 | 每个 PR |
| 18 | 只读范围 | 测试运行时，源码、测试、fixture、快照目录只读 | `.github/` 里的 CI 文件 | 测试运行中改源码、测试或快照 | 每个 PR |
| 19 | 工作区不变 | 全部跑完后 `git status --porcelain --ignored` 没有输出：构建产物、缓存、结果文件都写到工作区外 | `.github/` 里的 CI 文件、`.gitignore` | 测试或生成器偷偷改文件、改快照；写进被 ignore 的路径躲过检查 | 每个 PR |

## CODEOWNERS

本页启用的检查，"标准答案"一列里的东西都进 CODEOWNERS，改它要 owner 批准；不锁的话，检查形同虚设。实现文件不锁，worker 才能自由改。

| 锁什么 | 包括 | 写法 | 防什么 |
|---|---|---|---|
| 工具配置和测试钩子 | 下表各语言的文件名 | 只写文件名（不带 `/`），任何深度都匹配，新建的也算；带目录的写成 `**/` 开头 | 改规则；子目录里新建同名配置，覆盖根目录那份；测试钩子把失败改成通过 |
| 白名单和基线 | 豁免、escape hatch、依赖、变异、状态、二进制白名单，删除名单；lint 基线、CRAP 基线、覆盖率门槛 | 建议都放在一个目录里，锁整个目录 | 自己给自己放行 |
| 测试数据 | golden、快照、fixture 目录 | 目录 | 改标准答案迁就错误输出 |
| 生成相关 | 生成路径名单、生成器配置 | 文件 | 把逻辑藏进"生成"文件 |
| 检查工具清单 | 检查工具和版本 | 文件 | 换掉或降级检查工具 |
| git 配置 | `.gitignore`、`.gitattributes` | 文件名 | 把文件藏进被忽略的路径；改文件属性躲过 diff |
| 不锁 | 实现文件；lockfile 由 repo 自己定，它的变动由依赖检查管 | | |

各语言的配置文件名：

| 语言 | 进 CODEOWNERS 的配置文件名 |
|---|---|
| Rust | `rustfmt.toml`, `.rustfmt.toml`, `clippy.toml`, `.clippy.toml`, `**/.cargo/config.toml`, `**/.cargo/config`, `**/.cargo/mutants.toml`, `mutants.toml`, `deny.toml`, `rust-toolchain.toml`, `rust-toolchain`, `**/.config/nextest.toml`, `build.rs` |
| Python | `pyproject.toml`, `ruff.toml`, `.ruff.toml`, `setup.cfg`, `tox.ini`, `pytest.ini`, `conftest.py`, `.coveragerc`, `mypy.ini`, `.mypy.ini`, `pyrightconfig.json`, `sitecustomize.py`, `usercustomize.py` |
| TypeScript | `package.json`, `.prettierignore`, `.eslintignore`, `tsconfig*.json`, `jsconfig*.json`, `.eslintrc*`, `eslint.config.*`, `.prettierrc*`, `prettier.config.*`, `vitest.config.*`, `vite.config.*`, `jest.config.*`, `.c8rc*`, `.nycrc*`, `stryker.conf.*`, `knip.json`, `.knip.json*`, `.npmrc` |
