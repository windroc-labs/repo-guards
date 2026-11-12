# repo-guards

防止 worker（人或 AI agent）绕过一个 repo 的约束：接口不多不少、只经接口使用下层、不变量测试不能被改或跳过。实现交给 worker 自由改，靠测试和验收把关；接口和检查本身由 owner 把关。

## 要 guard 一个 repo，需要这六层

前四层由机器强制，后两层靠流程。

| # | 层 | 内容 | 放在哪 |
|---|---|---|---|
| 1 | GitHub 组织设置 | 各身份在 Organization 的权限（Owner、受管成员账号、外部协作者、App）；Actions 策略；Organization rulesets（分支和 tag 的 baseline，每个 repo 必跑自己的 CI）；2FA 和 token 策略。共用，设一次 | 本 repo [`github-settings/`](github-settings/README.md) |
| 2 | Repo 设置 | repo 角色；接入必跑的 CI；CI 文件 `.github/workflows/ci.yml`；CODEOWNERS 锁 `.github/` 和它自己；secrets 放进 Environment。每个 repo 都要做，各项要求见 [`repo-settings/`](repo-settings/README.md) | 本 repo [`repo-settings/`](repo-settings/README.md) |
| 3 | CI 检查 | 不依赖项目设计的检查：语言级（按工具类，列出 Rust、Python、TypeScript、Go 的工具，含 linter 必开规则），流程级（除第 21 项外各 repo 自己实现）；有模板的检查由本 repo 的 reusable workflow 跑；每项拿代码和标准答案比对，标准答案用 CODEOWNERS 锁住。逐项见 [`ci-checks/`](ci-checks/README.md) | 本 repo [`ci-checks/`](ci-checks/README.md) |
| 4 | 代码结构 | 设计规则（分层出口、工厂、契约测试）和照着设计做的架构检查，和第 3 层一起在 CI 里跑。逐项见 [`architecture/`](architecture/README.md) | 本 repo [`architecture/`](architecture/README.md)；设计本身在每个项目里 |
| 5 | 隐藏验收测试 | worker 看不到的测试，由 gate 或验收去跑。可见的测试会被特判、重载比较之类骗过 | 项目 repo 之外 |
| 6 | 独立审计 | gate 用另一个模型看 diff，专门找满足字面、违反意图的改动：改测试、特判测试环境、为过规则绕弯的写法 | gate brief；模板待做 |

不做：在 AGENTS.md 或 issue 里写"不准作弊"之类的禁令。对 agent 只能降低频率，不能当约束。

辅助：issue 和 PRD 的规格写清楚。规格越模糊，agent 越容易作弊。

辅助：push 到 public repo 之前，本地 pre-push hook 先跑 scanner，拦下泄漏。每个 clone 要启用一次，见 [`hooks/`](hooks/README.md)。

## `.github/workflows/` 里的文件

一个文件什么时候跑只看它开头的 `on:`。

- 本 repo 自己跑的：`ci.yml`（本 repo 的 CI，PR 和 push 到 main），`private-names.yml`（私有名字扫描，`pull_request_target` 和 push）。
- 只给别的 repo 用 `uses:` 调用的（只有 `on: workflow_call`）：其余各个文件，见 [`ci-checks/` 的 workflow 表](ci-checks/README.md#每个-repo-自己要做的)。

建议的落地顺序：1 → 2 → 3 → 6 → 4 → 5。
