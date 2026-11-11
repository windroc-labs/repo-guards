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
