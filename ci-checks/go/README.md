# Go 模板

[`ci-checks`](../README.md) 语言级各工具的 Go 配置，由 reusable workflow [`go.yml`](../../.github/workflows/go.yml) 从 repo-guards 读取，不用复制；接入见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)。各节写明它由哪一步跑；顺序同主表。各步在哪个 job 见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)下的 job 表。

Go 的版本固定在 [`.go-version`](.go-version)（1.27.4），`go.yml` 按它装 Go，各 job 设 `GOTOOLCHAIN=local`：不按项目 `go.mod` 的 `go`、`toolchain` 行换版本。下面的工具都由这个版本编译，读不懂更新的语言版本，所以 `go.mod` 要更新 Go 的项目，等 `.go-version` 升级。`go.mod` 的 `ignore` 指令（Go 1.25 起）让 `./...` 跳过目录，`go.yml` 各步就都不查那里，所以算行内豁免，`go.mod` 进 CODEOWNERS（见 [CODEOWNERS](../README.md#codeowners)）。各 job 还设 `GOWORK=off`（不读 PR 的 `go.work`）和 `GOENV=off`（不读 runner 上的用户 Go 配置），模块缓存、编译缓存和 golangci-lint 的缓存都放在 `$RUNNER_TEMP`。

工具由 Install Go tools 步用 `go install <包>@<版本>` 装，模块的 hash 由 Go 的 checksum 数据库校验：golangci-lint v2.15.0、govulncheck v1.9.0（`golang.org/x/vuln`）、deadcode（`golang.org/x/tools` v0.52.0）；Coverage job 另装 gocover-cobertura v1.5.0。本目录的两个检查器 [`comment-budget/`](comment-budget/)、[`license-check/`](license-check/) 各有自己的 `go.mod` 和 `go.sum`，同一步用 `go build` 编译。

## formatter（gofmt）

| 文件 | 作用 |
|---|---|
| [`.go-version`](.go-version) | 固定 Go 的版本，gofmt 随 Go 一起装。gofmt 没有格式选项 |

`go.yml` 的 Format 步对 `go list -e ./...` 找到的包里的全部 `.go` 文件（含测试文件和带 build tag 的文件；同 `go fmt ./...`，不看 `vendor/`、`testdata/`）跑 `gofmt -l`，只检查不改写，PR 改过的文件格式不对就失败（见[只查改动行](../README.md#只查改动行)）；语法错误算工具出错。gofmt 没有行内豁免。

## linter（golangci-lint）

| 文件 | 作用 |
|---|---|
| [`golangci.toml`](golangci.toml) | `linters.default = "none"`，只开列出的 linter，对应 [linter 规则](../README.md#linter-规则)的 Go 一列；`unused` 不在这里开（由 Dead code 步开，见下文），`govet` 也不开（Type check 步跑 `go vet`） |

- 门槛：其他语言用各工具的默认值；Go 的 `funlen`（100 行、50 条语句）、`revive` 的 `argument-limit`（5）和 `max-control-nesting`（5）、`gocyclo`（20）、`file-length-limit`（300 行）取计法相同的那个工具的默认值，没有就取中位数。
- 只管测试的规则（不 sleep、不读真实时间、不 `recover`、不设随机种子和样例数）只查 `_test.go`；只管发布代码的（不 `panic`、不留调试输出、不查 `testing.Testing`、`forcetypeassert`、不 import mock 库）不查 `_test.go`。
- `gosec` 只开 `G115`，`nosec: true`：`#nosec` 注释不算豁免。
- `exclusions.generated: strict`：只有带标准 `// Code generated ... DO NOT EDIT.` 头的文件跳过。
- `issues` 不限条数、同一行的问题不合并，`run.relative-path-mode: wd` 让路径相对项目目录，输出只有 JSON：都是为了让[只查改动行](../README.md#只查改动行)的脚本读全。

`go.yml` 的 Lint 步跑 `golangci-lint run`（JSON 输出），只有 PR 改动行上的问题失败；编译不过（`typecheck`）在哪都失败。`//nolint`（含写在 `package` 前、整个文件不查的 `//nolint:all`）、`// Code generated ... DO NOT EDIT.` 头和让文件不参与默认构建的 `//go:build` 算行内豁免。

## 注释预算

[`comment-budget/`](comment-budget/) 是 [`ci-checks`](../README.md#语言级) 第 3 项"注释预算"的 Go 实现：一个 Go 小程序，在自己的模块里，`go.mod` 固定 `github.com/BurntSushi/toml` v1.6.0（读配置）。

| 项 | 内容 |
|---|---|
| 量什么 | 每个改动的 `.go` 文件：注释字符数对比代码字符数，空白不算。改动文件 = 和 `--base` 的 merge-base 比 diff，删掉的不算 |
| 注释 | `//` 和 `/* */`，用 `go/scanner` 找，字符串和 rune 字面量里的不算。指令注释 `//go:`、`//line `、`//export `、`//extern ` 算代码。词法分析出错的文件算出错 |
| 测试 | 路径匹配 `test_paths` 的文件，整个文件算测试 |
| 上限 | 每个文件：源码代码字符的 `source_percent`% 加测试代码字符的 `test_percent`%，再加 `allowance_chars` 个字符的固定额度。超了就失败 |
| 参数 | `--base`（默认 `origin/main`）、`--config`（配置文件路径，相对当前目录；默认 repo 根目录下的 `.github/checks/go/comment-budget.toml`）、`--all`（查全部已跟踪文件，不看 diff） |
| 输出 | 超标文件逐个列出；最后给源码和测试各自的总比例。退出码：0 通过，1 超标，2 出错（含配置文件缺失、未知键、缺键、类型不对） |

设置全在配置文件里，每个键都必须写。模板 [`comment-budget.toml`](comment-budget.toml) 是默认值：

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `source_percent` | 整数 | 10 | 源码注释上限，占源码代码字符的百分比 |
| `test_percent` | 整数 | 5 | 测试注释上限，占测试代码字符的百分比 |
| `allowance_chars` | 整数 | 300 | 每个文件的固定额度（字符） |
| `test_paths` | 字符串数组 | `["**/*_test.go", "**/testdata/**"]` | 整个文件算测试的路径 glob，相对 repo 根目录。`**` 跨目录，`*` 不跨 |

格式是 TOML，只认这四个顶层键；整数不能为负，`1.0` 这样的浮点数也不行。

`go.yml` 的 Comment budget 步用本目录的检查器和配置，对 PR 的 base 跑。超标时用删注释来过，不要靠加代码压低比例。改程序后在该目录跑 `go test ./...`。

## 类型检查（go vet）

没有模板：类型检查就是编译器本身，加 `go vet` 的全部默认分析器，不靠配置。`go.yml` 的 Type check 步跑 `go vet -json ./...`，包代码和测试代码都查：vet 的问题落在 PR 改动行上才失败，编译错误在哪都失败（见[只查改动行](../README.md#只查改动行)）。`go vet` 不查已废弃的 API，由 Lint 步的 `staticcheck`（`SA1019`）查。

`go vet` 没有行内豁免；让文件不参与默认构建的 `//go:build` 算行内豁免。

## 死代码（unused、deadcode）

不用模板。`go.yml` 的 Dead code 步跑两样，问题落在 PR 改动行上才失败：

- golangci-lint 只开 `unused`，加 `--tests=false`：不看测试文件，只有测试调用的也算没用（为什么见[死代码](../README.md#死代码)）。所有模块都跑。
- `deadcode`（不带 `-test`）：从 `main` 包出发走不到的函数，导出的也算。只在 `go list ./...` 找到 `main` 包的模块跑；只有库的模块不查导出的函数，同 Rust 不查 crate 导出的 `pub` 项。

`//nolint:unused`、`// Code generated ... DO NOT EDIT.` 头和 `//go:build` 算行内豁免。

## 依赖检查（go mod tidy、govulncheck）

不用模板，规则见[依赖检查规则](../README.md#依赖检查规则)。

- Unused dependencies 步跑 `go mod tidy -diff`：`go.mod`、`go.sum` 里有没用的或缺了的依赖就失败，打印要改的 diff。
- Vulnerabilities 步跑 `govulncheck ./...`：代码调用到的有漏洞的模块（含 `.go-version` 那个版本的标准库）就失败。
- Dependency allowlist 步比 build list：在 PR 的项目目录和 merge-base 的副本（`git worktree`）里各跑一遍 `go list -mod=readonly -m -json all`，交给 [`dependency_allowlist.py`](../dependency-allowlist/dependency_allowlist.py) 的 `go` 格式，规则见[依赖白名单](../README.md#依赖白名单)。build list 是 Go 自己解析出的全部模块，`go.mod` 的 `go` 行低于 1.17 时也含间接依赖；读模块缓存，不读 `vendor/`。

这几步没有行内豁免。

## 许可证（license-check）

[`license-check/`](license-check/) 判定 build list 里每个模块的许可证是否被 [`licenses.toml`](../licenses.toml) 的 `allow` 满足：一个 Go 小程序，在自己的模块里，`go.mod` 固定 `github.com/google/licenseclassifier/v2` v2.0.0（go-licenses v2.0.1 用的分类器）和 `github.com/BurntSushi/toml` v1.6.0。

| 项 | 内容 |
|---|---|
| 输入 | stdin：`go list -mod=readonly -m -json all` 的输出。主模块不算；`replace` 成别的模块的，按替换后的路径和版本算；`replace` 成本地目录的算项目自己的，不查 |
| 判定 | 每个模块用 `go mod download -json` 取模块缓存里校验过 hash 的源码，只看模块根目录下文件名以 `LICENSE`、`LICENCE`、`COPYING` 开头的文件（不分大小写；`NOTICE` 不看），用 licenseclassifier 认许可证。至少认出一个，且认出的每个都在 `allow` 里（不分大小写，空白归一）才过 |
| 例外 | [`licenses.toml`](../licenses.toml) 的 `exceptions`（默认空，项目在覆盖文件的 `["ci-checks/licenses.toml"]` 节写，见[覆盖默认配置](../README.md#覆盖默认配置)），每条一个模块路径，按替换后的路径完全相同才跳过：路径只是以例外开头的模块照样查，例外写被 `replace` 掉的旧路径也不跳过 |
| 参数 | `license-check LICENSES_TOML EXCEPTIONS_FILE`；例外文件每行一个模块路径，可以不存在。`go.yml` 用 `config_overrides.py get` 把 `exceptions` 写成这个文件 |
| 输出 | 不过的模块逐个列出（`<路径>@<版本>: <认出的许可证或 no license found>`），最后给个数。退出码：0 通过，1 有模块不过，2 出错（输入读不懂、下载失败、配置不对） |
| 测试 | 在该目录跑 `go test ./...` |

`go.yml` 的 Licenses 步把 build list 交给本程序。查的是 build list，不是某个平台的 import 图，所以只在某个平台或 build tag 下用到的依赖也查；不读 `vendor/` 里的许可证文件。没有行内豁免，例外只写在 `licenses.toml` 的 `exceptions` 里。

## 覆盖率（go test）

| 文件 | 作用 |
|---|---|
| [`coverage.toml`](coverage.toml) | `ignore-filename-regex`：排除 `*_test.go`、`testdata/`，以及生成文件 `generated/`、`*.pb.go`、`*.pb.gw.go`（同 Python 的 `*_pb2.py`、TypeScript 的 `generated/`）。只拿文件在模块里的路径去比，模块路径本身不算。`go.yml` 用它删掉覆盖率数据里匹配的文件 |

`go.yml` 的 Coverage 和 Coverage gate 两步跑覆盖率（判定规则见[覆盖率](../README.md#覆盖率)）：Coverage 在 PR 上跑 `go test -vet=off -coverpkg=./... -coverprofile`，加上 `unit-tests` 选的参数；[`coverage-profile/coverage_profile.py`](coverage-profile/coverage_profile.py)（只用标准库）把各测试程序（分片时各片）的 profile 里同一块代码合并成一条（`set` 模式取有没有执行，计数模式相加），删掉匹配 `ignore-filename-regex` 的文件（测试：在该目录跑 `python3 -m unittest coverage_profile_test`），用 gocover-cobertura 转成 Cobertura 报告。不用 `-test.gocoverdir`：没有测试程序链接到的包（如只有 `main` 的包）不会进它的数据，分母就漏了。`-coverpkg=./...` 让模块里每个包都进分母，没有测试的包算 0%，被别的包的测试执行到的代码也算覆盖；`-vet=off`：vet 的问题由 Type check 步管，不让 Coverage 失败。Coverage gate 判定，门槛同其他语言（80%、90%）；`go test` 不量分支，报告里没有分支，总覆盖率和改动行都只算行。之后的 CRAP report 用同一份报告只出报告，见 [CRAP 分数](../README.md#crap-分数)。

覆盖率只量单元测试。`unit-tests` 输入写 `go test` 的参数，空格分开，原样传过去：包模式（默认 `./...`），也可以加 `-run=<正则>` 按测试名选。集成测试放在 build tag（如 `//go:build integration`）后面，不参与默认编译；或者不把它们的包列进 `unit-tests`。分片（见[覆盖率](../README.md#覆盖率)）时每片写自己的包模式，如 `"./a/..."`、`"./b/..."`，也可以用 `-run=<正则>` 把一个包按测试名拆成几片；各片的 profile 由 Coverage job 合并。

单元测试只在 Coverage 步里跑一遍，有测试失败这一步就失败；`go.yml` 不另跑测试。带 `// Output:` 的 `Example` 函数也是测试，在选中的包里一起跑。集成测试由 repo 自己的 job 跑。让文件不参与默认构建的 `//go:build`，和 `-coverpkg=./...` 之外的代码（带自己 `go.mod` 的子目录是另一个模块），都不进分母，算行内豁免。
