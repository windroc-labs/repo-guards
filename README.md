# repo-guards

防止 worker（人或 AI agent）绕过一个 repo 的约束：接口不多不少、只经接口使用下层、不变量测试不能被改或跳过。实现交给 worker 自由改，靠测试和验收把关；接口和检查本身由 owner 把关。

## 要 guard 一个 repo，需要这六层

前三层由机器强制，后三层靠流程。

| # | 层 | 内容 | 放在哪 |
|---|---|---|---|
| 1 | GitHub 组织设置 | 各身份在 Organization 的权限（Owner、受管成员账号、外部协作者、App）；Actions 策略；必跑的 guard；2FA 和 token 策略。共用，设一次 | 本 repo [`github-settings/`](github-settings/README.md) |
| 2 | Repo 设置 | repo 角色；rulesets（受保护分支、tag）和绕过名单；接入必跑的 guard；CI 文件 `.github/workflows/ci.yml`；CODEOWNERS 只锁检查本身；secrets 放进 Environment。每个 repo 都要做，各项要求见 [`repo-settings/`](repo-settings/README.md) | 本 repo [`repo-settings/`](repo-settings/README.md) |
| 3 | CI 检查 | 32 项必做检查，分结构、测试执行、CI 完整性、定期四组，另有 5 项按项目需要；每项拿代码和锁住的标准答案比对；外加各工具配置必须包含的通用规则。逐项见 [`ci-checks/`](ci-checks/README.md) | 本 repo [`ci-checks/`](ci-checks/README.md)；共享 workflow 待做 |
| 4 | 代码结构 | 每层一个出口；具体实现（比如某款芯片）通过工厂交出接口对象，上层叫不出实现的名字；每个接口一套契约测试，所有实现跑同一套 | 每个项目自己的设计；规范待做 |
| 5 | 隐藏验收测试 | worker 看不到的测试，由 gate 或验收去跑。可见的测试会被特判、重载比较之类骗过 | 项目 repo 之外 |
| 6 | 独立审计 | gate 用另一个模型看 diff，专门找满足字面、违反意图的改动：改测试、特判测试环境、为过规则绕弯的写法 | gate brief；模板待做 |

不做：在 AGENTS.md 或 issue 里写"不准作弊"之类的禁令。对 agent 只能降低频率，不能当约束。

辅助：issue 和 PRD 的规格写清楚。规格越模糊，agent 越容易作弊。

建议的落地顺序：1 → 2 → 3 → 6 → 4 → 5。
