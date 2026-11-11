# Repo 设置

每个 repo 都要做。Enterprise、Organization 和账号层的设置共用，设一次，见 [`github-settings/`](../github-settings/README.md)；各层的关系和"套餐"一列的写法见那里第 1 节。

## 1. 设置

| 设置 | 要求 | 原因 | 去哪里检查 | 套餐 |
|---|---|---|---|---|
| 协作者 | 只有 owner 是 Admin；agent 账号和外部协作者最高 Write，没有自定义 repo 角色 | Admin 能修改或删除 ruleset | repo > Settings > Collaborators and teams；最终结果用 `gh api --paginate "repos/<owner>/<repo>/collaborators?affiliation=all&permission=admin" --jq '.[].login'` 查，它把 Owner、基础权限、团队、Organization 角色带来的 admin 都算进去 | ✅ 全部<br>⚠️ 自定义 repo 角色：Enterprise Cloud |
| Rulesets | 由 Organization ruleset `baseline` 和 `baseline tags` 覆盖（见 [github-settings](../github-settings/README.md) 第 3 节 ruleset 文件表），repo 里不另设；以前导入的 repo 级 ruleset 删掉。长期分支一律以 `integration/` 开头 | 合并必须满足的条件统一在 Organization 层管，repo admin 改不了 | `gh api repos/<owner>/<repo>/rulesets --jq '.[] \| select(.source_type == "Repository") \| .name'` 没有输出 | ✅ 公开 repo：全部<br>⚠️ 私有 repo：Team |
| 必跑的 CI | 有一个只选这个 repo 的 Organization ruleset `require ci <repo>`（设计见 [github-settings](../github-settings/README.md) 第 3 节"必跑的 workflow"一行）。私有 repo 先把 repo > Settings > Actions > General > Access 设成 "Accessible from repositories in the organization"，否则 ruleset 里选不到它的 workflow。建法：复制 [`org-required-ci.json`](../github-settings/rulesets/org-required-ci.json)，`REPO` 换成 repo 名，两处 `0` 换成 `gh api repos/<org>/<repo> --jq .id` 的结果，再跑 `gh api -X POST orgs/<org>/rulesets --input <file>` | 没有它，CI 状态能伪造，PR 也能改掉自己的 CI（见 [github-settings 的"CI 状态伪造"](../github-settings/README.md#ci-状态伪造)）；限制见第 3 节 | Organization > Settings > Repository > Rulesets > require ci <repo>；`gh api repos/<owner>/<repo>/rules/branches/<默认分支> --jq '.[].type'` 里有 `workflows` | ⚠️ Team |
| CI 文件 | 叫 `.github/workflows/ci.yml`。PR 上由必跑的 CI 触发，`on:` 里不用写 `pull_request`；另由 push 到默认分支触发（见 [ci-checks](../ci-checks/README.md#流程级) 第 22 项）。`runs-on`：私有 repo 用 `[self-hosted, linux]`，runner 必须是一次性的；公开 repo 用 GitHub-hosted runner | `require ci <repo>` 只认这个路径，换名字就没有必跑的 CI；常驻的 self-hosted runner 能被任何 workflow 污染 | repo 里的 `.github/workflows/ci.yml`：`on:` 里有 push 到默认分支，`runs-on` 符合要求；PR 的 Checks 里有它的运行 | ✅ 全部 |
| Ruleset 绕过名单 | 只有 Organization admin，不能有 App、团队或角色 | 名单里的都能跳过检查 | Organization > Settings > Repository > Rulesets：管这个 repo 的每个 ruleset 的 Bypass list | ✅ 公开 repo：全部<br>⚠️ 私有 repo：Team |
| CODEOWNERS | 锁 `.github/`（含 `ci.yml` 和其他 workflows）和 CODEOWNERS 文件本身；每个 owner 对 repo 都有写权限 | 不锁的话，改 CI 文件、其他 workflow 和 CODEOWNERS 都不用 owner 批准；没有写权限的 owner 不生效，那个路径就等于没锁 | repo 里的 `.github/CODEOWNERS`（GitHub 打开这个文件时会标出出错的行）；`gh api repos/<owner>/<repo>/codeowners/errors`；repo > Settings > Collaborators and teams | ✅ 公开 repo：全部<br>⚠️ 私有 repo：Pro 或 Team（强制 code owner 批准） |
| Secrets（GitHub Actions secrets：存在 GitHub 设置里、workflow 运行时用的密钥，比如发布 token、API key；不是 repo 代码里的文件） | 放进 Environment，并把它的 Deployment branches 限制为只有默认分支；repo 级 secrets 为空，也没有 Organization 级 secrets 共享给这个 repo | 同一个 repo 的分支和 PR 会用改过的 workflow 跑，能读到 repo 级和共享过来的 Organization 级 secrets。Pro 和 Team 套餐的私有 repo 不能给 Environment 设必须审批，只能靠限制分支；Free 套餐的私有 repo 建不了 Environment，只能不放 secrets，或者升级套餐 | repo > Settings > Environments；repo > Settings > Secrets and variables > Actions；Organization > Settings > Secrets and variables | ✅ 公开 repo：全部<br>⚠️ 私有 repo：Environment 和限制分支要 Pro 或 Team；Organization secrets 共享过来要 Team |

## 2. workflows 写权限和 tag

GitHub Apps 的 workflows 写权限可以保留的理由：改 workflow 的 PR 合并要 code owner 批准；批准前跑起来，也拿不到只限默认分支使用的 Environment secrets。

tag 任何有写权限的人都能创建，可以打在没合并的提交上，所以不要用推 tag 来触发发布。

## 3. 必跑的 CI 的限制

- PR 改了 `.github/workflows/` 下的文件，检查它的是 main 上旧版的 `ci.yml`，所以这种 PR 不能改别的（见 [ci-checks](../ci-checks/README.md#流程级) 第 20 项）；
- 未测：PR 的目标分支改过（手动或自动，比如叠起来的 PR）、分支被别的 PR 用过、fork 来的 PR；
- `.github/` 以外削弱测试抓不到，要靠[顶层 README](../README.md) 第 5、6 层（隐藏验收测试、独立审计）。
