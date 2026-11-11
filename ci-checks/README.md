# CI 检查

第 3 层：不依赖项目设计的检查，每个 repo 的 `.github/workflows/ci.yml` 都要跑。依赖项目设计的架构检查在第 4 层，见 [`architecture/`](../architecture/README.md)。

每项检查拿代码和一份"标准答案"比对，标准答案用 CODEOWNERS 锁住，改它要 owner 批准（见下文 CODEOWNERS）。CI 只比对，不重新生成标准答案。目前每个 repo 在自己的 `ci.yml` 里实现这些检查。

检查按实现方式分两组：语言级（按工具分）、流程级。"频率"一列：每个 PR，或定期。定期结果必须强制，不能只当建议：比如失败自动开 issue，挡住下一次发布。标"按项目需要"的检查适用才加。

## 语言级

现成工具加配置：一行一类工具，对应一份锁住的配置；没有现成工具的（注释预算）用自己的检查器。最后三列是 Rust、Python、TypeScript 默认选用的工具；每种语言的模板配置待做。

| # | 工具 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 | Rust | Python | TypeScript |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 1 | formatter | 用检查模式跑，格式不一致就失败；CI 从不改写代码 | formatter 配置 | 放宽规则 | 每个 PR | rustfmt | ruff format | prettier |
| 2 | linter | 下文 [linter 规则](#linter-规则)全部开着；现有超标的记进 lint 基线，基线只能变小 | lint 配置、lint 基线、escape hatch 白名单 | 放宽规则；见下文各条 | 每个 PR | clippy | ruff | eslint（typescript-eslint） |
| 3 | 注释预算 | 每个改动的文件（和 base 的 merge-base 比 diff）：注释字符数（含文档注释、docstring；不算空白）不超过代码字符数的 10%，测试代码 5%，另加 300 字符固定额度。代码自己说明自己。没有现成 linter 能查，每种语言用该语言写一个自己的检查器，放在 `ci-checks/<语言>/comment-budget/` | 注释预算配置 | 注释堆砌 | 每个 PR | [comment-budget](rust/README.md#注释预算) | [comment-budget](python/README.md#注释预算) | [comment-budget](typescript/README.md#注释预算) |
| 4 | 类型检查 | 下文 [类型检查规则](#类型检查规则)全部开着 | 类型检查配置 | 放宽规则 | 每个 PR | 编译器 | pyright | tsc |
| 5 | 死代码 | 没有用不到的代码。文件内的由 linter 或编译器查；公开了但没人调用的要专门工具 | 检查配置 | 代码质量，不防作弊：工具把测试里的调用也算作使用 | 每个 PR | 编译器 `dead_code`、cargo-machete（没用的依赖） | ruff、vulture | tsc、knip |
| 6 | 重复代码 | 没有大段重复 | 检查配置 | 代码质量，不防作弊：改个名字就躲过 | 每个 PR | jscpd | jscpd | jscpd |
| 7 | 依赖检查 | lockfile 和 base 比 diff：新增或变了的包必须在依赖白名单里；不准 path、git、URL 依赖，不准 override 或 patch，不准 submodule，不准新的 vendor 目录；安装脚本关掉；测试插件自动加载关掉；做漏洞和许可证扫描 | 依赖白名单 | 引入改过的包、在安装或测试时改结果的插件、有漏洞或许可证不合的包 | 每个 PR | cargo-deny | pip-audit | npm audit |
| 8 | 覆盖率 | 总覆盖率不低于门槛，也不低于 merge-base。PR 改动的行被测试执行到的比例不低于门槛（diff-cover，三种语言通用）；门槛不设 100%：100% 会逼出空测试，断言由变异测试把关 | 覆盖率门槛 | 少写测试、删测试；新代码不测，靠旧代码撑总覆盖率 | 每个 PR | cargo-llvm-cov | coverage.py | vitest 的 v8 coverage |
| 9 | CRAP 分数（按项目需要） | 每个函数的 CRAP 分数（圈复杂度和覆盖率合算）不超过上限；现在超标的记进 CRAP 基线，基线只能变小 | CRAP 基线 | 复杂的函数不测 | 每个 PR | 没有现成工具，用覆盖率报告和复杂度结果算 | 同左 | 同左 |
| 10 | 变异测试 | 每个 PR 只变异改动的行，有变异体存活就失败；定期跑全部代码，存活的补测试。变异白名单里的除外 | 变异白名单 | 测试执行了代码，但不检查结果 | 每个 PR（改动行）、定期（全部） | cargo-mutants | mutmut | Stryker |
| 11 | sanitizer | 定期：全部测试在内存、线程、未定义行为 sanitizer 下跑。按项目需要：每个 PR 跑轻量版（sanitizer 和竞态检测） | 豁免白名单 | 平时测试暴露不出的内存和并发错误 | 定期；按项目需要每个 PR | ASan、TSan、Miri | 不适用 | 不适用 |
| 12 | 文档测试（按项目需要） | 文档里的示例能跑，结果和文档一致 | 文档 | 文档和代码脱节 | 每个 PR | `cargo test --doc` | doctest | 无标准工具 |

覆盖率只说明代码被执行过，不说明测试检查了结果：空测试也能把覆盖率刷上去。识破空测试要靠变异测试（工具在临时副本里把代码改坏，测试没失败就说明没检查结果；第 10 项），或者[顶层 README](../README.md) 第 5 层的隐藏验收测试。

### linter 规则

linter 配置至少开着这些规则。后三列是各语言的具体规则；`disallowed-methods`、`banned-api`、`no-restricted-*` 是禁用列表，括号里是要列进去的 API。

| 类 | 规则 | 防什么 | Rust | Python | TypeScript |
|---|---|---|---|---|---|
| 正确性 | 没有用不到的变量 | 漏用的值 | `unused_variables` | `F841` | `@typescript-eslint/no-unused-vars` |
| 正确性 | 没有用不到的 import | 残留依赖 | `unused_imports` | `F401` | `@typescript-eslint/no-unused-vars` |
| 正确性 | 没有用不到的参数 | 漏用的输入 | `unused_variables` | `ARG` | `@typescript-eslint/no-unused-vars` |
| 正确性 | 不吞异常：空 catch、丢弃错误 | 错误被静默 | `unused_must_use`、`clippy::let_underscore_must_use` | `S110` | `no-empty` |
| 正确性 | 不吞异常：裸 except、宽泛 except | 误吞无关异常 | 不适用 | `E722`、`BLE001` | 不适用 |
| 正确性 | 不遮蔽变量 | 用错同名变量 | 不适用（惯用同名重新绑定） | `A`（只查内置名）、`PLW2901` | `@typescript-eslint/no-shadow` |
| 正确性 | 浮点数不做相等比较 | 精度误差 | `clippy::float_cmp` | `RUF069`（preview） | 无现成规则 |
| 正确性 | 不拿常量跟常量比较 | 恒真恒假的条件 | `clippy::eq_op`（只查两边相同） | `PLR0133` | `no-constant-binary-expression` |
| 可读性 | 函数长度有上限 | 难读的长函数 | `clippy::too_many_lines` | `PLR0915` | `max-lines-per-function` |
| 可读性 | 参数个数有上限 | 难用的接口 | `clippy::too_many_arguments` | `PLR0913` | `max-params` |
| 可读性 | 嵌套深度有上限 | 难读的分支 | `clippy::excessive_nesting` | `PLR1702`（preview） | `max-depth` |
| 可读性 | 圈复杂度或认知复杂度有上限 | 难测的函数 | `clippy::cognitive_complexity` | `C901` | `complexity` |
| 可读性 | 文件长度有上限 | 大杂烩文件 | 无现成规则 | 无现成规则 | `max-lines` |
| 可读性 | 命名风格统一 | 名字难认 | `non_snake_case`、`non_camel_case_types`、`non_upper_case_globals` | `N` | `@typescript-eslint/naming-convention` |
| 可读性 | 不用布尔位置参数 | 调用处看不懂 | `clippy::fn_params_excessive_bools` | `FBT` | 无现成规则 |
| 可读性 | 没有注释掉的代码 | 死代码藏在注释里 | 无现成规则 | `ERA001` | 无现成规则 |
| 可读性 | 能简化的写法要简化 | 绕弯的写法 | `clippy::complexity`（组） | `SIM` | `no-else-return`、`no-lonely-if`、`no-unneeded-ternary`、`@typescript-eslint/prefer-optional-chain` |
| 可读性 | 不用旧版本语法 | 过时写法 | `rust_2018_idioms`（组） | `UP` | `no-var`、`prefer-rest-params`、`prefer-spread`、`object-shorthand` |
| 可读性 | 不用相对 import | 移动文件就断 | 不适用 | `TID252` | `import/no-relative-parent-imports`（eslint-plugin-import） |
| 危险写法 | 库代码不用崩溃式捷径 | 崩溃 | `clippy::unwrap_used`、`clippy::expect_used`、`clippy::panic` | `S101` | `@typescript-eslint/no-non-null-assertion` |
| 危险写法 | 测试里不 sleep | 时好时坏的测试 | `disallowed-methods`（`std::thread::sleep`） | `banned-api`（`time.sleep`） | `no-restricted-syntax`（`setTimeout`） |
| 危险写法 | 测试里不读真实时间 | 时好时坏的测试 | `disallowed-methods`（`SystemTime::now`、`Instant::now`） | `banned-api`（`time.time`、`datetime.datetime.now`） | `no-restricted-syntax`（`Date.now`、`new Date()`） |
| 危险写法 | 不残留调试器 | 发布代码停住 | 不适用 | `T100` | `no-debugger` |
| 危险写法 | 不残留调试输出 | 输出泄露、刷屏 | `clippy::dbg_macro`、`clippy::print_stdout`、`clippy::print_stderr` | `T20` | `no-console` |
| escape hatch | 动态 import 在白名单里 | 绕开分层规则 | 不适用 | `banned-api`（`importlib.import_module`） | `no-restricted-syntax`（`ImportExpression`） |
| escape hatch | eval 和动态执行在白名单里 | 绕开类型检查 | 不适用 | `S307`、`S102` | `no-eval`、`no-implied-eval`、`no-new-func` |
| escape hatch | 反射访问私有符号在白名单里 | 绕开封装 | 不适用 | `SLF001` | `no-restricted-syntax`（方括号访问私有成员） |
| escape hatch | unsafe 或 FFI 在白名单里 | 绕开内存安全 | `unsafe_code` | `banned-api`（`ctypes`） | 不适用 |
| escape hatch | 不检查的强制转换在白名单里 | 绕开类型检查 | 不适用 | `banned-api`（`typing.cast`） | `@typescript-eslint/no-unsafe-type-assertion` |
| 不检测测试环境 | 发布代码不读 CI 环境变量 | 测试环境走特判 | `disallowed-methods`（`std::env::var`） | `banned-api`（`os.environ`、`os.getenv`） | `no-restricted-syntax`（`process.env`） |
| 不检测测试环境 | 发布代码不查测试框架是否已加载 | 测试环境走特判 | 不适用 | `banned-api`（`sys.modules`） | `no-restricted-syntax`（`process.env.VITEST`） |
| 不检测测试环境 | 测试模块以外不用测试专用编译开关 | 测试环境走特判 | 无现成规则（`cfg(test)`） | 不适用 | 不适用 |
| 不检测测试环境 | 不按主机名、用户、当前目录分支 | 换台机器就变 | `disallowed-methods`（`std::env::current_dir`） | `banned-api`（`socket.gethostname`、`getpass.getuser`、`os.getcwd`） | `no-restricted-imports`（`node:os`）、`no-restricted-syntax`（`process.cwd`） |
| 不检测测试环境 | 发布代码不 import mock 库 | 生产里留 mock | 编译器（mock 库只放 dev-dependencies） | `banned-api`（`unittest.mock`） | `no-restricted-imports` |
| 不新增占位 | 不新增 TODO、FIXME 注释 | 留占位冒充完成 | 无现成规则 | `FIX` | `no-warning-comments` |
| 不新增占位 | 不新增"未实现"占位 | 留占位冒充完成 | `clippy::todo`、`clippy::unimplemented` | 无现成规则 | 无现成规则 |
| 测试质量 | 每个测试有断言 | 空测试 | 无现成规则 | 无现成规则 | `vitest/expect-expect`（`@vitest/eslint-plugin`） |
| 测试质量 | 没有恒真或常量断言 | 空测试 | `clippy::assertions_on_constants` | `F631`、`PLW0129`、`PT015` | 无现成规则 |
| 测试质量 | 没有 focus 的测试 | 只跑部分测试 | 不适用 | 不适用 | `vitest/no-focused-tests` |
| 测试质量 | 没有禁用的测试 | 只跑部分测试 | 无现成规则 | 无现成规则 | `vitest/no-disabled-tests` |
| 测试质量 | 测试里没有宽泛的 catch | 吞掉失败 | `disallowed-methods`（`std::panic::catch_unwind`） | `BLE001`、`PT011` | `no-restricted-syntax`（`CatchClause`） |
| 测试质量 | 测试里没有行内覆盖重试次数、超时、随机种子、样例数 | 在测试里局部放宽 | 无现成规则 | `banned-api`（`hypothesis.settings`、`random.seed`） | `no-restricted-syntax` |

### 类型检查规则

类型检查配置至少开着这些规则。

| 规则 | 防什么 | Rust | Python | TypeScript |
|---|---|---|---|---|
| 最严格模式 | 放宽检查 | 编译器本身 | pyright `typeCheckingMode = "strict"` | tsc `strict` |
| 不准 any 或类型未知的值 | 类型检查失效 | 不适用 | `reportUnknown*`（strict）、`ANN401` | `@typescript-eslint/no-explicit-any`、`@typescript-eslint/no-unsafe-*` |
| 公开函数的参数和返回值写类型 | 接口类型靠推断 | 编译器强制 | `ANN` | `@typescript-eslint/explicit-module-boundary-types` |
| 可能为空的值先处理再用 | 空值崩溃 | 编译器强制（`Option`） | `reportOptional*`（strict） | `strictNullChecks`（strict） |
| 按下标取值当作可能取不到 | 越界、缺键 | 不适用 | 无现成规则 | `noUncheckedIndexedAccess` |
| 不做会丢数据的强制转换 | 截断、符号错误 | `clippy::cast_possible_truncation`、`clippy::cast_sign_loss` | 不适用 | 不适用 |
| 不写多余的类型转换和比较 | 掩盖类型错误 | `clippy::unnecessary_cast` | `reportUnnecessaryCast`、`reportUnnecessaryComparison` | `@typescript-eslint/no-unnecessary-type-assertion`、`@typescript-eslint/no-unnecessary-condition` |
| 屏蔽类型检查的注释写明规则，多余的报错 | 整片屏蔽 | 不适用（类型错误无法屏蔽） | `PGH003`、`reportUnnecessaryTypeIgnoreComment` | `@typescript-eslint/ban-ts-comment`（只准带说明的 `@ts-expect-error`） |
| 不用已废弃的 API | 用上将删的接口 | `deprecated` | `reportDeprecated` | `@typescript-eslint/no-deprecated` |

## 流程级

本 repo 只定要求，各 repo 用自己的语言和工具实现，比如用语言自带的语法解析找豁免和所在函数。实现放在 `.github/` 下，由 CODEOWNERS 锁住。

| # | 检查 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 |
|---|---|---|---|---|---|
| 13 | 行内豁免 | 各工具的豁免都算：lint、类型检查、覆盖率排除、跳过标记、死代码和重复代码的忽略、sanitizer 抑制、"生成文件"头。每一处豁免都要在豁免白名单里登记（工具、文件、规则编号、所在函数、次数、原因）；没登记的、次数对不上的、登记了但代码里已经没有的，都失败 | 豁免白名单 | 在代码里局部关掉检查 | 每个 PR |
| 14 | 测试结果状态门 | 结果文件存在，且是这次运行生成的；里面每个测试都是 passed：skipped、expected-fail、重试过、flaky 的数量为 0，状态白名单里的除外。另跑一次只收集不执行（collect-only），测试数要一致。测试 ID 和 merge-base 比，删掉或改名的 ID 让检查失败，删除名单里的除外 | 状态白名单、删除名单 | 加跳过标记、标成预期失败、重试到通过、改构建配置让测试不跑、删测试或改名、拿旧结果文件冒充 | 每个 PR |
| 15 | 先失败后通过 | PR 新增或改过的测试拿去跑 base 的源码：PR 改了行为时，至少一个失败；在 head 上全部通过 | `.github/` 里的 CI 文件 | 测试没测到这次改的行为 | 每个 PR |
| 16 | 隔离再跑 | 必跑的测试（时间允许就全部测试）再跑一遍：去掉 CI 环境变量、断网、随机时区、locale 和时钟、换一个 checkout 路径、打乱顺序；每个测试有超时 | `.github/` 里的 CI 文件 | linter 规则"不检测测试环境"没抓到的测试环境特判；测试依赖顺序、网络或机器；测试卡住 | 每个 PR |
| 17 | 工具版本 | 检查工具按它们自己锁定的清单安装，版本写死，和 PR 的依赖分开。跑检查工具的 job 不安装也不运行 PR 代码；运行 PR 代码的 job 只产出结果文件，由另一个 job 解析判定 | 检查工具清单 | PR 的代码或依赖改掉检查工具或它的结果；工具升级让检查悄悄变松 | 每个 PR |
| 18 | 生成内容 | 生成文件用锁住的生成器重新生成，逐字节相同；带"生成"头的文件必须在生成路径名单里；二进制、压缩过的代码、大段编码数据、符号链接只能出现在二进制白名单里；golden 和快照文件锁住，测试从不以更新模式运行 | 生成路径名单、生成器配置、二进制白名单、golden 和快照目录 | 把逻辑藏进"生成"文件、二进制或编码数据躲过检查；改 golden 文件迁就错误输出 | 每个 PR |
| 19 | 只读范围 | 测试运行时，源码、测试、fixture、快照目录只读 | `.github/` 里的 CI 文件 | 测试运行中改源码、测试或快照 | 每个 PR |
| 20 | 工作区不变 | 全部跑完后 `git status --porcelain --ignored` 没有输出：构建产物、缓存、结果文件都写到工作区外 | `.github/` 里的 CI 文件、`.gitignore` | 测试或生成器偷偷改文件、改快照；写进被 ignore 的路径躲过检查 | 每个 PR |
| 21 | workflow 改动单独成 PR | 改了 `.github/workflows/` 下任何文件的 PR，不能改别的文件。实现：`ci.yml` 里一步，用 `git diff --name-only "$BASE_REF"...HEAD` 列出改动；这一步跑的是 main 上的 `ci.yml`，PR 删不掉它 | `.github/` 里的 CI 文件 | 必跑的 CI 跑的是 main 上的 `ci.yml`，改 `ci.yml` 的 PR 只由旧版检查；夹带的别的改动也就没经过新版检查 | 每个 PR |

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
