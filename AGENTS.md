# AGENTS.md

This repo is public. Everything in it is public: files, every commit in history, commit messages, PR titles, bodies and comments, and Actions logs. Removing something later does not unpublish it.

Before you commit, push or comment, check that none of these appear:

- **Secrets.** Tokens, keys, passwords, private keys, or anything that looks like one. Name a secret by its variable name only.
- **Personal data.** Real names, personal email addresses, phone numbers. Commit as the agent account with its GitHub noreply address.
- **Local machine details.** Home directory paths, host names, runner names, container IDs, hardware.
- **Private resources.** Links to or names of private repos, private documents and their paths, issue tracker IDs or URLs.
- **The live org's attack surface.** Which accounts, apps or tokens hold which permissions, and which settings are not yet applied. Keep audit records of the live org in the private docs repo; this repo holds only reusable guidance and config.

Numeric repository IDs are fine.

If a change needs any of the above, stop and ask the owner.

## If something leaks

Deleting a file or rewriting history does not remove it: PR diffs, forks and caches keep it. Replace the repo instead. Every step marked "owner" needs an org owner.

1. If a secret leaked, revoke and replace it first (owner).
2. Remove the leaked content on `main` through a normal PR.
3. Build a one-commit copy of `main`: `git archive main | tar -x -C <new dir>`, then `git init -b main`, commit everything, and check the files against the list above.
4. Rename this repo to `repo-guards-old` (owner). Org rulesets point to the repo by ID, so the guard keeps working.
5. Create an empty public `repo-guards` (owner) and push the one-commit copy.
6. In the old repo, export each repo ruleset and import it into the new repo (owner). In Settings > General > Features, turn off Wikis and Projects.
7. In the org ruleset `require ci guard`, change the required workflow's repo to the new `repo-guards` (owner). Update `repository_id` in `github-settings/rulesets/org-required-guard.json` through a PR.
8. Open a test PR in a guarded repo and check that `guard` runs and passes. Close it.
9. Delete `repo-guards-old` (owner). Its PRs, comments and Actions logs go with it.
