#!/usr/bin/env python3
"""Prove that this checkout behaves exactly like a previous power-report.py on real data, without touching the
live database. Run as root on the host that has the database and the config.

  tools/equivalence.py --old /usr/local/bin/power-report.py [--db power_refactor_test] [--out /var/tmp/power-equivalence]

1. pg_dump the live database once (snapshot in --out, mode 700; data never goes in the repo).
2. For OLD, then NEW: restore the snapshot into the test database (owned by the app's db user), run
   `sync`, `forecast` and `report --no-send` for every site, then dump entities, hourly and forecast (without
   made_at, the only column that records when it ran) as sorted text.
3. Compare, for each step: stdout, stderr, the HTML reports and the three table dumps. Any difference fails.

The old script is copied into --out with exactly two edits (asserted): its database name and its --no-send HTML
path. NEW runs from this checkout with a copy of the config pointing at the test database. Both runs must happen
inside the same clock hour, away from the minutes when Home Assistant writes its hourly statistics, because sync,
forecast and report depend on the time; the tool refuses to start too close to the hour.
The test database is dropped at the end unless --keep.
"""

import argparse
import datetime as dt
import difflib
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
LIVE_CONFIG = os.environ.get("POWER_REPORT_CONFIG", "/etc/power-report/config.toml")


def sh(cmd, **kw):
    return subprocess.run(cmd, capture_output=True, text=True, **kw)


def pg(*cmd):
    r = sh(["runuser", "-u", "postgres", "--", *cmd], cwd="/tmp")
    if r.returncode:
        sys.exit(f"{' '.join(cmd)} failed: {r.stderr.strip()}")
    return r.stdout


def restore(snapshot, db, owner):
    pg("dropdb", "--if-exists", db)
    pg("createdb", "-O", owner, db)
    with open(snapshot, "rb") as f:     # root reads the private snapshot and hands it over on stdin
        r = subprocess.run(["runuser", "-u", "postgres", "--", "pg_restore", "--no-owner", f"--role={owner}", "-d", db],
                           stdin=f, capture_output=True, text=False, cwd="/tmp")
    if r.returncode:
        sys.exit("pg_restore failed: " + r.stderr.decode(errors="replace").strip())


def dump_tables(db, dest):
    queries = {
        "entities": "select site, entity, label, kind, parent, price from entities order by 1, 2",
        "hourly": "select site, entity, ts, kwh, avg_w, estimated from hourly order by 1, 2, 3",
        "forecast": "select site, entity, ts, kwh from forecast order by 1, 2, 3, 4",
    }
    for name, q in queries.items():
        (dest / f"{name}.tsv").write_text(pg("psql", "-d", db, "-AtF", "\t", "-c", f"set timezone='UTC'; {q}"))


def run_steps(cmd_prefix, env, dest, sites):
    for step in (["sync"], ["forecast"], ["report", "--no-send"]):
        r = sh(cmd_prefix + step + sites, env=env, cwd="/tmp")
        (dest / f"{step[0]}.stdout").write_text(r.stdout)
        (dest / f"{step[0]}.stderr").write_text(r.stderr)
        (dest / f"{step[0]}.exit").write_text(str(r.returncode))


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--old", required=True)
    ap.add_argument("--db", default="power_refactor_test")
    ap.add_argument("--out", default="/var/tmp/power-equivalence")
    ap.add_argument("--keep", action="store_true", help="keep the test database")
    ap.add_argument("--force", action="store_true", help="skip the clock-window check")
    a = ap.parse_args()
    sys.path.insert(0, str(REPO))
    from power_report import config
    cfg = config.load(LIVE_CONFIG)
    live_db, owner = cfg.db.get("name", "power"), cfg.db.get("user", "power")
    if a.db == live_db:
        sys.exit("refusing to use the live database as the test database")
    now = dt.datetime.now()
    if not a.force and not 15 <= now.minute < 40:
        sys.exit(f"it is {now:%H:%M}: run between :15 and :40 so both runs share the hour and HA's hourly statistics")
    hour = now.hour

    out = Path(a.out)
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(mode=0o700, parents=True)
    for side in ("old", "new"):
        (out / side).mkdir()
    snapshot = out / "snapshot.dump"
    with open(snapshot, "wb") as f:
        r = subprocess.run(["runuser", "-u", "postgres", "--", "pg_dump", "-Fc", live_db], stdout=f, cwd="/tmp")
    if r.returncode:
        sys.exit("pg_dump failed")
    sites = list(cfg.sites)

    # OLD: the previous script with only its database name and HTML path changed
    src = Path(a.old).read_text()
    old = src.replace('dbname="power"', f'dbname="{a.db}"')
    old = old.replace('f"/tmp/power-report-{site}.html"', f'f"{out / "old"}/power-report-{{site}}.html"')
    if old.count(f'dbname="{a.db}"') != 1 or src.count('f"/tmp/power-report-{site}.html"') != 1:
        sys.exit("the old script does not have exactly one database name and one HTML path to redirect")
    old_script = out / "old-power-report.py"
    old_script.write_text(old)
    restore(snapshot, a.db, owner)
    run_steps([sys.executable, str(old_script)], os.environ.copy(), out / "old", sites)
    dump_tables(a.db, out / "old")

    # NEW: this checkout with a config copy pointing at the test database
    text = Path(LIVE_CONFIG).read_text()
    new_cfg, n1 = re.subn(r'(?m)^(\[database\][^\[]*?^name\s*=\s*)"[^"]*"', rf'\1"{a.db}"', text)
    new_cfg, n2 = re.subn(r'(?m)^no_send_dir\s*=\s*"[^"]*"', f'no_send_dir = "{out / "new"}"', new_cfg)
    if (n1, n2) != (1, 1):
        sys.exit("could not point the config copy at the test database / output dir")
    (out / "config-new.toml").write_text(new_cfg)
    restore(snapshot, a.db, owner)
    env = dict(os.environ, POWER_REPORT_CONFIG=str(out / "config-new.toml"))
    run_steps([sys.executable, str(REPO / "bin" / "power-report")], env, out / "new", sites)
    dump_tables(a.db, out / "new")

    if dt.datetime.now().hour != hour:
        sys.exit("the runs crossed an hour boundary: results are not comparable, run again")
    files = sorted(p.name for p in (out / "old").iterdir())
    bad = 0
    for name in files:
        o, n = (out / "old" / name).read_text(), (out / "new" / name).read_text() if (out / "new" / name).exists() else None
        if n is None:
            print(f"DIFF  {name}: missing in the new run")
            bad += 1
        elif o != n:
            bad += 1
            diff = list(difflib.unified_diff(o.splitlines(), n.splitlines(), f"old/{name}", f"new/{name}", lineterm="", n=0))
            print(f"DIFF  {name}: {sum(1 for l in diff if l[:1] in '+-') - 2} changed lines, first ones:")
            print("\n".join(diff[2:12]))
        else:
            print(f"same  {name} ({len(o.splitlines())} lines)")
    if not a.keep:
        pg("dropdb", "--if-exists", a.db)
    print(f"{'EQUIVALENT' if not bad else 'DIFFERENT'}: {len(files) - bad} of {len(files)} outputs identical (details in {out})")
    return 1 if bad else 0


if __name__ == "__main__":
    sys.exit(main())
