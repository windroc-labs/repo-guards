# TypeScript 模板

[`ci-checks`](../README.md) 语言级各工具的 TypeScript 配置，由 reusable workflow [`typescript.yml`](../../../.github/workflows/typescript.yml) 从 repo-guards 读取，不用复制；接入见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)。`typescript.yml` 按 [`package.json`](package.json) 把工具装在 repo 之外，再用 `npm ci` 装 repo 自己的依赖，npm 设置取 [`.npmrc`](.npmrc)（见[依赖检查规则](../README.md#依赖检查规则)）。各节写明它由哪一步跑；顺序同主表。各步在哪个 job 见[每个 repo 自己要做的](../README.md#每个-repo-自己要做的)下的 job 表。

## formatter（prettier）

| 文件 | 作用 |
|---|---|
| [`.prettierrc.json`](.prettierrc.json) | 格式选项 |
| [`package.json`](package.json) | `devDependencies` 固定 prettier 的版本，不带 `^` 或 `~`。不固定的话，升级后同样的代码可能突然不过 |

`typescript.yml` 的 Format 步只检查不改写，PR 改过的文件格式不对就失败（见[只查改动行](../README.md#只查改动行)）。`// prettier-ignore` 算行内豁免。

## 注释预算

[`comment-budget/`](comment-budget/) 是 [`ci-checks`](../README.md#语言级) 第 3 项"注释预算"的 TypeScript 实现：一个 TypeScript 脚本，用 Node 自带的类型剥离直接运行，要 Node 22.18 及以上。依赖在它自己的 `package.json` 里固定版本：`typescript`（6.0.3，用它的语法分析找注释；7.x 的 JS API 还是 unstable）、`smol-toml`（读配置）；测试用 `node:test`。

| 项 | 内容 |
|---|---|
| 量什么 | 每个改动的 `.ts`、`.tsx`、`.mts`、`.cts`、`.js`、`.jsx`、`.mjs`、`.cjs` 文件（`.d.ts`、`.d.mts`、`.d.cts` 不算）：注释字符数对比代码字符数，空白不算。改动文件 = 和 `--base` 的 merge-base 比 diff，删掉的不算 |
| 注释 | `//`、`/* */` 和 JSDoc `/** */`，用 TypeScript 编译器的语法分析找：只扫 token 之间的空白，所以字符串、模板字符串、正则字面量、JSX 文本里的不算；第一行的 `#!` shebang 算代码。有语法错误的文件算出错 |
| 测试 | 路径匹配 `test_paths` 的文件，整个文件算测试 |
| 上限 | 每个文件：源码代码字符的 `source_percent`% 加测试代码字符的 `test_percent`%，再加 `allowance_chars` 个字符的固定额度。超了就失败 |
| 参数 | `--base`（默认 `origin/main`）、`--config`（配置文件路径，相对当前目录；默认 repo 根目录下的 `.github/checks/typescript/comment-budget.toml`）、`--all`（查全部已跟踪文件，不看 diff） |
| 输出 | 超标文件逐个列出；最后给源码和测试各自的总比例。退出码：0 通过，1 超标，2 出错（含配置文件缺失、未知键、缺键、类型不对） |

设置全在配置文件里，每个键都必须写。模板 [`comment-budget.toml`](comment-budget.toml) 是默认值：

| 键 | 类型 | 默认 | 含义 |
|---|---|---|---|
| `source_percent` | 整数 | 10 | 源码注释上限，占源码代码字符的百分比 |
| `test_percent` | 整数 | 5 | 测试注释上限，占测试代码字符的百分比 |
| `allowance_chars` | 整数 | 300 | 每个文件的固定额度（字符） |
| `test_paths` | 字符串数组 | `["**/*.test.ts", "**/*.spec.ts", "**/*.test.tsx", "**/*.spec.tsx", "**/__tests__/**", "**/tests/**"]` | 整个文件算测试的路径 glob，相对 repo 根目录。`**` 跨目录，`*` 不跨 |

格式是 TOML，只认这四个顶层键；整数不能为负，`1.0` 这样的浮点数也不行。

`typescript.yml` 的 Comment budget 步用本目录的检查器和配置，对 PR 的 base 跑。超标时用删注释来过，不要靠加代码压低比例。改脚本后在该目录跑 `npm test` 和 `npx tsc`。

## 类型检查（tsc）

| 文件 | 作用 |
|---|---|
| [`tsconfig.json`](tsconfig.json) | 开 `strict` 和 `noUncheckedIndexedAccess`，对应 [类型检查规则](../README.md#类型检查规则) 的 TypeScript 一列。`typescript.yml` 的 Type check 步把这里的 `compilerOptions` 作为命令行参数加在 repo 自己的 `tsconfig.json` 上，命令行优先 |
| [`package.json`](package.json) | `devDependencies` 同样固定 typescript 的版本 |

`// @ts-expect-error`、`// @ts-ignore`、`// @ts-nocheck` 算行内豁免。

## 死代码（knip）

| 文件 | 作用 |
|---|---|
| [`knip.json`](knip.json) | `entry`、`project` 默认 `["src/index.ts!"]`、`["src/**/*.ts!"]`，相对 `working-directory`；项目在覆盖文件的 `["ci-checks/typescript/knip.json"]` 节改（见[覆盖默认配置](../README.md#覆盖默认配置)）。结尾的 `!` 标成发布代码，供 `--production` 用（见[死代码](../README.md#死代码)） |
| [`package.json`](package.json) | `devDependencies` 固定 knip 的版本 |

`typescript.yml` 的 Dead code and unused dependencies 步跑 `knip --production`，同一步也查没用的依赖（第 7 项）。`@public` JSDoc 标记算行内豁免；`knip.json` 里的 `ignore*` 写在覆盖文件里，由 CODEOWNERS 审，不另登记。

## 覆盖率（vitest v8）

| 文件 | 作用 |
|---|---|
| [`coverage.json`](coverage.json) | `exclude`：测试文件、`.d.ts` 和 `generated/` 目录，不进分母 |

只算发布代码：`include` 取 `knip.json` 的 `project` 里结尾带 `!` 的 glob（去掉 `!`），和死代码用同一份"发布代码"的定义；没被测试 import 的文件也进分母。

repo 用 vitest 跑测试，`devDependencies` 里要有 `vitest` 和同版本的 `@vitest/coverage-v8`，版本由 repo 自己的 lockfile 固定。`typescript.yml` 的 Coverage 和 Coverage gate 两步跑覆盖率（判定规则见[覆盖率](../README.md#覆盖率)）：Coverage 在 PR 上跑 `vitest run`，不选测试文件：repo 自己的 vitest 配置（`test.include`、`test.exclude`）选中的测试就是覆盖率量的测试，所以这份配置只选单元测试，集成测试由 repo 自己的 job 用另一份配置另跑（如 `vitest run --config vitest.integration.config.ts`）。覆盖率设置全用命令行参数给（provider `v8`、Cobertura 报告、`include`、`exclude`），命令行优先于 repo 的 vitest 配置；Coverage gate 判定。之后的 CRAP report 用同一份报告只出报告，见 [CRAP 分数](../README.md#crap-分数)。`/* v8 ignore */` 注释算行内豁免。

## 接口快照生成（api-snapshot）

[`api-snapshot/`](api-snapshot/) 生成 TypeScript 的接口快照条目，用 Node 自带的类型剥离直接运行；做法和限制见[接口快照：TypeScript 生成](../README.md#接口快照typescript-生成)。

## 分层规则（dependency-cruiser）

[`layer-rules/`](layer-rules/) 是[第 2 项分层规则](../../../architecture/README.md#第-2-项分层规则)的 TypeScript 实现：`typescript.yml` 的 Layer rules 步在 `working-directory` 里跑 `node layer-rules.ts`，用 Node 自带的类型剥离直接运行。依赖在它自己的 `package.json` 里固定版本，和项目的依赖分开装：dependency-cruiser 18.7.2、`typescript` 6.0.3（dependency-cruiser 用它解析 TypeScript，只支持到 6.x）。

| 项 | 内容 |
|---|---|
| 配置文件 | `working-directory` 下的 `.dependency-cruiser.json`（可以写 JSON5）、`.js`、`.cjs`、`.mjs`、`.ts`、`.cts` 或 `.mts`，按这个顺序取第一个，和 dependency-cruiser 自己找的顺序一样。规则用 dependency-cruiser 自己的写法（`forbidden`、`allowed`），写哪层可以或不可以 import 哪层。只在这一层目录找，不往上级、下级目录找 |
| 没有配置 | 这七个文件名都没有：打印一行提示，然后通过 |
| 脚本配置 | `.js`、`.ts` 这类配置加载时就会运行。它们的文件名在 CODEOWNERS 里锁住，所以可以运行；它们再 `require` 或 `import` 的文件也得是 `.dependency-cruiser` 开头的文件名，别的文件（包括 npm 包）一律不加载，失败 |
| 查什么 | 对 `working-directory` 整个目录跑一次（`depcruise .`），有违规就失败。`node_modules` 之类的目录由配置的 `options.doNotFollow`、`options.exclude` 排除，不排除就一起查 |
| 已有的违规 | 在配置里豁免（如规则里写 `pathNot`），或者用 dependency-cruiser 的基线文件：`working-directory` 下有 `.dependency-cruiser-known-violations.json`（`depcruise --baseline` 生成）时带上 `--ignore-known`，文件里列出的违规不算 |
| 不执行项目代码 | [`guard.ts`](layer-rules/guard.ts) 预加载进 dependency-cruiser，只加载本目录的模块和 `.dependency-cruiser` 开头的文件：`extends` 指向别的 JavaScript 文件或 npm 包、设了 `options.webpackConfig`，都会失败。`extends` 指向 JSON 文件、`options.tsConfig` 照常读，只解析不运行；`options.babelConfig` 不起作用，本目录没装 babel。不写缓存（`--no-cache`） |
| 锁住 | CODEOWNERS 的 `.dependency-cruiser*` 锁住配置和基线文件。`extends` 指向的 JSON 文件要用 `.dependency-cruiser` 开头的文件名才锁得住：这一步只读 JSON、不运行它，所以不查它的文件名；指向别的文件名，就是把那部分规则交给不经 owner 批准的改动 |
| 退出码 | 0 通过或跳过；1 有违规、配置被 dependency-cruiser 拒绝，或要加载不许加载的文件。dependency-cruiser 自己的退出码是错误条数，`guard.ts` 把非 0 都改成 1，不然 256 条会变成 0 |
| 测试 | 在该目录跑 `npm ci --ignore-scripts`、`npm test` 和 `npx tsc` |

## 读写配置（workflow-config）

[`workflow-config/`](workflow-config/) 是 `typescript.yml` 读写 JSON 用的脚本，`node workflow-config.ts <命令>` 直接跑，不用装依赖（要 Node 22.18 及以上）。

| 命令 | 作用 |
|---|---|
| `tsc-flags TSCONFIG` | 每个 `compilerOptions` 打印两行：`--<键>` 和值；Type check 步用它 |
| `coverage-include KNIP_JSON`、`coverage-exclude COVERAGE_JSON` | 每行打印一个 glob：前者是 `project` 里结尾带 `!` 的（去掉 `!`），后者是 `exclude`；Coverage 步用它们 |
| `replace FILE FROM TO` | 把文件里所有 `FROM` 换成 `TO`；Coverage 步用它换 blob 报告里的检出根目录 |
| 退出码 | 0 成功；2 出错（参数不对、文件缺失、JSON 不对） |
| 测试 | 在该目录跑 `node --test workflow-config.test.ts` |
