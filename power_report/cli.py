"""power-report: sync Home Assistant hourly energy statistics into PostgreSQL, forecast, weekly email report, per site.

  power-report sync [site ...]                copy hourly kWh / mean W from each site's HA statistics into the database
  power-report forecast [site ...]            hourly forecast for the next horizon_days (seasonal model once a site has a year of history)
  power-report report [--no-send] [site ...]  build one report per site; mail each unless --no-send (then print it and
                                              write <no_send_dir>/power-report-<site>.html)
  power-report run [--no-send] [site ...]     sync, forecast and report every site, carrying on past a failing site or step
                                              (a site whose sync failed is still reported, with its stale-data warning)
No site = every site in the config (/etc/power-report/config.toml, or POWER_REPORT_CONFIG).
A site that fails is reported on stderr and the others still run; the exit status is then 1.

Sites are Home Assistant instances, read through the read-only exporter (exporter/power-export): locally for a
site with `ha`, or as the forced command of an SSH key for a site with `ssh`. Energy units (Wh/kWh) are normalised
to kWh. House total = the single grid source of HA's energy config, or the configured `house` meter when the grid
sources are tariff registers (stored as kind=tariff with their HA prices). A site with an `ev` table gets
`derived.ev_charging` (kind ev) from the house's night load; 3+ nights in a row without charging count as away.
A site with a `climate` table also gets heating hours, gas and room temperature/humidity in climate_hourly and a
"Heating and indoor climate" section in its report.
"""

import sys

from . import config
from .forecast import forecast
from .report import report
from .sync import sync

STEPS = {"sync": lambda cfg, s, send: sync(cfg, s),
         "forecast": lambda cfg, s, send: forecast(cfg, s),
         "report": lambda cfg, s, send: report(cfg, s, send=send)}


def main(argv=None):
    args = list(sys.argv[1:] if argv is None else argv)
    cmd = args.pop(0) if args else ""
    send = "--no-send" not in args
    cfg = config.load()
    sites = [a for a in args if not a.startswith("--")] or list(cfg.sites)
    unknown = [s for s in sites if s not in cfg.sites]
    if cmd not in ("sync", "forecast", "report", "run") or unknown:
        sys.exit(__doc__ + (f"\nunknown site: {', '.join(unknown)}" if unknown else ""))
    failed = False
    for step in (("sync", "forecast", "report") if cmd == "run" else (cmd,)):
        for site in sites:
            try:
                STEPS[step](cfg, site, send)
            except Exception as exc:
                print(f"{step} {site} failed: {exc!r}", file=sys.stderr)
                failed = True
    return 1 if failed else 0
