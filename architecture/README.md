# 代码结构

第 4 层：项目自己的架构设计，和照着设计做的检查。项目做完这一层的设计才适用。

## 设计规则

- 每层一个出口。
- 具体实现（比如某款芯片）通过工厂交出接口对象，上层叫不出实现的名字。
- 每个接口一套契约测试，所有实现跑同一套。

## CODEOWNERS

| 项 | 要求 | 防什么 |
|---|---|---|
| CODEOWNERS | 下表每项启用的检查，"标准答案"一列里的文件（接口快照、架构规则配置、契约测试目录等）都进 CODEOWNERS。配置文件用不带 `/` 的文件名模式，任何深度都匹配。实现文件不锁 | 不锁的话，worker 改接口、改分层规则、改契约测试都不用 owner 批准 |

## 检查

和第 3 层的检查一起在 repo 的 `.github/workflows/ci.yml` 里跑。比对方式、"频率"一列和"按项目需要"的含义同[第 3 层](../ci-checks/README.md)。

| # | 检查 | 要求 | 标准答案（锁住的文件） | 防什么 | 频率 |
|---|---|---|---|---|---|
| 1 | 接口快照 | 用公开 API 工具从构建产物生成完整签名（参数类型、返回类型、默认值），和快照逐项比对：多、少、签名变了都失败。不靠数个数 | 接口快照 | 偷偷增删或改对外接口 | 每个 PR |
| 2 | [分层规则](#第-2-项分层规则) | 只经接口使用下层，不越层，没有循环依赖 | 架构规则配置 | 越层调用、绕开接口 | 每个 PR |
| 3 | 分层覆盖全部模块 | 每个源码模块在架构规则里恰好归一层；没归层的失败 | 架构规则配置 | 新模块不归层，分层规则管不到 | 每个 PR |
| 4 | 实现名不越过工厂 | 具体实现的名字只出现在它自己的包、工厂和它自己的测试里；工厂之上不对接口对象做向下转型或按类型分支 | 架构规则配置 | 上层直接用具体实现，绕开接口 | 每个 PR |
| 5 | 副作用只在适配层 | 时钟、随机数、文件系统、网络、子进程、硬件 API 只在指定的适配模块里用 | 架构规则配置 | 副作用散在业务代码里，测试替换不了，结果随环境变 | 每个 PR |
| 6 | 测试边界 | 测试只 import 本单元的出口、下层接口和测试支持代码；只 mock 下层接口，不 mock 被测单元，也不 mock CI 工具 | 架构规则配置 | mock 掉被测代码或 CI 工具，测试测不到真东西 | 每个 PR |
| 7 | 契约测试矩阵 | 静态找出每个接口的全部实现（含 fake 和测试替身），每个都要出现在契约测试的结果里；接口快照里的每个接口都有一套契约测试；只跑契约测试，就要执行到每个接口方法 | 契约测试目录、接口快照 | 新实现不跑契约、接口没有契约、契约漏测方法、改弱契约 | 每个 PR |
| 8 | schema 和迁移兼容（按项目需要） | 新 schema 能读旧数据；迁移能从上一个发布版本升级 | 已发布的 schema 快照 | 破坏已有数据 | 每个 PR |
| 9 | 体积和性能预算（按项目需要） | 包体积、关键路径耗时不超预算 | 预算文件 | 体积和性能悄悄变差 | 每个 PR |
| 10 | 属性契约测试（按项目需要） | 契约测试用随机生成的输入跑，每次用新种子并写进日志 | 契约测试目录 | 只对固定样例特判 | 每个 PR |

### 第 1 项接口快照

由四个语言 workflow（[`ci-checks/`](../ci-checks/README.md#每个-repo-自己要做的)）的 API snapshot job 跑，`ci.yml` 不用改。比对规则和输出见 [接口快照比对](../ci-checks/README.md#接口快照比对)。

- 启用：在 `.github/checks/repo-guards.toml` 的 `[project."<working-directory>"]` 节里给语言设快照路径，如 `api-snapshot.rust = ".github/checks/api-rust.json"`。路径相对 repo 根目录，要放在 `.github/` 下（已由 CODEOWNERS 锁住），每种语言、每个 `working-directory` 各一份；路径的规定见[覆盖默认配置](../ci-checks/README.md#覆盖默认配置)。没设就不启用。改这个设置按第 21 项单独提一个只改 `.github/` 的 PR。
- 第一次：文件还不存在，job 失败，并把完整快照作为 artifact 上传。把它原样放到配置的路径，提交进同一个 PR。
- 改接口时：生成结果和快照对不上，job 失败，job summary 列出多、少、变的条目，并写明 artifact 名字和该放的路径。接口改动是有意的，就从这次运行的 artifact 下载新快照，原样放到配置的路径，和代码在同一个 PR 里提交；快照在 `.github/` 下，owner 审 PR 时逐条看快照的 diff。不是有意的，就改回代码。
- 现状：各语言的生成还没接上，设了路径的语言，job 在 Generate 步退出 2。

### 第 2 项分层规则

由语言 workflow 的 Checks job 里的 Layer rules 步跑，紧跟 Inline exemptions，`ci.yml` 不用改。现在只有 `python.yml` 有这一步。

- 启用：在 `.github/checks/repo-guards.toml` 的 `[project."<working-directory>"]` 节写 `layers`，测试代码写进 `test-paths`（见[覆盖默认配置](../ci-checks/README.md#覆盖默认配置)）。没写 `layers`，或没有本语言的层时，这一步打印一行提示，列出设了 `layers` 的项目键，然后通过。

```toml
[project."."]
test-paths = ["tests/**"]
layers = [
  { name = "api", paths = ["src/api/**"], exit = "src/api" },
  { name = "core", paths = ["src/core/**"], exit = "src/core" },
  { name = "store", paths = ["src/store/**"], exit = "src/store/__init__.py" },
]
```

- 配置：`layers` 从上层到下层排。`paths`、`exit`、`test-paths` 都相对 `working-directory`；glob 里 `**` 跨目录，`*` 不跨。`exit` 是层的出口，一个路径：Python 是模块文件或包目录（指它的 `__init__.py`），包目录下的 Python 文件都要被本层 `paths` 匹配。一层属于它的 `exit` 的语言，每种语言按列出的顺序只取自己的层，所以一个 `layers` 里可以有几种语言各一套。Python 的层 `paths` 要在 `source-dirs` 里。配置无效时退出 2，写明层名和键：一个文件被同一语言的两层匹配；`exit` 不存在、不是 Rust、Python、TypeScript 或 Go 的模块、不被本层 `paths` 匹配、在 `test-paths` 里、不是一个字符串；层名重复；层里有未知键；路径为空、是绝对路径或带 `..`。
- 规则：每次运行只查本 `working-directory` 下本语言的文件，只判起点和终点都在层里、都不是测试代码的 import。第 N 层到第 N+1 层只能 import 它的出口，否则是"绕开出口"；到第 N+2 层及更下层是"越层"；到上层是"向上"；同一层随便 import，但同一层文件之间的 import 环是"循环"。终点在项目里但不属于任何层的计入"未归层"数，不失败；标准库和第三方包不管。
- 什么算 import（Python）：所有 `import`、`from … import` 语句，在函数里、`if TYPE_CHECKING:` 下的也算；`importlib.import_module`、`__import__` 不算。模块按 `source-dirs` 和相对 import 解析到项目里的文件；`from 包 import 名字` 里名字是子模块时算那个子模块，否则算包的 `__init__.py`。一个语句算 1 条，指向几个文件时每个文件各 1 条。只用 `ast` 解析，不 import、不执行项目代码；解释器是 uv 自己装的 Python 3.14，在 `/` 下查找（在检出目录里查找时，uv 会运行项目的 `.venv` 里的 python，连带它的 `.pth` 文件），带 `-I -S` 运行，不读 site-packages。
- 只挡新增：HEAD 和它与 `BASE_REF` 的 merge-base 各算一次，两边都用 HEAD 的配置；两边的文件都从 git 对象读，不读工作区，符号链接和 submodule 不读。绕开出口、越层、向上按（发起文件、目标文件）比条数，HEAD 比 merge-base 多就失败，相等或减少都通过；改名或移动过的文件在 merge-base 一边是 0 条。循环看边：一个环里有 merge-base 没有的边（发起文件、目标文件）才失败，已有的边上再加 import 不算。
- 输出：条数增加的文件对，HEAD 上它的每处引用一条 `::error` 注解，写明规则、起止层名、两边的条数和要减到不超过几条，绕开出口的还写应走的出口。新环里每条新边的每处引用一条"循环"注解，写出从这条边的终点起、按 import 方向的整个环。job summary 列出所有违反层规则的文件对（含条数减少和减到 0 的）、新增的循环边数和未归层 import 数。
- 退出码：0 通过；1 有新增违规；2 配置无效、HEAD 上层里的文件解析不了（写明文件）或跑不了。merge-base 上层里的文件解析不了时只打印提示，那个文件在 merge-base 一边当作没有 import。失败不挡同一 job 后面的步。
- 本地：跑过 `fetch_ci_checks.py` 后，在 repo 根目录跑 `python=$(cd / && uv python install --no-config -q 3.14 && uv python find --no-config --managed-python 3.14)`，再跑 `"$python" -I -S .repo-guards/ci-checks/layer-rules/layer_rules.py python --working-directory <working-directory>`，同 CI 的这一步。`--base` 默认是 `origin/HEAD`；给和 CI 的 `BASE_REF` 同一个 commit 时，输出和退出码同 CI。工作区有未提交的改动时先提示，检查的仍是 HEAD commit。
- 实现：共用部分 [`ci-checks/layer-rules/layer_rules.py`](../ci-checks/layer-rules/layer_rules.py)（只用标准库）读配置、读两边的文件、判定、输出；各语言的扫描器只报每个 import 语句解析到的项目文件，协议写在 Python 扫描器 [`ci-checks/python/layer-rules/imports.py`](../ci-checks/python/layer-rules/imports.py) 的开头。测试：`ci-checks/layer-rules/` 跑 `python3 -m unittest layer_rules_test`，`ci-checks/python/layer-rules/` 跑 `python3 -m unittest imports_test`。
