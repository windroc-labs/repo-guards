# GitHub 设置

ruleset 管"合并要满足什么条件"，各层的权限设置管"谁能改这些条件"。两样都要设好。

这里是共用、设一次的设置（Enterprise、Organization、账号）；每个 repo 都要做的设置见 [`repo-settings/`](../repo-settings/README.md)。

## 1. 设置分几层

按范围从宽到窄：

| 层 | 范围 | 管什么 | 和其他层的关系 | 共用还是每个 repo |
|---|---|---|---|---|
| Enterprise | 多个 Organization | 管多个 Organization | 只有 Enterprise 套餐有这一层 | 共用，设一次 |
| Organization | Organization 下所有 repo | 成员和角色（Owner/Member、基础权限、团队、Organization 角色）；App 的安装和权限；Actions 策略；token 策略（classic/fine-grained、OAuth App） | 给出上限：比如 Organization 把 `GITHUB_TOKEN` 设成只读，repo 只能更严，不能放宽 | 共用，设一次 |
| 账号（每个人或 agent 自己的） | 这个账号能访问的所有 repo，可以跨 Organization | 2FA、token、授权过的 OAuth App 和 GitHub App | 不在 Organization 的包含关系里。token 的权限越不过账号的角色，所以主要靠角色封顶；Organization 的 PAT 策略只管 PAT，管不到 `gh auth login` 拿到的 OAuth token | 共用，每个账号设一次 |
| Repo | 单个 repo，最窄 | 谁能访问、什么角色（直接加的人、团队）；rulesets；Actions 设置；Environments 和 secrets；CODEOWNERS（repo 里的一个文件） | 实际生效的权限是所有来源叠加（Owner、基础权限、团队、Organization 角色、直接授权），任何一个来源给了的权限都算 | 每个 repo 都要做 |

个人账号下的 repo 没有 Enterprise 和 Organization 这两层，只有账号和 Repo。账号和 Repo 这两层的要求，无论是个人 repo、Organization repo，还是 Enterprise 下的 Organization repo，都一样；Organization 和 Enterprise 只是在上面多加一层上限。

下面除 Repo 层外，每层一张表。"套餐"一列写的是最低要求的套餐：个人账号有 Free、Pro，Organization 有 Free、Team，再往上是 Enterprise Cloud。✅ 表示 Free 就有；⚠️ 表示要付费套餐，后面写要什么套餐。一格里分情况时，每种情况一行。

## 2. Enterprise（共用，设一次；没有 Enterprise 套餐就跳过）

| 设置 | 要求 | 原因 | 去哪里检查 | 套餐 |
|---|---|---|---|---|
| Enterprise owner | 只有 owner 一人 | Enterprise owner 可以把自己加成任何 Organization 的 Owner | Enterprise > People > Administrators | ⚠️ Enterprise Cloud |
| Enterprise 策略 | 和第 3 节 Organization 的要求一致，或者更严 | Enterprise 强制的策略，Organization 改不了 | Enterprise > Policies | ⚠️ Enterprise Cloud |
| Enterprise rulesets | 绕过名单只有 owner | Enterprise 的 ruleset 对它选中的 Organization 和 repo 生效，Organization 和 repo 改不了 | Enterprise > Policies > Code | ⚠️ Enterprise Cloud |
| 必跑的 workflow | 同第 3 节；Enterprise 层也能设，对它选中的 Organization 生效 | 同第 3 节 | Enterprise > Policies > Code：Require workflows to pass | ⚠️ Enterprise Cloud |
| token 策略 | 同第 3 节 | 同第 3 节 | Enterprise > Policies > Personal access tokens | ⚠️ Enterprise Cloud |

## 3. Organization（共用，设一次）

| 设置 | 要求 | 原因 | 去哪里检查 | 套餐 |
|---|---|---|---|---|
| Owner | 只有 owner 一人 | Owner 能修改或删除任何 ruleset | Organization > People：Role 一列 | ✅ 全部 |
| 成员角色 | agent 账号是 Member，没有自定义 Organization 角色 | 自定义角色可以单独给"Edit repository rules"之类的权限 | Organization > People：roles 一列为 0 | ✅ 全部<br>⚠️ 自定义 Organization 角色：Enterprise Cloud |
| 基础权限 | 不能是 Admin | 设成 Admin，所有成员对所有 repo 都是 admin | Organization > Settings > Member privileges > Base permissions | ✅ 全部 |
| 团队 | 没有团队对 repo 有 admin | 团队成员会继承 admin，子团队也会继承 | Organization > Teams | ✅ 全部 |
| Organization 角色 | 没有人有 "All-repository admin"、"CI/CD admin"，或带 "Manage organization ref update rules and rulesets" 的自定义角色 | All-repository admin 一次就给了所有 repo 的 admin；CI/CD admin 能改 Actions 策略、管理 secrets；后者能改 Organization rulesets | Organization > Settings > Organization roles > Role assignments（含 Teams 标签） | ⚠️ 自定义角色：Enterprise Cloud<br>⚠️ 预设角色：文档说法不一，每个套餐都要查 |
| GitHub Apps | 没有 Administration；只给需要的 repo；workflows 写权限可以保留 | 有 Administration 就能改 ruleset；能访问的 repo 越少，出事时影响越小；workflows 写权限的理由见 [repo-settings 第 2 节](../repo-settings/README.md#2-workflows-写权限和-tag) | Organization > Settings > GitHub Apps > 每个 App | ✅ 全部 |
| Actions：`GITHUB_TOKEN` | 默认只读 | PR 里的代码会在 CI 里跑。这只管没写 `permissions:` 的 workflow，写了的可以给自己加写权限（见下文"CI 状态伪造"） | Organization > Settings > Actions > General > Workflow permissions：选 "Read repository contents and packages permissions" | ✅ 全部 |
| Actions：创建和批准 PR | 不允许 | 打开的话，workflow 可以自己批准 PR | 同上：不勾 "Allow GitHub Actions to create and approve pull requests" | ✅ 全部 |
| 必跑的 workflow | 每个 repo 一个 Organization ruleset `require ci <repo>`（模板 `rulesets/org-required-ci.json`）：只选这个 repo，管默认分支和 `integration/**`；唯一的规则是必跑这个 repo 自己的 `.github/workflows/ci.yml`，取 `refs/heads/main` 上的版本，不钉提交；Organization admin 可以绕过。每个 repo 怎么建见 [repo-settings 第 1 节](../repo-settings/README.md#1-设置)"必跑的 CI"一行 | 跑的永远是 main 上的 `ci.yml`，PR 改不了要跑什么，也跳不过（实测见下文"CI 状态伪造"）；必跑的 workflow 只能在 Organization 或 Enterprise 层设，不能在 repo 层设 | Organization > Settings > Repository > Rulesets > require ci <repo> | ⚠️ Team（2026-11-11 在 Team 套餐的 Organization 界面上确认） |
| 2FA | 要求所有成员和外部协作者开 2FA | 账号被盗时多一道门槛 | Organization > Settings > Authentication security | ✅ 全部 |
| token 策略 | 禁止 classic token，fine-grained token 需要 owner 批准 | 管住访问 Organization 资源的 PAT。管不到 `gh auth login` 拿到的 OAuth token：GitHub CLI 是特权 OAuth App，OAuth App 限制也拦不住它 | Organization > Settings > Personal access tokens | ✅ 全部 |

| Ruleset 文件 | 层 | 管什么 |
|---|---|---|
| `rulesets/org-baseline.json` | Organization | org 内所有 repo（新建 repo 自动纳入）共用的分支规则，管默认分支和 `integration/**`：必须走 PR、code owner 批准、只能 squash 合并，禁止 force push 和删除；不含 CI 状态检查（伪造的状态能满足它，见下文） |
| `rulesets/org-required-ci.json` | Organization | 每个 repo 一份的模板：必跑这个 repo 自己的 `ci.yml`（见上表"必跑的 workflow"一行）。`REPO` 和两处 `0` 是占位 |
| `rulesets/org-baseline-tags.json` | Organization | org 内所有 repo（新建 repo 自动纳入）的 tag：可以创建，禁止修改和删除 |

### CI 状态伪造

有写权限的人能让一个 workflow 给自己加 `statuses: write`，给 PR 的提交贴一个伪造的 `CI` 成功状态。提交信息里再写 `[skip ci]`，真正的 CI 就不跑，伪造的成功成了唯一的结果。所以不用 `required_status_checks` 规则要求 `CI` 状态。

现在的堵法是上表"必跑的 workflow"。2026-11-11 实测（私有 repo）：

| 情形 | 结果 |
|---|---|
| main 上的 `ci.yml` | 对 PR 的 merge commit 跑 |
| PR 改了 `ci.yml`，比如削弱它 | 改过的版本从不运行，削弱 `ci.yml` 的 PR 被挡住 |
| 从不是 PR 的分支贴伪造的 `CI` 成功状态 | 满足了旧的状态检查规则，但必跑的 workflow 仍然挡住合并 |
| head 提交写 `[skip ci]` | 什么都没跑，PR 一直被挡住 |
| `ci.yml` 里写了 `on: pull_request` | 没有多跑一次 |

每个 repo 要做的设置见 [repo-settings 第 1 节](../repo-settings/README.md#1-设置)"必跑的 CI"和"CI 文件"两行，限制见那里第 3 节。

## 4. 账号（共用，每个账号设一次）

| 账号 | 要求 | 原因 | 去哪里检查 | 套餐 |
|---|---|---|---|---|
| owner | 开 2FA，保存好恢复码；凭证绝不给 agent | owner 能绕过所有规则 | 账号 Settings > Password and authentication | ✅ 全部 |

其他账号层的设置（token 权限、授权过的 App、外部协作者自己的账号）GitHub 没法强制；真正的限制是第 3 节（Organization）和 [repo-settings](../repo-settings/README.md)（repo 角色）。
