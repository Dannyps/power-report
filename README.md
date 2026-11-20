# power-report

Weekly email reports and forecasts of household electricity use from **Home Assistant**'s energy statistics,
for one or more homes. It copies HA's hourly statistics into PostgreSQL, forecasts the next weeks, and mails a
plain-text + HTML report per home; Grafana can chart the same tables.

- **sync** (hourly): reads each home's HA energy config and hourly statistics through a small read-only exporter,
  locally or over SSH (a forced-command key), normalises units to kWh, fills HA gaps, spreads smart-meter feed
  outages, and (optionally) derives unmetered EV charging from the night load.
- **forecast** (nightly): recency-weighted hour-of-week profile; once a home has a year of history, a seasonal
  model (weekday level x day-of-year index) for the house and the EV separately, leaving out "away" periods.
- **report** (weekly, or on demand from a web button or a Home Assistant script): last 7 days vs the previous week
  and the 4-week average, cost (flat or per tariff register), circuits and devices, weekday/weekend and hourly
  pattern, night standby load, month-to-date and projection, EV sessions, away detection, season hints.
- **heating and indoor climate** (optional, per home): heating and burner hours, gas from the meter readings, and
  room temperature and humidity (hours above a damp limit), synced into `climate_hourly`, shown as a report section
  and as Grafana panels. Configure `[sites.<name>.climate]` (see `config.example.toml`).

Python 3.11+ stdlib plus `psycopg2`. Your data stays in your database: nothing here talks to any service but your
own Home Assistant and PostgreSQL.

## Layout

```
power_report/        the package: config, db, exporter (client), sync, model, forecast, report, render, cli
bin/power-report     CLI entry point (installed as /usr/local/bin/power-report.py)
exporter/power-export  read-only HA exporter (runs next to each Home Assistant)
config.example.toml  copy to /etc/power-report/config.toml
sql/schema.sql       database schema (+ role setup in its header)
systemd/             sync/forecast/report timers, on-demand path unit, tmpfiles rule
web/power-report.php "email the report now" endpoint (serve it behind a login)
ha/power-report.yaml Home Assistant script that requests a report
grafana/             dashboard generator, its API test, provisioning and compose examples
bin/                 deploy, drift, check, test, secret-scan (+ git hook)
tools/equivalence.py prove a change keeps behaviour, on a copy of the real database
tests/               unit tests (synthetic data only)
```

## Usage

```
power-report.py sync [site ...]
power-report.py forecast [site ...]
power-report.py report [--no-send] [site ...]
power-report.py run [--no-send] [site ...]     # sync, forecast, report; a failing site does not stop the others
```

## Install

1. PostgreSQL: create the roles and database, load `sql/schema.sql` (see its header). Put the `power` role's
   password in `/etc/power-report/pgpass` (mode 600).
2. `cp config.example.toml /etc/power-report/config.toml` (mode 600) and describe your homes.
3. For a remote Home Assistant: a `power-export` user there whose `authorized_keys` line is
   `restrict,from="<this host>",command="/usr/local/bin/power-export" ssh-ed25519 ...`, the matching key here
   (`ssh` in the site config), and `install_ssh` so `bin/deploy` can install the exporter there.
4. `bin/deploy --dry-run`, then `bin/deploy` (as root). It runs the tests, installs, reloads systemd, enables the
   timers and runs `bin/check`.
5. Optional: Grafana (`grafana/`), the web button (`web/`, `[deploy] web`), the HA script (`ha/`).

## Development

| # | Task | Command |
|---|---|---|
| 1 | Unit tests | `bin/test` |
| 2 | What would change / install | `bin/deploy --dry-run` / `bin/deploy` |
| 3 | Undo a deploy | `bin/deploy --rollback [STAMP]` (backups in /var/backups/power-report/) |
| 4 | Hand edits on the host | `bin/drift` |
| 5 | Smoke test (read-only, prints no readings) | `bin/check` |
| 6 | Prove a refactor keeps behaviour | `tools/equivalence.py --old <previous power-report.py or checkout>` |

Rules for this public repo: **never commit real readings, reports, your config or credentials.** Tests use
synthetic data; `tools/equivalence.py` keeps its database copy and outputs outside the repo. The pre-commit hook
(`git config core.hooksPath bin/hooks`) refuses secrets.

## License

MIT, see `LICENSE`.
