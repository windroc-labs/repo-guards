# AGENTS.md

This repo is public. Everything in it is public: files, every commit in history, commit messages, PR titles, bodies and comments, and Actions logs. Removing something later does not unpublish it.

Before you commit, push or comment, check that none of these appear:

- **Secrets.** Tokens, keys, passwords, private keys, or anything that looks like one. Name a secret by its variable name only.
- **Personal data.** Real names, personal email addresses, phone numbers. Commit as the agent account with its GitHub noreply address.
- **Local machine details.** Home directory paths, host names, runner names, container IDs, hardware.
- **Private resources.** Links to or names of private repos and private documents and their paths.
- **The live org's attack surface.** Which accounts, apps or tokens hold which permissions, and which settings are not yet applied. Keep audit records of the live org in the private docs repo; this repo holds only reusable guidance and config.

Numeric repository IDs are fine.

Before you push, also run `python3 scanner/scan.py --names-file <path to the private-name list>` from the repo root, with the owner's list kept outside the checkout. It checks the tracked files and the commits in `origin/main..HEAD` for private names, local paths, emails, em dashes and broken relative links. It supplements the check above; it does not replace it.

Enable the pre-push hook in your clone with `git config core.hooksPath hooks`. On every push from the clone it runs `scanner/scan.py --pre-push` on what the push publishes and refuses the push when a pushed commit's message, emails or added or changed files carry a leak, or when `REPO_GUARDS_PRIVATE_NAMES` is unset. It does not run the hygiene checks; still run the command above. Details: [hooks/README.md](hooks/README.md).

If a change needs any of the above, stop and ask the owner.

## Where this code runs

`github-dir-pr.yml`, `go.yml`, `inline-exemptions.yml`, `jscpd.yml`, `python.yml`, `rust.yml` and `typescript.yml` are reusable workflows: they and the `ci-checks/` files they check out run in caller repos, not here. `ci.yml` and `private-names.yml` run here.
Each caller's `.github/`, including its `ci.yml` and `.github/checks/`, is locked by its CODEOWNERS, so its inputs are the owner's choice. Run what the caller asks: do not validate, filter or sanitize its inputs, and do not guard against a malicious caller. A bad input fails only the caller's own run.
This trust covers the caller's config, not the worker's code under check: catching that code is what the checks are for.
The leak rules above cover what this repo publishes, not what callers pass in.

## If something leaks

Deleting a file or rewriting history does not remove it: PR diffs, forks and caches keep it. Replace the repo instead. Every step marked "owner" needs an org owner.

1. If a secret leaked, revoke and replace it first (owner).
2. Remove the leaked content on `main` through a normal PR.
3. Build a one-commit copy of `main`: `git archive main | tar -x -C <new dir>`, then `git init -b main`, commit everything, and check the files against the list above.
4. Rename this repo to `repo-guards-old` (owner).
5. Create an empty public `repo-guards` (owner) and push the one-commit copy.
6. In the new repo's Settings > General > Features, turn off Wikis and Projects (owner).
7. Check that the org `baseline` rulesets cover the new repo: `gh api repos/<org>/repo-guards/rules/branches/main` lists their rules.
8. Delete `repo-guards-old` (owner). Its PRs, comments and Actions logs go with it.
