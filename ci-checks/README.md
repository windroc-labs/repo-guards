# CI 检查

第 3 层：不依赖项目设计的检查，每个 repo 的 `.github/workflows/ci.yml` 都要跑。依赖项目设计的架构检查在第 4 层，见 [`architecture/`](../architecture/README.md)。

每项检查拿代码和一份"标准答案"比对，标准答案用 CODEOWNERS 锁住，改它要 owner 批准（见下文 CODEOWNERS）。CI 只比对，不重新生成标准答案。有模板的检查由本 repo 的 reusable workflow 跑：各 repo 的 `ci.yml` 用 `@main` 调用它们，它们从同一个 commit 的 repo-guards 读共用配置。共用配置改动要本 repo 的 owner 批准，合进 `main` 后各 repo 下一次运行就用上，不用改各自的 `ci.yml`。其余检查每个 repo 在自己的 `ci.yml` 里实现。

检查按实现方式分两组：语言级（按工具分）、流程级。"频率"一列：每个 PR，或定期。定期结果必须强制，不能只当建议：比如失败自动开 issue，挡住下一次发布。标"按项目需要"的检查适用才加。

## 每个 repo 自己要做的

1. 把 [`ci.yml`](ci.yml) 复制成 `.github/workflows/ci.yml`：删掉用不到的语言 job，`with:` 按下表填，`uses:` 保持 `@main`。`runs-on`：公开 repo 用默认的 GitHub-hosted runner，私有 repo 写 `'["self-hosted","linux"]'`（例子里两种都有）。
2. 把 [`dependabot.yml`](dependabot.yml) 复制成 `.github/dependabot.yml`，让 Dependabot 升级其他 action 在 `uses:` 里钉住的版本；repo-guards 的 workflow 用 `@main`，不用升级。
3. 要改共用配置，写进 `.github/checks/repo-guards.toml`（见[覆盖默认配置](#覆盖默认配置)）；jscpd 基线放 `.github/checks/jscpd-baseline.json`。`.github/` 已由 CODEOWNERS 锁住。
4. 接入行内豁免（[第 13 项](#行内豁免)）：语言 workflow 和 `jscpd.yml` 已带这一步，`ci.yml` 不用加 job；把代码里现有的每一处行内豁免登记进 `.github/checks/repo-guards.toml` 的 `["ci-checks/inline-exemptions/settings.toml"]` 节，各写原因；第一次运行的输出就是要加的条目。
5. 第 2 层的设置，见 [`repo-settings`](../repo-settings/README.md)。
6. 可选：本地要读规则、跑工具时，把 [`fetch-ci-checks/fetch_ci_checks.py`](fetch-ci-checks/fetch_ci_checks.py)（只用标准库）复制进 repo，在 repo 根目录跑它。它确认 `ci.yml` 用 `@main` 调用 repo-guards，把 `main` 当前的 `ci-checks/` 下载到 `.repo-guards/`，commit 记在 `.repo-guards/SHA`，`main` 改了就重跑。下载后它用下载下来的 [`config-overrides/config_overrides.py`](config-overrides/config_overrides.py) 把 `.github/checks/repo-guards.toml`（`--overrides` 可改路径）写进 `.repo-guards/` 里对应的文件，和 CI 一致；合并失败就退出 2，留着上次下载的。它会运行下载来的代码，从哪个 repo 下载由 `ci.yml` 的 `uses:` 决定，所以只在 `ci.yml` 是 CODEOWNERS 批准过的那份的检出上跑。脚本在 `.repo-guards/` 里写一个内容为 `*` 的 `.gitignore`，git 自己忽略整个目录，不用改 repo 的 `.gitignore`。本地只有 `ci-checks/`，覆盖它以外文件的节在本地会失败。测试：在该目录跑 `python3 -m unittest fetch_ci_checks_test`（要 Python 3.11.4 及以上）。

| workflow | 跑什么 | 输入（默认） |
|---|---|---|
| [`rust.yml`](../.github/workflows/rust.yml) | [Rust](rust/README.md) 各项和依赖检查 | `working-directory`（`.`）、`unit-tests`（`--lib --bins`） |
| [`python.yml`](../.github/workflows/python.yml) | [Python](python/README.md) 各项和依赖检查 | `working-directory`（`.`） |
| [`typescript.yml`](../.github/workflows/typescript.yml) | [TypeScript](typescript/README.md) 各项和依赖检查 | `working-directory`（`.`） |
| [`go.yml`](../.github/workflows/go.yml) | [Go](go/README.md) 各项和依赖检查 | `working-directory`（`.`）、`unit-tests`（`./...`） |
| [`jscpd.yml`](../.github/workflows/jscpd.yml) | [重复代码](#重复代码) | 无 |
| [`github-dir-pr.yml`](../.github/workflows/github-dir-pr.yml) | [第 21 项](#github-改动单独成-pr) | 无 |

每个都有 `runs-on` 输入：runner 标签的 JSON，默认 `'"ubuntu-latest"'`。`working-directory` 是项目所在目录（`Cargo.lock`、`pyproject.toml`、`requirements.txt`、`package.json` 或 `go.mod` 所在处，如 `crates/app`），相对 repo 根目录；各项检查都在这里跑，`source-dirs` 相对它。`unit-tests` 选覆盖率要量的单元测试：Rust 是测试目标，Go 是 `go test` 的参数（包模式），见[覆盖率](#覆盖率)。`coverage-shards` 把覆盖率分片，见下文。别的设置都在共用配置文件里，项目用覆盖文件改，见[覆盖默认配置](#覆盖默认配置)。准用的许可证只写在 [`licenses.toml`](licenses.toml)：`allow` 是 SPDX 名单，四种语言共用；`exceptions` 是 Python、TypeScript、Go 不查许可证的包（Go 是模块路径，要完全相同），默认空；`python-classifiers` 把 pip 报的 trove classifier 名对到 SPDX。Rust 的例外是 [`rust/deny.toml`](rust/deny.toml) 的 `licenses.exceptions`，cargo-deny 的写法。

四个语言 workflow 各有两个并行的 job：快的检查合在 Checks 里；慢的只有覆盖率（要跑单元测试），单独占 Coverage。在实测的 caller 上，其余各项每项最多几秒，覆盖率要两分多钟。每个 job 自己检出代码，只装自己用到的工具；job 里一项失败，同一 job 后面的项照跑；只有 Checks 的第一项 Inline exemptions（[第 13 项](#行内豁免)）例外，它失败时同一 job 后面的项都不跑。

| workflow | job（里面的步） |
|---|---|
| `rust.yml` | Checks（Inline exemptions、Format、Lint、Comment budget、Type check and dead code、Unused dependencies、Dependency rules、Dependency allowlist）、Coverage（Coverage、Coverage gate、CRAP report） |
| `python.yml` | Checks（Inline exemptions、Format、Lint、Comment budget、Type check、Dead code、Unused dependencies、Vulnerabilities、Dependency allowlist、Licenses）、Coverage（同上） |
| `typescript.yml` | Checks（Inline exemptions、Format、Comment budget、Type check、Dead code and unused dependencies、Vulnerabilities、Dependency allowlist、Licenses）、Coverage（同上） |
| `go.yml` | Checks（Inline exemptions、Format、Lint、Comment budget、Type check、Dead code、Unused dependencies、Vulnerabilities、Dependency allowlist、Licenses）、Coverage（同上） |

覆盖率慢时，caller 用 `coverage-shards` 输入分片：JSON 字符串列表，每项是一片要跑的测试，Rust 是这片的 `unit-tests` 值（如 `"--lib"`、`"--test=unit"`，可在 `--` 后带测试名过滤，如 `"--test=unit -- input::"`，分片时代替 `unit-tests`），Python 是 pytest 的测试路径，TypeScript 是 vitest 的文件过滤词，Go 是这片的 `go test` 参数（包模式，如 `"./a/..."`，可带 `-run=<正则>` 按测试名拆，分片时代替 `unit-tests`），都用空格分开。每片一个 Coverage shard job 并行跑，Coverage job 合并各片的数据再判门槛、出 CRAP 报告；有一片失败，Coverage 就失败。没列进任何一片的测试不跑。默认 `[]`：一个 Coverage job，和以前一样。每片和 Coverage job 都要各自装工具、编译（Rust 的 Coverage job 还要按所有片的测试目标再编译一遍），所以只有跑测试的时间远多于编译时，分片才快。

分片的数据用各工具自己的办法合并：Rust 在 Coverage job 里按所有片的测试目标（各片 `--` 前的词，去重）再编译一遍（不跑测试），用 `cargo llvm-cov report` 合并各片的 `.profraw`；Python 用 `coverage combine`；TypeScript 用 vitest 的 blob 报告和 `--merge-reports`，blob 报告里是绝对路径，各片先把检出目录换成占位符；Go 各片各写一份 `-coverprofile`，Coverage job 把所有片的同一块代码合并成一条。在样例项目上，各片放在不同目录里跑，合并出的报告和不分片的逐文件、逐行、逐分支一致。四种语言各片的 artifact 名字由 [`coverage/shard_artifact.py`](coverage/shard_artifact.py) 生成：`<语言>-coverage-<working-directory 的 sha256 前 16 位>-shard`，只含小写字母、数字和 `-`，因为 download-artifact 对名字做 `decodeURIComponent`（`%` 会让下载失败），还把它当 glob 匹配；同一个 caller 用不同 `working-directory` 调用多次，名字也各不相同。测试：在 `coverage/` 跑 `python3 -m unittest shard_artifact_test`。

### 覆盖默认配置

共用配置的默认值都在 repo-guards 里，每份用工具自己的格式。项目要改，都写在一个文件里：`.github/checks/repo-guards.toml`（`.github/` 由 CODEOWNERS 锁住）。没有这个文件，全部用默认。

- 节名是从 repo-guards 根目录算起的文件路径，如 `["ci-checks/rust/deny.toml"]`。repo-guards 里任何 `.toml`、`.json` 文件都能覆盖，没有例外：覆盖率门槛、`.jscpd.json`、写在 TOML 里的工具版本、`licenses.toml`、`rust/deny.toml` 的安全规则都算。节里只写和默认不一样的项。
- 合并：表逐层按键合并；列表和其他值整个替换。列表没有例外：要在默认列表上加一条，连默认的一起写。只能改值，不能删键。
- 每个 job 检出 repo-guards、装好 uv 后，先跑 Apply caller overrides 步：[`config-overrides/config_overrides.py`](config-overrides/config_overrides.py)（只用标准库，要 Python 3.11 及以上）的 `apply` 把各节原地写进检出的 `repo-guards/` 里对应的文件（TOML 重写后不留注释，JSON 缩进 2 格），后面各步照旧读这些文件。
- 节对应的文件不存在、不是 `.toml` 或 `.json`、读不懂，或者节不是表，合并就失败并报出节名，一个文件都不写。路径不做别的检查。
- 保留节 `[project."<working-directory>"]` 不是文件，放项目设置，现在只有 `source-dirs`：Python 要查的源码目录，默认 `["src"]`，按 workflow 的 `working-directory` 选节。
- 依赖白名单在 [`dependency-allowlist/allowlist.toml`](dependency-allowlist/allowlist.toml) 的 `allow`（见[依赖白名单](#依赖白名单)），Python、TypeScript、Go 的许可证例外在 [`licenses.toml`](licenses.toml) 的 `exceptions`，Rust 的依赖来源、构建脚本白名单和许可证例外在 [`rust/deny.toml`](rust/deny.toml)，knip 的 `entry`、`project` 在 [`typescript/knip.json`](typescript/knip.json)：默认都是共用值或空列表，项目在对应文件那一节改。
- clippy、tsc、deptry、cargo machete、vitest 读项目自己的文件，不归这个文件管；jscpd 基线照旧是 `.github/checks/jscpd-baseline.json`。
- 覆盖能放宽安全检查，这是有意的：比如放开 `rust/deny.toml` 的依赖来源（`sources`）、忽略漏洞（`advisories`），加依赖白名单和许可证例外。把关靠 CODEOWNERS：改这个文件要 owner 批准，审的时候看清每一节放宽了什么。
- 测试：在 `config-overrides/` 跑 `python3 -m unittest config_overrides_test`。

```toml
["ci-checks/coverage/coverage.toml"]
total-min = 70

["ci-checks/dependency-allowlist/allowlist.toml"]
allow = ["serde@1.0.228", "libc"]

["ci-checks/licenses.toml"]
exceptions = ["some-package"]

["ci-checks/typescript/knip.json"]
entry = ["src/main.ts!"]

[project."."]
source-dirs = ["src", "tools"]
```

## 语言级

现成工具加配置：一行一类工具，对应一份锁住的配置；没有现成工具的（注释预算）用自己的检查器。每项的细节和 Rust、Python、TypeScript、Go 默认选用的工具见[附录：语言级各项](#语言级各项)；每种语言的模板配置和跑它的步见各语言 README。"模板"一列：✅ 四种语言的模板和 workflow 都有了，⚠️ 只做了一部分，空着是还没开始。

| # | 项 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 | 模板 |
| --- | --- | --- | --- | --- | --- | --- |
| 1 | [formatter](#formatter) | 用检查模式跑，CI 从不改写代码；PR 改过的文件格式不一致就失败（见[只查改动行](#只查改动行)） | formatter 配置 | 放宽规则 | 每个 PR | ✅ |
| 2 | [linter](#linter-规则) | 下文规则全部开着；只有 PR 改动行上的问题失败，现有的不挡（见[只查改动行](#只查改动行)） | lint 配置、escape hatch 白名单 | 放宽规则；见下文各条 | 每个 PR |  |
| 3 | [注释预算](#注释预算) | 每个改动的文件，注释字符数不超过代码字符数的一定比例 | 注释预算配置 | 注释堆砌 | 每个 PR | ✅ |
| 4 | [类型检查](#类型检查规则) | 下文规则全部开着；只有 PR 改动行上的问题失败（见[只查改动行](#只查改动行)） | 类型检查配置 | 放宽规则 | 每个 PR | ✅ |
| 5 | [死代码](#死代码) | 只算发布代码里的调用：只有测试调用的代码也是死代码；只有 PR 改动行上的问题失败（见[只查改动行](#只查改动行)） | 检查配置 | 没用的代码；只有测试调用的函数 | 每个 PR | ✅ |
| 6 | [重复代码](#重复代码) | 没有大段重复；现有的重复记进重复基线，基线只能变小；测试代码排除或用更松的门槛 | 检查配置、重复基线 | 复制粘贴；默认只认完全相同的 token，改个名字就躲过，交给[第 6 层](../README.md#要-guard-一个-repo需要这六层)独立审计 | 每个 PR | ✅ |
| 7 | [依赖检查](#依赖检查规则) | lockfile 和 base 比 diff：新增或变了的包必须在依赖白名单里；依赖来源、安装和测试时的行为也受限 | 依赖白名单 | 引入改过的包、在安装或测试时改结果的插件、有漏洞或许可证不合的包、没用的依赖 | 每个 PR | ⚠️ |
| 8 | [覆盖率](#覆盖率) | 行和分支都算（Go 没有分支覆盖率，只算行）。总覆盖率 ≥ 80%，PR 改动的行 ≥ 90%，都是固定门槛。只算发布代码 | 覆盖率门槛、覆盖率配置 | 少写测试、删测试；新代码不测，靠旧代码撑总覆盖率；靠测试代码自己的覆盖撑比例 | 每个 PR | ✅ |
| 9 | [CRAP 分数](#crap-分数) | 只报告，不强制：每个函数的 CRAP 分数（圈复杂度和覆盖率合算），最高的 20 个写进 job summary；这一步永远不失败 | 无 | 复杂的函数不测（只提示，不拦） | 每个 PR | ⚠️ |
| 10 | [变异测试](#变异测试) | 每个 PR 只变异改动的行，有变异体存活就失败；定期跑全部代码，存活的补测试。变异白名单里的除外 | 变异白名单 | 测试执行了代码，但不检查结果 | 每个 PR（改动行）、定期（全部） |  |
| 11 | [sanitizer](#sanitizer) | 定期：全部测试在内存、线程、未定义行为 sanitizer 下跑。按项目需要：每个 PR 跑轻量版（sanitizer 和竞态检测） | 豁免白名单 | 平时测试暴露不出的内存和并发错误 | 定期；按项目需要每个 PR |  |
| 12 | [文档测试](#文档测试)（按项目需要） | 文档里的示例能跑，结果和文档一致 | 文档 | 文档和代码脱节 | 每个 PR |  |

## 流程级

本 repo 只定要求（第 13 项除外，由四个语言 workflow 和 [`jscpd.yml`](../.github/workflows/jscpd.yml) 实现；第 21 项除外，由 [`github-dir-pr.yml`](../.github/workflows/github-dir-pr.yml) 实现；第 17 项由本 repo 的共享 workflow 部分实现），各 repo 用自己的语言和工具实现。实现放在 `.github/` 下，由 CODEOWNERS 锁住。要求较长的检查的细节见[附录：流程级各项](#流程级各项)。

状态指本 repo 的实现：✅ 已实现，⚠️ 部分实现，空着为未实现。

| # | 检查 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 | 状态 |
|---|---|---|---|---|---|---|
| 13 | [行内豁免](#行内豁免) | 每一处豁免都要在豁免白名单里登记，登记和代码对不上就失败 | 豁免白名单 | 在代码里局部关掉检查 | 每个 PR | ✅ |
| 14 | [测试结果状态门](#测试结果状态门) | 这次运行的结果文件里每个测试都是 passed；测试数和测试 ID 都对得上 | 状态白名单、删除名单 | 加跳过标记、标成预期失败、重试到通过、改构建配置让测试不跑、删测试或改名、拿旧结果文件冒充 | 每个 PR |  |
| 15 | 先失败后通过 | PR 新增或改过的测试拿去跑 base 的源码：PR 改了行为时，至少一个失败；在 head 上全部通过 | `.github/` 里的 CI 文件 | 测试没测到这次改的行为 | 每个 PR |  |
| 16 | 隔离再跑 | 必跑的测试（时间允许就全部测试）再跑一遍：去掉 CI 环境变量、断网、随机时区、locale 和时钟、换一个 checkout 路径、打乱顺序；每个测试有超时 | `.github/` 里的 CI 文件 | linter 规则"不检测测试环境"没抓到的测试环境特判；测试依赖顺序、网络或机器；测试卡住 | 每个 PR |  |
| 17 | 工具版本 | 检查工具按它们自己锁定的清单安装，版本写死，和 PR 的依赖分开 | 检查工具清单 | PR 的依赖换掉检查工具；工具升级让检查悄悄变松 | 每个 PR | ⚠️ |
| 18 | [生成内容](#生成内容) | 生成文件能逐字节重新生成；二进制和编码数据只在白名单里；golden 和快照文件锁住 | 生成路径名单、生成器配置、二进制白名单、golden 和快照目录 | 把逻辑藏进"生成"文件、二进制或编码数据躲过检查；改 golden 文件迁就错误输出 | 每个 PR |  |
| 19 | 只读范围 | 测试运行时，源码、测试、fixture、快照目录只读 | `.github/` 里的 CI 文件 | 测试运行中改源码、测试或快照 | 每个 PR |  |
| 20 | 工作区不变 | 全部跑完后 `git status --porcelain --ignored` 没有输出：构建产物、缓存、结果文件都写到工作区外 | `.github/` 里的 CI 文件、`.gitignore` | 测试或生成器偷偷改文件、改快照；写进被 ignore 的路径躲过检查 | 每个 PR |  |
| 21 | [`.github/` 改动单独成 PR](#github-改动单独成-pr) | 改了 `.github/` 下任何文件的 PR，不能改别的文件 | `.github/` 里的 CI 文件 | 改 CI 的 PR 只经过旧版或它自己改过的检查，夹带的别的改动也就没经过新版检查 | 每个 PR | ✅ |

## CODEOWNERS

本页启用的检查，"标准答案"一列里的东西都进 CODEOWNERS，改它要 owner 批准；不锁的话，检查形同虚设。实现文件不锁，worker 才能自由改。

| 锁什么 | 包括 | 写法 | 防什么 |
|---|---|---|---|
| 工具配置和测试钩子 | 下表各语言的文件名 | 只写文件名（不带 `/`），任何深度都匹配，新建的也算；带目录的写成 `**/` 开头 | 改规则；子目录里新建同名配置，覆盖根目录那份；测试钩子把失败改成通过 |
| 白名单和基线 | 豁免、escape hatch、依赖、变异、状态、二进制白名单，删除名单；重复基线、覆盖率门槛 | 建议都放在一个目录里，锁整个目录 | 自己给自己放行 |
| 测试数据 | golden、快照、fixture 目录 | 目录 | 改标准答案迁就错误输出 |
| 生成相关 | 生成路径名单、生成器配置 | 文件 | 把逻辑藏进"生成"文件 |
| 检查工具清单 | 检查工具和版本 | 文件 | 换掉或降级检查工具 |
| git 配置 | `.gitignore`、`.gitattributes` | 文件名 | 把文件藏进被忽略的路径；改文件属性躲过 diff |
| 不锁 | 实现文件；lockfile 由 repo 自己定，它的变动由依赖检查管 | | |

各语言的配置文件名：

| 语言 | 进 CODEOWNERS 的配置文件名 |
|---|---|
| Rust | `rustfmt.toml`, `.rustfmt.toml`, `clippy.toml`, `.clippy.toml`, `**/.cargo/config.toml`, `**/.cargo/config`, `**/.cargo/mutants.toml`, `mutants.toml`, `deny.toml`, `rust-toolchain.toml`, `rust-toolchain`, `**/.config/nextest.toml`, `build.rs` |
| Python | `pyproject.toml`, `setup.py`, `ruff.toml`, `.ruff.toml`, `setup.cfg`, `tox.ini`, `pytest.ini`, `conftest.py`, `.coveragerc`, `mypy.ini`, `.mypy.ini`, `pyrightconfig.json`, `sitecustomize.py`, `usercustomize.py` |
| TypeScript | `package.json`, `.prettierignore`, `.eslintignore`, `tsconfig*.json`, `jsconfig*.json`, `.eslintrc*`, `eslint.config.*`, `.prettierrc*`, `prettier.config.*`, `vitest.config.*`, `vite.config.*`, `jest.config.*`, `.c8rc*`, `.nycrc*`, `stryker.conf.*`, `knip.json`, `.knip.json*`, `.npmrc` |
| Go | `go.mod`, `.golangci.yml`, `.golangci.yaml`, `.golangci.toml`, `.golangci.json`, `go.work` |

## 附录

### 语言级各项

#### 只查改动行

第 1、2、4、5 项只挡新问题，接入时不用改现有代码。工具照旧查整个项目，再由 [`diff-filter/diff_filter.py`](diff-filter/diff_filter.py)（只用标准库）只留 PR 改动行上的问题：

- 改动行：`git diff --text <base>...HEAD` 里新增或改过的行，删掉的行不算；`--text` 让 `.gitattributes` 标成二进制的文件也照样比。`<base>`：PR 是 base 分支（比的是 merge-base），merge queue 是 base sha，push 是推送前的 commit（只看推上来的那段），新分支是默认分支。
- 有行号的问题（lint、类型检查、死代码）：落在改动行上才失败。
- 按文件算的问题（formatter、没用的文件）：PR 改过这个文件就失败，只删了行或只改了名也算。
- 在哪都失败：Rust 和 Go 的编译错误（Go 的由 golangci-lint 报成 `typecheck`，`go vet` 遇到编译错误非零退出，按工具出错算）、tsc 的配置错误，以及 knip 报的依赖问题（第 7 项，查的是 `package.json`，不按行）。clippy 加 `--cap-lints=warn`，deny 级 lint 也降成警告，旧代码里的不会中止编译；`cargo check` 不加 `-D warnings`，警告由脚本判。
- 其余问题打印成汇总（每个文件几个），不让 CI 失败。
- 工具出错算失败：退出码不在该工具的正常范围、输出读不懂，或退出码说有问题却一个也没读出来（cargo 退出 101 却没有 error 级消息也算）。
- 局限：Python、TypeScript 的类型错误落在没改的行上就不挡，比如 PR 改了函数签名，旧调用处报的错。Rust、Go 的编译错误照样挡。

| 项 | 内容 |
|---|---|
| 用法 | `diff_filter.py --base <ref> --format <格式> -- <命令…>`：在当前目录跑命令，stderr 原样输出，解析 stdout |
| 格式 | `cargo`（`--message-format=json`）、`ruff`、`ruff-format`（`--output-format json`）、`pyright`（`--outputjson`，只算 error）、`vulture`、`tsc`（`--pretty false`）、`knip`（`--reporter json`）、`golangci`（`--output.json.path stdout`）、`go-vet`（`go vet -json`）、`deadcode`、`paths`（每行一个文件：`prettier --list-different`、`cargo fmt --message-format short`、`gofmt -l`） |
| 退出码 | 0 通过，1 改动行上有问题，2 出错 |
| 测试 | 在该目录跑 `python3 -m unittest diff_filter_test` |

#### formatter

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| [rustfmt](rust/README.md#formatterrustfmt) | [ruff format](python/README.md#formatterruff-format) | [prettier](typescript/README.md#formatterprettier) | [gofmt](go/README.md#formattergofmt) |

#### linter 规则

所有规则都开在 error 级，改动行上的 warning 也让 CI 失败（Rust 由[只查改动行](#只查改动行)的脚本判，ESLint 用 `--max-warnings 0`；ruff 没有 warning 级别，golangci-lint 报的都算）。linter 配置至少开着这些规则。后四列是各语言的具体规则。禁用列表类规则是 Rust `clippy::disallowed_methods`（配置键 `disallowed-methods`）、Python `TID251`（配置键 `banned-api`）、TypeScript `no-restricted-*`、Go `forbidigo`（禁用的函数）和 `depguard`（禁用的包）；冒号后或括号里是要列进去的 API 或选择器。工具：Rust 用 clippy，Python 用 ruff，TypeScript 用 eslint（typescript-eslint），Go 用 [golangci-lint](go/README.md#lintergolangci-lint)。

| 类 | 规则 | 防什么 | Rust | Python | TypeScript | Go |
|---|---|---|---|---|---|---|
| 正确性 | 没有用不到的变量 | 漏用的值 | `unused_variables` | `F841` | `@typescript-eslint/no-unused-vars` | 编译器强制、`staticcheck`（`SA4006`，赋了值没读） |
| 正确性 | 没有用不到的 import | 残留依赖 | `unused_imports` | `F401` | `@typescript-eslint/no-unused-vars` | 编译器强制 |
| 正确性 | 没有用不到的参数 | 漏用的输入 | `unused_variables` | `ARG` | `@typescript-eslint/no-unused-vars` | `revive`（`unused-parameter`） |
| 正确性 | 不吞异常：空 catch、丢弃错误 | 错误被静默 | `unused_must_use`、`clippy::let_underscore_must_use` | `S110` | `no-empty` | `errcheck`（`check-blank`）、`nilerr` |
| 正确性 | 不吞异常：裸 except、宽泛 except | 误吞无关异常 | 不适用 | `E722`、`BLE001` | 不适用 | 不适用 |
| 正确性 | 不丢弃异步结果 | 异步任务没跑或错误丢失 | `unused_must_use`（`Future` 带 must_use）、`clippy::let_underscore_future` | `RUF006` | `@typescript-eslint/no-floating-promises` | `errcheck`（`go` 语句里丢弃的错误） |
| 正确性 | 没有无效果的表达式 | 写了却不生效的代码 | `clippy::no_effect` | `B015`、`B018` | `@typescript-eslint/no-unused-expressions` | 编译器强制（表达式语句）、`staticcheck`（`SA4017`） |
| 正确性 | 不遮蔽变量 | 用错同名变量 | 不适用（惯用同名重新绑定） | `A`（只查内置名）、`PLW2901` | `@typescript-eslint/no-shadow` | `predeclared`（只查内置名） |
| 正确性 | 浮点数不做相等比较 | 精度误差 | `clippy::float_cmp` | `RUF069`（preview） | 无现成规则 | `testifylint`（`float-compare`，只查 testify 断言） |
| 正确性 | 不拿常量跟常量比较 | 恒真恒假的条件 | `clippy::eq_op`（只查两边相同） | `PLR0133` | `no-self-compare`（只查两边相同）、`no-constant-condition`（只查条件里） | `staticcheck`（`SA4000`，只查两边相同） |
| 正确性 | 不做会丢数据的强制转换 | 截断、符号错误 | `clippy::cast_possible_truncation`、`clippy::cast_sign_loss` | 不适用 | 不适用 | `gosec`（`G115`） |
| 正确性 | 不写多余的类型转换和比较 | 掩盖类型错误 | `clippy::unnecessary_cast` | 见[类型检查规则](#类型检查规则) | `@typescript-eslint/no-unnecessary-type-assertion`、`@typescript-eslint/no-unnecessary-condition` | `unconvert`、`staticcheck`（`S1002`） |
| 可读性 | 函数长度有上限 | 难读的长函数 | `clippy::too_many_lines` | `PLR0915` | `max-lines-per-function` | `funlen` |
| 可读性 | 参数个数有上限 | 难用的接口 | `clippy::too_many_arguments` | `PLR0913` | `max-params` | `revive`（`argument-limit`） |
| 可读性 | 嵌套深度有上限 | 难读的分支 | `clippy::excessive_nesting`（须在 `clippy.toml` 设 `excessive-nesting-threshold`，默认 0 即关闭） | `PLR1702`（preview） | `max-depth` | `revive`（`max-control-nesting`） |
| 可读性 | 圈复杂度或认知复杂度有上限 | 难测的函数 | 无可靠规则（靠 `excessive_nesting` 加 `too_many_lines`） | `C901` | `complexity` | `gocyclo` |
| 可读性 | 文件长度有上限 | 大杂烩文件 | 无现成规则 | 无现成规则 | `max-lines` | `revive`（`file-length-limit`） |
| 可读性 | 命名风格统一 | 名字难认 | `non_snake_case`、`non_camel_case_types`、`non_upper_case_globals` | `N` | `@typescript-eslint/naming-convention` | `revive`（`var-naming`） |
| 可读性 | 不用布尔位置参数 | 调用处看不懂 | `clippy::fn_params_excessive_bools`（`clippy.toml` 设 `max-fn-params-bools = 0`，默认 3） | `FBT` | 无现成规则 | `revive`（`flag-parameter`，只查用作控制开关的布尔参数） |
| 可读性 | 没有注释掉的代码 | 死代码藏在注释里 | 无现成规则 | `ERA001` | 无现成规则 | `gocritic`（`commentedOutCode`） |
| 可读性 | 能简化的写法要简化 | 绕弯的写法 | `clippy::complexity`（组） | `SIM` | `no-else-return`、`no-lonely-if`、`no-unneeded-ternary`、`@typescript-eslint/prefer-optional-chain` | `staticcheck`（`S1*`） |
| 可读性 | 不用旧版本语法 | 过时写法 | `rust_2018_idioms`（组） | `UP` | `no-var`、`prefer-rest-params`、`prefer-spread`、`object-shorthand` | `modernize` |
| 可读性 | 不用相对 import | 移动文件就断 | 不适用 | `TID252` | `import/no-relative-parent-imports`（eslint-plugin-import） | 编译器强制（模块模式下相对 import 编译不过） |
| 可读性 | 公开函数的参数和返回值写类型 | 接口类型靠推断 | 不适用（见[类型检查规则](#类型检查规则)） | `ANN` | `@typescript-eslint/explicit-module-boundary-types` | 不适用（见[类型检查规则](#类型检查规则)） |
| 危险写法 | 库代码不用崩溃式捷径 | 崩溃 | `clippy::unwrap_used`、`clippy::expect_used`、`clippy::panic` | `S101` | `@typescript-eslint/no-non-null-assertion` | `forbidigo`（`panic`）、`forcetypeassert` |
| 危险写法 | 测试里不 sleep | 时好时坏的测试 | `clippy::disallowed_methods`（`disallowed-methods`：`std::thread::sleep`） | `TID251`（`banned-api`：`time.sleep`） | `no-restricted-syntax`（`setTimeout`） | `forbidigo`（`time.Sleep`） |
| 危险写法 | 测试里不读真实时间 | 时好时坏的测试 | `clippy::disallowed_methods`（`disallowed-methods`：`SystemTime::now`、`Instant::now`） | `TID251`（`banned-api`：`time.time`、`datetime.datetime.now`） | `no-restricted-syntax`（`Date.now`、`new Date()`） | `forbidigo`（`time.Now`、`time.Since`、`time.Until`） |
| 危险写法 | 不残留调试器 | 发布代码停住 | 不适用 | `T100` | `no-debugger` | `forbidigo`（`runtime.Breakpoint`） |
| 危险写法 | 不残留调试输出 | 输出泄露、刷屏 | `clippy::dbg_macro`、`clippy::print_stdout`、`clippy::print_stderr` | `T20` | `no-console` | `forbidigo`（`fmt.Print`、`fmt.Printf`、`fmt.Println`、`print`、`println`） |
| 危险写法 | 不用已废弃的 API | 用上将删的接口 | 见[类型检查规则](#类型检查规则) | 见[类型检查规则](#类型检查规则) | `@typescript-eslint/no-deprecated` | `staticcheck`（`SA1019`） |
| escape hatch | 动态 import 在白名单里 | 绕开分层规则 | 不适用 | `TID251`（`banned-api`：`importlib.import_module`） | `no-restricted-syntax`（`ImportExpression`） | `forbidigo`（`plugin.Open`） |
| escape hatch | eval 和动态执行在白名单里 | 绕开类型检查 | 不适用 | `S307`、`S102` | `no-eval`、`no-implied-eval`、`no-new-func` | 不适用 |
| escape hatch | 反射访问私有符号在白名单里 | 绕开封装 | 不适用 | `SLF001` | `no-restricted-syntax`（方括号访问私有成员） | `depguard`（`unsafe`：改私有字段、`//go:linkname` 都要它） |
| escape hatch | unsafe 或 FFI 在白名单里 | 绕开内存安全 | `unsafe_code` | `TID251`（`banned-api`：`ctypes`） | 不适用 | `depguard`（`unsafe`，cgo 也引入它） |
| escape hatch | 不检查的强制转换在白名单里 | 绕开类型检查 | 不适用 | `TID251`（`banned-api`：`typing.cast`） | `@typescript-eslint/no-unsafe-type-assertion` | 不适用（类型断言运行时检查；`unsafe.Pointer` 转换见上一行） |
| escape hatch | 不用 any 或类型未知的值 | 绕开类型检查 | 不适用 | `ANN401` | `@typescript-eslint/no-explicit-any`、`@typescript-eslint/no-unsafe-*` | 不适用（`any` 用前要类型断言） |
| escape hatch | 屏蔽类型检查的注释写明规则 | 整片屏蔽 | 不适用（类型错误无法屏蔽） | `PGH003` | `@typescript-eslint/ban-ts-comment`（只准带说明的 `@ts-expect-error`） | 不适用（类型错误无法屏蔽） |
| 不检测测试环境 | 发布代码不按名字读 CI 或测试标记变量，普通配置变量照常读；见[测试标记变量](#测试标记变量) | 测试环境走特判 | 无现成规则（自己写的检查） | 无现成规则（自己写的检查） | `no-restricted-syntax`，见[测试标记变量](#测试标记变量) | 无现成规则 |
| 不检测测试环境 | 测试模块以外不用测试专用编译开关（`cfg(test)`） | 测试环境走特判 | 无现成规则（自己写的检查） | 不适用 | 不适用 | 不适用（测试代码只在 `_test.go`） |
| 不检测测试环境 | 发布代码不查测试框架是否已加载 | 测试环境走特判 | 不适用 | `TID251`（`banned-api`：`sys.modules`） | `no-restricted-syntax`（`import.meta.vitest`、`globalThis.__vitest_worker__`） | `forbidigo`（`testing.Testing`） |
| 不检测测试环境 | 不按主机名、用户、当前目录分支 | 换台机器就变 | `clippy::disallowed_methods`（`disallowed-methods`：`std::env::current_dir`） | `TID251`（`banned-api`：`socket.gethostname`、`getpass.getuser`、`os.getcwd`） | `no-restricted-imports`（`node:os`）、`no-restricted-syntax`（`process.cwd`） | `forbidigo`（`os.Hostname`、`os.Getwd`）、`depguard`（`os/user`） |
| 不检测测试环境 | 发布代码不 import mock 库 | 生产里留 mock | 编译器（mock 库只放 dev-dependencies） | `TID251`（`banned-api`：`unittest.mock`） | `no-restricted-imports` | `depguard`（`github.com/stretchr/testify/mock`、`github.com/golang/mock`、`go.uber.org/mock`） |
| 不新增占位 | 不新增 TODO、FIXME 注释 | 留占位冒充完成 | 无现成规则 | `FIX` | `no-warning-comments` | `godox`（`TODO`、`FIXME`、`XXX`） |
| 不新增占位 | 不新增"未实现"占位 | 留占位冒充完成 | `clippy::todo`、`clippy::unimplemented` | 无现成规则 | 无现成规则 | `forbidigo`（`panic`，见崩溃式捷径一行） |
| 测试质量 | 每个测试有断言 | 空测试 | 无现成规则 | 无现成规则 | `vitest/expect-expect`（`@vitest/eslint-plugin`） | 无现成规则 |
| 测试质量 | 没有恒真或常量断言 | 空测试 | `clippy::assertions_on_constants` | `F631`、`PLW0129` | 无现成规则 | `testifylint`（`useless-assert`，只查 testify）、`staticcheck`（`SA4000`） |
| 测试质量 | 测试里没有宽泛的 catch | 吞掉失败 | `clippy::disallowed_methods`（`disallowed-methods`：`std::panic::catch_unwind`） | `BLE001`、`PT011` | `no-restricted-syntax`（`CatchClause`） | `forbidigo`（`recover`） |
| 测试质量 | 测试里没有行内覆盖超时、随机种子、样例数 | 在测试里局部放宽 | 无现成规则 | `TID251`（`banned-api`：`hypothesis.settings`、`random.seed`） | `no-restricted-syntax` | `forbidigo`（`rand.Seed`、`quick.Config`） |

##### 测试标记变量

"发布代码不按名字读 CI 或测试标记变量"一条的细节。标记变量指 `CI`、`TEST`、`NODE_ENV`、`VITEST`、`VITEST_*`、`GITHUB_*`、`RUNNER_*`、`PYTEST_*`。变量名必须是字符串字面量，动态拼名字躲不过。漏网的由流程级[隔离再跑](#流程级)（第 16 项）抓。

TypeScript 用 `no-restricted-syntax`，配这四个选择器：

- 点号读标记变量：`MemberExpression[object.object.name="process"][object.property.name="env"][computed=false][property.name=/^(CI|TEST|NODE_ENV|VITEST(_.*)?|(GITHUB|RUNNER|PYTEST)_.*)$/]`
- `["CI"]` 写法读标记变量：`MemberExpression[object.object.name="process"][object.property.name="env"][computed=true][property.value=/^(CI|TEST|NODE_ENV|VITEST(_.*)?|(GITHUB|RUNNER|PYTEST)_.*)$/]`
- 非字面量下标：`MemberExpression[object.object.name="process"][object.property.name="env"][computed=true][property.type!="Literal"]`
- 不是直接 `.NAME` 或 `["NAME"]` 成员访问的 `process.env`，如别名 `const e = process.env; e.CI`、解构、整个传出去：`MemberExpression[object.name="process"][property.name="env"]:not(MemberExpression > .object)`

#### 注释预算

代码自己说明自己。没有现成 linter 能查，每种语言用该语言写一个自己的检查器，放在 `ci-checks/<语言>/comment-budget/`。怎么量、上限多少，见各语言 README 的"注释预算"一节：

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| [comment-budget](rust/README.md#注释预算) | [comment-budget](python/README.md#注释预算) | [comment-budget](typescript/README.md#注释预算) | [comment-budget](go/README.md#注释预算) |

#### 类型检查规则

工具：Rust 用[编译器](rust/README.md#类型检查编译器)，Python 用 [pyright](python/README.md#类型检查pyright)，TypeScript 用 [tsc](typescript/README.md#类型检查tsc)，Go 用编译器和 [go vet](go/README.md#类型检查go-vet)。

类型检查配置至少开着这些规则。只列类型检查器自己查的规则（rustc 及其 lint、pyright、tsc、Go 编译器和 go vet）；linter 实现的在 [linter 规则](#linter-规则)。

| 规则 | 防什么 | Rust | Python | TypeScript | Go |
|---|---|---|---|---|---|
| 最严格模式 | 放宽检查 | 编译器本身 | pyright `typeCheckingMode = "strict"` | tsc `strict` | 编译器本身、`go vet`（全部默认分析器） |
| 不准 any 或类型未知的值 | 类型检查失效 | 不适用 | `reportUnknown*`（strict） | `noImplicitAny`（strict，只管隐式 any）；显式 any 见 [linter 规则](#linter-规则) | 不适用（`any` 用前要类型断言） |
| 公开函数的参数和返回值写类型 | 接口类型靠推断 | 编译器强制 | `reportMissingParameterType`（strict，只管参数）；返回值见 [linter 规则](#linter-规则) | 见 [linter 规则](#linter-规则) | 编译器强制 |
| 可能为空的值先处理再用 | 空值崩溃 | 编译器强制（`Option`） | `reportOptional*`（strict） | `strictNullChecks`（strict） | 无现成规则 |
| 按下标取值当作可能取不到 | 越界、缺键 | 不适用 | 无现成规则 | `noUncheckedIndexedAccess` | 无现成规则 |
| 不写多余的类型转换和比较 | 掩盖类型错误 | 见 [linter 规则](#linter-规则) | `reportUnnecessaryCast`、`reportUnnecessaryComparison`（strict） | 见 [linter 规则](#linter-规则) | 见 [linter 规则](#linter-规则) |
| 屏蔽类型检查的注释写明规则，多余的报错 | 整片屏蔽 | 不适用（类型错误无法屏蔽） | `reportUnnecessaryTypeIgnoreComment`；写明规则见 [linter 规则](#linter-规则) | 见 [linter 规则](#linter-规则) | 不适用（类型错误无法屏蔽） |
| 不用已废弃的 API | 用上将删的接口 | `deprecated` | `reportDeprecated`（strict） | 见 [linter 规则](#linter-规则) | 见 [linter 规则](#linter-规则)（`go vet` 不查） |

#### 死代码

文件内的由 linter 或编译器查；公开了但没人调用的要专门工具。模板见各语言 README：[Rust](rust/README.md#死代码编译器cargo-machete)、[Python](python/README.md#死代码vulturedeptry)、[TypeScript](typescript/README.md#死代码knip)、[Go](go/README.md#死代码unuseddeadcode)。

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| 非测试构建下的编译器 `dead_code`：`#[cfg(test)]` 的代码不参与编译；不查 crate 导出的 `pub` 项 | ruff；vulture 只扫发布代码目录，不扫测试 | tsc；knip `--production`：入口只取标 `!` 的发布代码 | golangci-lint `unused`（`--tests=false`，不看测试文件）；有 `main` 包的模块再跑 `deadcode`（不带 `-test`）；只有库的模块不查导出的函数，同 Rust 不查 `pub` 项 |

#### 重复代码

四种语言都用 jscpd，版本锁在 [`jscpd-requirements.in`](jscpd-requirements.in)（Rust 引擎；4.x 的 Node 引擎没有 `--baseline`），由 [`jscpd.yml`](../.github/workflows/jscpd.yml) 跑（之前先对账 `jscpd:ignore-start`，见[行内豁免](#行内豁免)），配置是共用的 [`.jscpd.json`](.jscpd.json)（项目在覆盖文件的 `["ci-checks/.jscpd.json"]` 节改）：`minTokens`、`minLines` 是门槛，短于它的重复不算；`ignore` 排除测试文件；`gitignore` 为 false：不读 PR 的 `.gitignore`，排除只认 `.jscpd.json`；`failOnNewClones` 为 0：不在基线里的重复都失败。基线固定在 `.github/checks/jscpd-baseline.json`，不存在时直接失败。接入时生成基线，之后只能变小：

```sh
jscpd --config .repo-guards/ci-checks/.jscpd.json --baseline .github/checks/jscpd-baseline.json --update-baseline .
```

`ignorePattern` 写在覆盖文件里，由 CODEOWNERS 审，不另登记为行内豁免。

#### 依赖检查规则

依赖检查至少开着这些规则。Rust 的配置模板见 [Rust README](rust/README.md#依赖检查cargo-deny)。"脚本"指各 repo 自己写的检查；标"项目白名单"的，名单内容由各项目定，放进锁住的依赖白名单。

| 规则 | 防什么 | Rust | Python | TypeScript | Go |
|---|---|---|---|---|---|
| 新增或变了的包在依赖白名单里 | 引入改过的包 | [依赖白名单](#依赖白名单)：`Cargo.lock` 和 merge-base 比（项目白名单） | 同 Rust 列，比 `uv.lock`，或由 `requirements.txt` 解析的 `pylock.toml` | 同 Rust 列，比 `package-lock.json` | 同 Rust 列，比 `go list -m all` 的 build list |
| 不准 path、git、URL 依赖 | 引入改过的包 | git 和别的 registry：cargo-deny `[sources]` `unknown-git = "deny"`、`unknown-registry = "deny"`，要开的源写进 `allow-git`、`allow-registry`（项目白名单）；path：无现成规则（脚本查 `Cargo.lock` 里没有 `source` 的包都是 workspace 成员） | 无现成规则（脚本查 `uv.lock` 里除了项目自己，每个包的 `source` 都是 `registry`）；uv `no-sources` 只忽略 `[tool.uv.sources]`，挡不住直接写在依赖里的 URL | [`.npmrc`](typescript/.npmrc) `allow-git=none`、`allow-remote=none`、`allow-file=none`；`allow-directory` 不设：`none` 让 npm workspaces 装不上（npm 11.19 实测报 `EALLOWDIRECTORY`），directory 依赖无现成规则 | path：只能写成 `replace` 到本地目录，见下一行；git、URL：不适用（`go.mod` 只按模块路径和版本写依赖） |
| 不准 override 或 patch | 改掉依赖解析结果 | 无现成规则（脚本查 `Cargo.toml` 和 `.cargo/config.toml` 里没有 `[patch]`、`[replace]`） | 无现成规则（脚本查 `pyproject.toml` 里没有 `[tool.uv]` `override-dependencies`） | 无现成规则（脚本查 `package.json` 里没有 `overrides`） | `replace`：无现成规则（脚本查 `go.mod` 里没有 `replace`） |
| 不准 submodule、不准新的 vendor 目录 | 绕开 lockfile 引入代码 | 无现成规则（四种语言共用的脚本：没有 `.gitmodules`，和 base 比没有新的 vendor 目录） | 同 Rust 列的共用脚本 | 同 Rust 列的共用脚本 | 同 Rust 列的共用脚本 |
| 关掉安装脚本 | 在安装时改结果 | `build.rs` 关不掉；cargo-deny `[bans.build]` `allow-build-scripts`：基础 crate 写在共用名单（[`deny.toml`](rust/deny.toml)），其余由项目定（项目白名单） | uv `[tool.uv]` `no-build = true`，只装 wheel（项目自己的包和 editable 依赖照样构建） | `.npmrc` `ignore-scripts=true` | 不适用：Go 构建不跑依赖的脚本 |
| 关掉测试插件自动加载 | 在测试时改结果的插件 | 不适用 | 环境变量 `PYTEST_DISABLE_PLUGIN_AUTOLOAD` 或 `--disable-plugin-autoload`（pytest 8.4 起），要用的插件用 `-p` 列出 | 不适用 | 不适用 |
| 漏洞扫描 | 有漏洞的包 | cargo-deny `[advisories]` | pip-audit `--locked`，读从 `uv.lock` 导出或由 `requirements.txt` 解析的 `pylock.toml`（2.10.1 不读 `uv.lock`） | `npm audit` | [govulncheck](go/README.md#依赖检查go-mod-tidygovulncheck)（只报代码调用到的） |
| 许可证在准用名单里 | 许可证不合的包 | cargo-deny `[licenses]`：共用名单（[`licenses.toml`](licenses.toml)）；例外由项目定 | pip-licenses 列出包，[`license-check`](python/README.md#许可证pip-licenses) 按 SPDX 表达式对共用名单（[`licenses.toml`](licenses.toml)）判定；例外由项目定 | license-checker-rseidelsohn：共用名单（[`licenses.toml`](licenses.toml)）；例外由项目定 | [`license-check`](go/README.md#许可证license-check) 认出 build list 里每个模块的许可证文件，对共用名单（[`licenses.toml`](licenses.toml)）判定；例外由项目定 |
| 没有用不到的依赖 | 没用的依赖 | [cargo-machete](rust/README.md#死代码编译器cargo-machete) | [deptry](python/README.md#死代码vulturedeptry)（`DEP002`） | [knip](typescript/README.md#死代码knip) | [`go mod tidy -diff`](go/README.md#依赖检查go-mod-tidygovulncheck) |

#### 依赖白名单

一份项目依赖白名单管四种语言：[`dependency-allowlist/allowlist.toml`](dependency-allowlist/allowlist.toml) 的 `allow`，默认空，项目在覆盖文件的 `["ci-checks/dependency-allowlist/allowlist.toml"]` 节写（见[覆盖默认配置](#覆盖默认配置)）。各 workflow 的 Dependency allowlist 步用 [`dependency-allowlist/dependency_allowlist.py`](dependency-allowlist/dependency_allowlist.py)（只用标准库）拿 PR 的 lockfile 和 merge-base 的比：新增的包和版本变了的包要在 `allow` 里，否则失败。没变的和删掉的不查，接入时不用列现有依赖；修法总是加一条。

- 条目：`"name"` 准这个包的所有版本，`"name@version"` 只准这个版本；也认 cargo-deny 的 `{ crate = "name@version" }` 和 `{ name = "...", version = "..." }`。包名照 lockfile 里的写，Go 写模块路径；失败的输出给出要加的那一条。
- 比什么：Rust 是 `Cargo.lock` 里有 `source` 的包（workspace 成员和 path 依赖不算）；Python 是 `uv.lock`，没有就是由 `requirements.txt` 解析的 `pylock.toml`（见 [Python README](python/README.md#依赖白名单和漏洞pylocktoml)）；TypeScript 是 `package-lock.json` 的 `packages` 里 `node_modules/` 下的包（workspace 链接不算）；Go 是 build list，即 PR 的项目目录和 merge-base 的副本里各跑一遍 `go list -mod=readonly -m -json all` 列出的模块，主模块不算，`replace` 成别的模块的按替换后的路径和版本算，`replace` 成本地目录的算项目自己的，不算（见 [Go README](go/README.md#依赖检查go-mod-tidygovulncheck)）。只换了来源、版本没变的包不算变了。
- merge-base 上没有 lockfile（Go 是 `go.mod`）时，所有包都算新增。
- 名单不分语言：一条 `name` 在四种语言里都放行同名的包。
- 名单不放进 `rust/deny.toml` 的 `bans.allow`：那里不空时，cargo-deny 会禁掉名单外的所有 crate。
- 测试：在该目录跑 `python3 -m unittest dependency_allowlist_test`。

#### 覆盖率

四种语言各自只跑单元测试、生成 Cobertura 报告，再用同一个检查器 [`coverage/coverage_gate.py`](coverage/coverage_gate.py)（只用标准库，要 Python 3.11 及以上）判定：

| 项 | 内容 |
|---|---|
| 总覆盖率 | (覆盖的行 + 覆盖的分支) ÷ (全部行 + 全部分支)，取 Cobertura 报告根节点的总数。不低于门槛。`go test` 不量分支，Go 的报告里没有分支，总覆盖率和改动行都只算行 |
| 改动行 | diff-cover 对 merge-base 算 PR 改动行的覆盖率，不低于门槛 |
| 门槛 | [`coverage/coverage.toml`](coverage/coverage.toml)：`total-min`、`diff-min`，0 到 100，两个键都必须写 |
| 退出码 | 0 通过，1 不达标，2 出错（配置不对、报告读不了、一行发布代码都没量到、找不到 diff-cover） |

diff-cover 的版本连同依赖带 hash 固定在 [`coverage/requirements.txt`](coverage/requirements.txt)（由 `requirements.in` 用 `uv pip compile --generate-hashes --universal --python-version 3.12` 生成）。测试：在 `coverage/` 跑 `python3 -m unittest coverage_gate_test`。

门槛只用固定值，不和 merge-base 比：和 merge-base 比，每个 PR 都只能持平或往上，门槛越叠越高。不设 100%：100% 会逼出空测试，断言由变异测试把关。测试文件和生成文件不进分母，排除名单写在各语言锁住的覆盖率配置里。第 9 项用同一份报告和配置。跑测试就是执行 PR 的代码，所以覆盖率的几步在各 workflow 单独的 Coverage job 里跑，静态检查的 job 不跑测试。

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| [cargo-llvm-cov](rust/README.md#覆盖率cargo-llvm-cov) | [coverage.py](python/README.md#覆盖率coveragepy) | [vitest 的 v8 coverage](typescript/README.md#覆盖率vitest-v8) | [`go test -cover`](go/README.md#覆盖率go-test) |
| `rust.yml` 的 `unit-tests` 输入选 cargo 测试目标，默认 `--lib --bins`：Coverage 步只跑这些目标 | repo 自己的 pytest 配置（`testpaths`、`addopts`）：Coverage 步跑不带参数的 `pytest` | repo 自己的 vitest 配置（`test.include`、`test.exclude`）：Coverage 步跑 `vitest run` | `go.yml` 的 `unit-tests` 输入选 `go test` 的包模式，默认 `./...`：Coverage 步只跑这些包；集成测试放在 build tag（如 `//go:build integration`）后面不参与默认编译，或不列进 `unit-tests` |

覆盖率只算单元测试：改动行、总覆盖率、第 9 项 CRAP 分数的覆盖率，都只来自单元测试。哪些是单元测试由各 repo 自己说（上表最后一行）。单元测试只跑一遍，就在 Coverage 步里（分片时在各片里）。集成测试是 repo 自己的，由 repo 自己的 job 跑，不进覆盖率，四个 workflow 都不跑。

覆盖率只说明代码被执行过，不说明测试检查了结果：空测试也能把覆盖率刷上去。识破空测试要靠变异测试（工具在临时副本里把代码改坏，测试没失败就说明没检查结果；第 10 项），或者[顶层 README](../README.md) 第 5 层的隐藏验收测试。

#### CRAP 分数

只报告，不强制。各 workflow 最后一步 CRAP report 用同一个脚本 [`crap/crap_report.py`](crap/crap_report.py)（只用标准库）算，把分数最高的 20 个函数写成表格放进 job summary（文件、函数、复杂度、覆盖率、分数），不留文件、不发 PR 评论。

| 项 | 内容 |
|---|---|
| 分数 | CRAP = 复杂度² × (1 − 覆盖率)³ + 复杂度 |
| 复杂度 | [lizard](https://github.com/terryyin/lizard) 算的圈复杂度，四种语言同一个工具。版本连同依赖带 hash 固定在 [`crap/requirements.txt`](crap/requirements.txt)（生成命令同覆盖率）。Rust 的 `match` 按分支数算，但范围模式的分支（`90..=100 =>`）lizard 不算，分数偏低 |
| 覆盖率 | 第 8 项 Coverage 步对 PR 跑出的同一份 Cobertura 报告，只来自单元测试，不再跑测试。一个函数的覆盖率 = 它起止行之间 (覆盖的行 + 覆盖的分支) ÷ (全部行 + 全部分支)，和第 8 项同一算法。声明那一行（`def`、`export const f =`）在模块加载时就算执行过，不计入，没被调用的函数才会是 0%；只有一行的函数例外。只量报告里有的文件，一行都没量到的函数不列 |
| 不失败 | 脚本永远退出 0；报告不存在、读不了、lizard 装不上或跑不了时，打印一行 `CRAP report skipped: <原因>`，同一行也写进 job summary。这一步另加 `continue-on-error`，任何意外都不改 job 结果 |

测试：在 `crap/` 跑 `python3 -m unittest crap_report_test`。

#### 变异测试

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| cargo-mutants | mutmut | Stryker | gremlins |

#### sanitizer

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| ASan、TSan、Miri | 不适用 | 不适用 | `-race`（竞态检测） |

#### 文档测试

| Rust | Python | TypeScript | Go |
|---|---|---|---|
| `cargo test --doc` | doctest | 无标准工具 | `Example` 函数（带 `// Output:`，由 `go test` 跑） |

### 流程级各项

#### 行内豁免

- 各工具的豁免都算：lint、类型检查、覆盖率排除、跳过标记、死代码和重复代码的忽略、sanitizer 抑制、"生成文件"头。
- 登记的内容：工具、文件、规则编号、所在函数、次数、原因。
- 没登记的、次数对不上的、登记了但代码里已经没有的，都失败。
- 例外：Rust 加在 cfg 正好是 `#[cfg(test)]` 的 `mod` 上的 `coverage(off)` 不用登记（模块名不限，`mod x { … }` 和 `mod x;` 都算，`cfg(any(test, …))` 不算；见[第 8 项](#覆盖率)），加在别处的照样登记。

实现：四个语言 workflow 的 Checks job 和 [`jscpd.yml`](../.github/workflows/jscpd.yml) 各有一步 Inline exemptions，调用方不用改 `ci.yml`。语言 workflow 的这一步在装好工具、套用完覆盖文件之后，是第一个检查步骤，只跑本语言的扫描器，只对账白名单里本语言工具的条目（见下面内置写法表的语言列）；它失败时同一 job 后面的检查步骤不跑。`jscpd` 的条目由 `jscpd.yml` 对账：它在 Duplication 步之前跑 repo 里有的每种语言的扫描器，只对账 `jscpd`，失败时 Duplication 不跑。`generated` 的条目按文件的语言归到那个语言 workflow；`file` 不是这四种语言文件的 `generated` 条目对不上任何代码，算白名单格式不对。白名单格式、`skip` 不对时，每个跑这一步的 workflow 都退出 2。调用方没调用的 workflow 管的工具不查。每一步都查 HEAD 上 git 跟踪的全部文件，从 repo 根目录算，不看 diff，不按路径排除，不扫 submodule 的内容；只读 `.rs`、`.py`、`.pyi`、`.ts`、`.tsx`、`.mts`、`.cts`、`.js`、`.jsx`、`.mjs`、`.cjs`、`.go` 和任何目录下的 `Cargo.toml`、`pyproject.toml`、`go.mod`。这些文件里有符号链接、语法错误或不是 UTF-8 时退出 2 并给出文件名。共用部分 [`inline-exemptions/inline_exemptions.py`](inline-exemptions/inline_exemptions.py)（只用标准库）读白名单、对账、报错；各语言的扫描器用该语言自己的解析器，放在各自目录：[`rust/inline-exemptions/`](rust/inline-exemptions)（`syn`）、[`python/inline-exemptions/`](python/inline-exemptions)（`ast`、`tokenize`）、[`typescript/inline-exemptions/`](typescript/inline-exemptions)（TypeScript 编译器的语法解析）、[`go/inline-exemptions/`](go/inline-exemptions)（`go/parser`、`golang.org/x/mod/modfile`）。扫描器只解析，不 import、不构建、不执行调用方的代码。调用方的 `.cargo/config.toml`、`rust-toolchain.toml`、`go.work`、`uv.toml` 也不生效：cargo 在根目录 `/` 运行（不在调用方的目录树里，也不在共享的临时目录里），工具链取本 repo 的 [`rust/rust-toolchain.toml`](rust/rust-toolchain.toml)；Go 设 `GOWORK=off`；uv 不读配置（`UV_NO_CONFIG`、`--no-config`）。

白名单写在 `.github/checks/repo-guards.toml` 的 `["ci-checks/inline-exemptions/settings.toml"]` 节，四种语言共用一份，每条一个表，六个字段都要写：

```toml
[["ci-checks/inline-exemptions/settings.toml".exemption]]
tool = "rustc"
file = "src/a.rs"
rule = "dead_code"
function = "Foo::bar"
count = 1
reason = "只在 feature full 下用到"
```

- 键是（`tool`、`file`、`rule`、`function`），同一个键只能写一条；`count` 是代码里这个键出现的次数，正整数；`reason` 不能为空。
- `file` 相对 repo 根目录。`function` 是最内层的具名函数或方法，只带最内层的所属类型或类（Rust `Foo::bar`，Python、TypeScript、Go `Foo.bar`），不带模块路径；闭包、lambda、匿名回调、Go 函数字面量算外层的具名函数；加在函数上的属性、装饰器和紧挨着声明的注释算那个函数；不在函数里的为 `""`。
- 一处写了几条规则按每条各算一次；能带规则列表的写法没写规则时，规则记 `"*"`。
- 同一节的 `skip` 列出不查的工具，默认空，只能写下表里的工具名；跳过的工具在白名单里的条目要删。
- 结果：0 一致；1 不一致，输出按（文件、函数、工具、规则）排序，列出要加（可直接粘贴，`reason` 留空）、要删、要改 `count` 的条目；2 跑不了（白名单格式不对、`skip` 有未知工具、源文件读不了）。输出里的文件名和规则都写成 TOML 字符串，不会产生 workflow 命令。
- 改这一节的 `exemption` 可以和代码在同一个 PR 里，见 [第 21 项](#github-改动单独成-pr)。

内置写法（表外的写法不算，调用方改不了；注释形式只认真正的注释，字符串、模板字符串、正则、JSX 文本和 Rust 文档注释里的不算）：

| 语言 | 写法 | 工具 / 规则 |
|---|---|---|
| Rust（含 `cfg_attr` 里的和宏体里照写的） | `#[allow(…)]`、`#[expect(…)]`、`#![allow(…)]`、`#![expect(…)]` | `clippy::` 开头的 `clippy`，其余 `rustc` / lint 名 |
| | `#[rustfmt::skip]`；`coverage(off)`；`#[ignore]` | `rustfmt` / `skip`；`cargo-llvm-cov` / `off`；`cargo-test` / `ignore` |
| | `#[no_sanitize(…)]`、`#[sanitize(<名> = "off")]` | `sanitizer` / sanitizer 名 |
| | `Cargo.toml` 的 `[lints.*]`、`[workspace.lints.*]` 里设成 `allow` 的 lint；`[package.metadata.cargo-machete]` 的 `ignored` | `clippy` 或 `rustc` / lint 名（`clippy::x`）；`cargo-machete` / crate 名 |
| Python（大小写、空格不限） | `# noqa`、`# noqa: <规则>`；文件级 `# ruff: noqa`、`# flake8: noqa` | `ruff`，`V` 开头的规则记 `vulture` |
| | `# fmt: off`、`# yapf: disable`；`# fmt: skip` | `ruff-format` / `off`；`skip` |
| | `# type: ignore[…]`、`# pyright: ignore[…]`；文件级 `# pyright: <设置>` | `pyright` / 规则或设置名 |
| | `# pragma: no cover`、`# pragma: no branch`（coverage.py 默认正则） | `coverage` / `no cover`、`no branch` |
| | `pytest.mark.skip`、`skipif`、`xfail`，`pytest.skip()`、`xfail()`、`importorskip()`（含别名导入和 `pytestmark`） | `pytest` / 标记或函数名 |
| | `unittest.skip`、`skipIf`、`skipUnless`、`expectedFailure`、`SkipTest`，`self.skipTest()` | `unittest` / 名字 |
| | 文件名含 `whitelist` 的 vulture 白名单里引用的名字；`pyproject.toml` 的 `[tool.deptry]` `ignore`、`per_rule_ignores` | `vulture` / 名字；`deptry` / 规则码或 `规则码:包` |
| TypeScript | `@ts-expect-error`、`@ts-ignore`、`@ts-nocheck` | `tsc` / 指令名 |
| | `eslint-disable`、`eslint-disable-line`、`eslint-disable-next-line`（` -- ` 后是说明），`/* eslint <规则>: "off" */` | `eslint` / 规则 |
| | `prettier-ignore`；`v8`、`c8`、`istanbul` 的 `ignore next`、`start`、`if`、`else`、`file`；JSDoc `@public` | `prettier` / `ignore`；`vitest-coverage` / 关键词；`knip` / `public` |
| | `test`、`it`、`describe`、`suite` 链上的 `.skip`、`.todo`、`.fails`、`.skipIf()`、`.runIf()`（含从 `vitest` 别名导入的），`ctx.skip()`、`context.skip()`，`xit`、`xtest`、`xdescribe` | `vitest` / 标记名 |
| Go | `//nolint`、`//nolint:<linter>,…`（`//` 后不带空格；写在 `package` 前的作用于整个文件） | `golangci-lint` / linter，`all` 或不写记 `*` |
| | `<x>.Skip()`、`Skipf()`、`SkipNow()` | `go-test` / 方法名 |
| | `//go:build`、`// +build` 行（不求值） | `go-build` / 条件原文 |
| | `go.mod` 的 `ignore` 路径；嵌套模块的 `go.mod` | `go-mod` / 路径；`module` |
| 四种语言 | 文件开头注释（第一个代码 token 之前）里的 `@generated`、`Code generated … DO NOT EDIT.`、protobuf 的生成头 | `generated` / `generated` |
| | `jscpd:ignore-start` | `jscpd` / `ignore` |

`.github/checks/repo-guards.toml` 里写的设置（如 `.jscpd.json` 的 `ignorePattern`、`typescript/knip.json` 的 `ignore*`）由 CODEOWNERS 审，不另登记。`# nosec`、`.only` 不在表里：`#nosec` 由 `golangci.toml` 关掉，focus 标记归[测试结果状态门](#测试结果状态门)。

本地：跑过 `fetch_ci_checks.py` 后，在 repo 根目录跑 `uv run --no-config --no-project --python 3.14 .repo-guards/ci-checks/inline-exemptions/inline_exemptions.py <对象>`，`<对象>` 是 `rust`、`python`、`typescript`、`go` 或 `jscpd`，结果和退出码同 CI 里对应 workflow 的这一步；`rust` 要装 `cargo`，`go` 要装 `go`，`typescript` 要装 `node` 和 `npm`，`jscpd` 要装 repo 里有的语言的这些工具。工作区有未提交的改动时，输出第一行先提示，检查按工作区里的内容做。测试：`inline-exemptions/` 跑 `python3 -m unittest inline_exemptions_test`，`python/inline-exemptions/` 跑 `python3 -m unittest exemptions_test`，`rust/inline-exemptions/` 跑 `cargo test --locked`，`go/inline-exemptions/` 跑 `go test ./...`，`typescript/inline-exemptions/` 跑 `npm ci --ignore-scripts && npm test`。

#### 测试结果状态门

- 结果文件存在，且是这次运行生成的。
- skipped、expected-fail、重试过、flaky 的数量为 0，状态白名单里的除外。
- 另跑一次只收集不执行（collect-only），测试数要一致。
- 测试 ID 和 merge-base 比，删掉或改名的 ID 让检查失败，删除名单里的除外。
- 这些也抓 focus 或禁用的测试、行内覆盖的重试次数，所以 linter 规则里不再单列这几条。

#### 生成内容

- 生成文件用锁住的生成器重新生成，逐字节相同。
- 带"生成"头的文件必须在生成路径名单里。
- 二进制、压缩过的代码、大段编码数据、符号链接只能出现在二进制白名单里。
- golden 和快照文件锁住，测试从不以更新模式运行。

#### `.github/` 改动单独成 PR

实现：[`github-dir-pr.yml`](../.github/workflows/github-dir-pr.yml) 跑 [`github-dir-pr/github_dir_pr.py`](github-dir-pr/github_dir_pr.py)，用 `git -c core.quotePath=false diff --name-only --no-renames -z "$BASE_REF"...HEAD` 列出改动：改名拆成删和增，旧路径也算；文件名按 NUL 分隔、不转义。只在 `pull_request` 上跑（merge queue 的 diff 是整组改动）；它由 main 上的 `ci.yml` 调用，PR 删不掉它。

例外：PR 在 `.github/` 下只改了 `.github/checks/repo-guards.toml`，且这个文件和 merge-base 比只差 `["ci-checks/inline-exemptions/settings.toml"]` 节的 `exemption` 时，可以同时改别的文件：加或删豁免的代码和它的登记要一起合（见[行内豁免](#行内豁免)）。测试：在 `github-dir-pr/` 跑 `python3 -m unittest github_dir_pr_test`。

为什么：必跑的 CI 跑的是 main 上的 `ci.yml`，改 `ci.yml` 的 PR 只由旧版检查；`ci.yml` 又执行 PR 里的 `.github/` 脚本，改脚本的 PR 由它自己改过的脚本检查；夹带的别的改动也就没经过新版检查。
