# pre-push 泄漏扫描

push 到 public repo 之前，在本地拦下泄漏。CI 在 push 之后才跑，那时内容已经公开。hook 只调用 [`scanner/scan.py`](../scanner/scan.py) 的 `--pre-push`，和 CI 用同一个 scanner。

## 启用

git clone 不会启用 repo 里的 hook，每个 public repo 的 clone 要自己开一次。在这个 clone 里运行：

```
git config core.hooksPath <repo-guards checkout 的绝对路径>/hooks
```

在 repo-guards 自己的 clone 里是 `git config core.hooksPath hooks`。

- 只写这个 clone 的 `.git/config`，不加 `--global`。
- 设了 `core.hooksPath` 以后，git 不再跑这个 clone 的 `.git/hooks/` 里的 hook。
- 规则跟着 repo-guards checkout 的 commit 走；更新 checkout 就更新规则。
- 需要 `git` 和 `python3`（3.10 起）。

## 名单

private-name 名单从环境变量 `REPO_GUARDS_PRIVATE_NAMES` 读，每行一个，内容和 CI 的 `PRIVATE_NAMES` secret 相同。把它放进跑 push 的进程的环境；不写进镜像，也不进任何 repo。没设置时，push 一律被拒。

## 扫什么

- 只扫 push 新发布的内容：push 的 commit 里，remote-tracking ref（`refs/remotes/<remote>/*`）和目标 ref 原来的 commit 都没有的那些。remote-tracking ref 过时只会多扫，不会漏扫。
- 每个这样的 commit：message，author 和 committer 的 email，它新增或改动的每个文件的路径和内容（后来又删掉的也扫）。非 UTF-8 文件跳过并计数，和 `scan.py` 扫 tracked 文件一样。
- annotated tag 的 message 和 tagger email；push 的 ref 名。
- 检查项：`scan.py` 的 leak 类检查（private-name、local-path、email）。
- 不查 tracker ID：public repo 里允许出现。
- 每个启用了 hook 的 clone 的每次 push 都扫，不查目标 repo 是否 public。
- 不代替 [AGENTS.md](../AGENTS.md) 要求的 push 前检查：hygiene 类检查（em-dash、link）和 PR 标题、正文还是由 `scan.py` 和 CI 查。CI 的 private-names 检查继续作为后备。

## 被拒时

输出只给位置，不给匹配到的文本。文件里的 hit 写成 `commit <sha> file #k`，k 是这个文件在 `git diff-tree -r --root -c --no-commit-id --name-only <sha>` 输出里的行号。

| 原因 | 怎么修 |
|---|---|
| 有 hit | 在本地 amend 或 rebase 去掉，再 push。内容还没发布，改本地 commit 就够了 |
| 名单不可用 | 设置 `REPO_GUARDS_PRIVATE_NAMES` 后重新 push，commit 不用改 |
| 用法错误（退出码 2） | 输出给出原因，例如 ref 指向的不是 commit。修好前 push 一直被拒 |
