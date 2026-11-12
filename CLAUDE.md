# power-report (for agents)

PUBLIC repository. Read `README.md` first.
- Never commit real readings, reports, screenshots of reports, the real config (`/etc/power-report/config.toml`),
  host names, entity ids or credentials. Tests use synthetic data only. The pre-commit hook refuses secrets; never
  `--no-verify`. Before every push, `git diff origin/main` and read it for personal details.
- Site specifics live in the config, not in code. Commits are authored `Daniel Silva <git@dannyps.net>`.
- Workflow: `bin/test`; for any change to sync/forecast/report logic run `tools/equivalence.py --old <installed
  /usr/local/bin/power-report.py or a checkout of the previous commit>` between :15 and :40 (it must say EQUIVALENT,
  or every difference must be intended and explained); then `bin/deploy` (runs the tests and `bin/check`); commit; push.
- The weekly report mails the owner: never run `report` or `run` without `--no-send` while testing.
- The deployment host keeps notes outside this repo; do not copy them in.
