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
