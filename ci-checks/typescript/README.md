# TypeScript 模板

[`ci-checks`](../README.md) 语言级各工具的 TypeScript 配置。文件复制到 repo 根目录，`package.json` 里的条目合并进已有的那份；顺序同主表。

## formatter（prettier）

| 文件 | 作用 |
|---|---|
| [`.prettierrc.json`](.prettierrc.json) | 格式选项 |
| [`package.json`](package.json) | `devDependencies` 固定 prettier 的版本，不带 `^` 或 `~`；换成接入时的最新版本号。不固定的话，升级后同样的代码可能突然不过 |

`ci.yml` 里一步（之前用 `npm ci` 装好依赖）：

```yaml
- run: npx prettier --check .
```

只检查不改写，格式不对就失败。`// prettier-ignore` 算行内豁免。

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

`comment-budget/` 整个目录复制到 `.github/checks/typescript/comment-budget/`（`package.json` 不合并，单独一份），`comment-budget.toml` 复制到 `.github/checks/typescript/comment-budget.toml`。配置文件在 `.github/` 下，CODEOWNERS 锁住，改上限要 owner 批准；目录里的 `package.json`、`tsconfig.json` 按文件名也在 CODEOWNERS 里。`ci.yml` 里两步：

```yaml
- run: npm ci --ignore-scripts --prefix .github/checks/typescript/comment-budget
- env:
    BASE_REF: ${{ github.event_name == 'pull_request' && format('origin/{0}', github.base_ref) || github.event.before }}
  run: node .github/checks/typescript/comment-budget/comment-budget.ts --base "$BASE_REF"
```

checkout 要拉到 base 的历史（`fetch-depth: 0`），否则找不到 merge-base。超标时用删注释来过，不要靠加代码压低比例。改脚本后在该目录跑 `npm test` 和 `npx tsc`。
