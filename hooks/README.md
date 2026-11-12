# pre-push 泄漏扫描

push 到 public repo 之前，在本地拦下泄漏。CI 在 push 之后才跑，那时内容已经公开。

## 启用

git clone 不会启用 repo 里的 hook，每个 clone 要自己开一次。在要保护的 clone 里运行：

```
git config core.hooksPath <repo-guards checkout 的绝对路径>/hooks
```

在 repo-guards 自己的 clone 里是 `git config core.hooksPath hooks`。

- 只写这个 clone 的 `.git/config`，不加 `--global`。
- 设了 `core.hooksPath` 以后，git 不再跑这个 clone 的 `.git/hooks/` 里的 hook。
- hook 用同一个 checkout 的 `scanner/scan.py`，规则固定在这个 checkout 的 commit 上；更新 checkout 就更新规则。
- 需要 `git`、`python3`（3.10 起）和登录过的 `gh`。

## 一次性设置（owner 手动做）

private-name 名单只从环境变量 `REPO_GUARDS_PRIVATE_NAMES` 读，每行一个，内容和 CI 的 `PRIVATE_NAMES` secret 相同。把它放进跑 push 的进程的环境，和其他 run secret 一样注入；不写进镜像，也不进任何 repo。没设置时，push 到 public repo 一律被拒。

## 扫什么

- 只在目标是 public 时扫。只有 GitHub API（`gh api repos/<owner>/<name>`）报 `private` 或 `internal` 的 github.com repo 跳过；不是 github.com、查询失败、`gh` 不可用，都按 public 处理。
- 扫 push 新发布的内容，也就是目标 repo 还没有的 commit：每个新增或改动文件的路径和内容（二进制也扫），commit message，author 和 committer 的名字和 email；annotated tag 的 message 和 tagger；push 的 ref 名。
- 检查项：`scan.py` 的 leak 类检查（private-name、local-path、email），加 junk 路径（`__pycache__`、`node_modules`、`*.pyc`、`*.pyo`）。
- 不查 tracker ID：public repo 里允许出现。
- 不代替 [AGENTS.md](../AGENTS.md) 要求的 push 前检查：hygiene 类检查（em-dash、link）和 PR 标题、正文还是由 `scan.py` 和 CI 查。

## 被拒时

输出只给位置，不给匹配到的文本。文件按 push 内的序号 `file #N` 给出，并注明它是哪个 commit 在 `git diff-tree -r --root -c <commit>` 里的第几项。

| 原因 | 怎么修 |
|---|---|
| 有 hit | 在本地 amend 或 rebase 去掉，再 push。内容还没发布，改本地 commit 就够了 |
| 名单没设置 | 设置 `REPO_GUARDS_PRIVATE_NAMES`（上面的一次性设置）后重新 push，commit 不用改 |
| 扫描失败 | 输出给出异常类型。修好原因前，push 一直被拒 |
