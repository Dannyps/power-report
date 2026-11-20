#!/usr/bin/env python3
"""Generate the Grafana "Power" dashboard JSON (a provisioned file: regenerate, then restart Grafana). $site picks the home.

  grafana/make-dashboard.py [--out FILE]     default: [grafana] dashboard_out in the power-report config
Time zone, default home and description come from the config ([grafana] default_site, description; timezone).
The datasource must have uid "power-pg" (see grafana/provisioning/).
"""
import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from power_report import config  # noqa: E402

ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
ap.add_argument("--out")
ARGS = ap.parse_args()
CFG = config.load()
TZ = CFG.tz_name
GRAFANA = CFG.grafana
DEFAULT_SITE = GRAFANA.get("default_site") or next(iter(CFG.sites))
DESCRIPTION = GRAFANA.get("description", "Home Assistant hourly energy statistics per home. Forecast: weekly profile, see the weekly report.")

DS = {"type": "postgres", "uid": "power-pg"}
SITE = "site='$site'"
HOUSE = f"(select entity from entities where kind='house' and {SITE})"
H = f"{SITE} and entity={HOUSE}"  # the selected site's house meter
LATEST = f"(select max(made_at) from forecast where {SITE})"
pid = 0


def panel(kind, title, sql, x, y, w, h, fmt="time_series", unit=None, extra=None, options=None, custom=None, decimals=None):
    global pid
    pid += 1
    defaults = {"custom": custom or {}}
    if unit:
        defaults["unit"] = unit
    if decimals is not None:
        defaults["decimals"] = decimals
    p = {"id": pid, "type": kind, "title": title, "datasource": DS, "gridPos": {"x": x, "y": y, "w": w, "h": h},
         "targets": [{"datasource": DS, "refId": "A", "format": fmt, "rawQuery": True, "editorMode": "code", "rawSql": sql}],
         "fieldConfig": {"defaults": defaults, "overrides": []}, "options": options or {}}
    if extra:
        p.update(extra)
    return p


bars = {"drawStyle": "bars", "fillOpacity": 80, "lineWidth": 1, "stacking": {"mode": "none"}, "barAlignment": 0}
stat_opts = {"reduceOptions": {"calcs": ["lastNotNull"], "fields": "", "values": False}, "colorMode": "none", "graphMode": "none"}
panels = []

# --- top stats
panels.append(panel("stat", "House, last 7 days", f"""select now() as time, sum(kwh) as kWh from hourly
  where {H} and ts >= now() - interval '7 days'""", 0, 0, 5, 4, unit="kwatth", options=stat_opts, decimals=1))
panels.append(panel("stat", "Cost, last 7 days", f"""select now() as time, coalesce(
  (select sum(h.kwh * e.price) from hourly h join entities e using (site, entity) where h.{SITE} and e.kind='tariff' and h.ts >= now() - interval '7 days'),
  (select sum(h.kwh * e.price) from hourly h join entities e using (site, entity) where h.{SITE} and e.kind='house' and h.ts >= now() - interval '7 days')) as EUR""",
    5, 0, 5, 4, unit="currencyEUR", options=stat_opts, decimals=2))
panels.append(panel("stat", "This month so far", f"""select now() as time, sum(kwh) as kWh from hourly
  where {H} and ts >= date_trunc('month', now() at time zone '{TZ}') at time zone '{TZ}'""", 10, 0, 5, 4, unit="kwatth", options=stat_opts, decimals=1))
panels.append(panel("stat", "Projected month total", f"""select now() as time,
  (select coalesce(sum(kwh),0) from hourly where {H} and ts >= date_trunc('month', now() at time zone '{TZ}') at time zone '{TZ}')
  + (select coalesce(sum(kwh),0) from forecast where {H} and made_at={LATEST}
       and ts > (select max(ts) from hourly where {H})
       and ts < (date_trunc('month', now() at time zone '{TZ}') + interval '1 month') at time zone '{TZ}') as kWh""",
    15, 0, 5, 4, unit="kwatth", options=stat_opts, decimals=0))
panels.append(panel("stat", "Night standby (median, 14 days)", f"""select now() as time,
  percentile_cont(0.5) within group (order by night_min_w) as W from daily
  where {H} and day >= current_date - 14 and night_min_w is not null""", 20, 0, 4, 4, unit="watt", options=stat_opts, decimals=0))

# --- daily house + forecast
panels.append(panel("timeseries", "Daily house usage, actual and forecast", f"""select day::timestamp as time, kwh as "Actual" , null::float as "Forecast"
  from daily where {H} and $__timeFilter(day::timestamp)
union all
select (ts at time zone '{TZ}')::date::timestamp, null, sum(kwh) from forecast
  where {H} and made_at={LATEST}
  and ts > (select max(ts) from hourly where {H}) and $__timeFilter((ts at time zone '{TZ}')::date::timestamp)
  group by 1 order by 1""", 0, 4, 24, 9, unit="kwatth", custom=bars, decimals=1,
    extra={"fieldConfig": {"defaults": {"unit": "kwatth", "decimals": 1, "custom": bars}, "overrides": [
        {"matcher": {"id": "byName", "options": "Forecast"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
        {"matcher": {"id": "byName", "options": "Actual"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "blue"}}]}]}},
    options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}))

# --- circuits stacked daily
stack = dict(bars, stacking={"mode": "normal"})
panels.append(panel("timeseries", "Daily usage by circuit or tariff", f"""select d.day::timestamp as time, e.label as metric, d.kwh
  from daily d join entities e using (site, entity) where d.{SITE} and e.kind in ('circuit', 'tariff') and $__timeFilter(d.day::timestamp)
union all
select h.day::timestamp, 'Other (lights, hardwired, standby)', h.kwh - coalesce((select sum(c.kwh) from daily c join entities ce using (site, entity) where c.{SITE} and ce.kind='circuit' and c.day=h.day),0)
  from daily h where h.{SITE} and h.entity={HOUSE} and $__timeFilter(h.day::timestamp)
  and exists (select 1 from entities where {SITE} and kind='circuit')
order by 1""", 0, 13, 12, 9, unit="kwatth", custom=stack, decimals=1, options={"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}))

# --- hourly power
panels.append(panel("timeseries", "House power, hourly average (gaps filled evenly shown separately)", f"""select ts as time,
  case when not estimated then kwh * 1000 end as "House (W)", case when estimated then kwh * 1000 end as "Estimated, gap filled (W)" from hourly
  where {H} and $__timeFilter(ts) order by ts""", 12, 13, 12, 9, unit="watt", custom={"drawStyle": "line", "fillOpacity": 15, "lineWidth": 1, "showPoints": "never"}))

# --- hour of day profile
panels.append(panel("barchart", "Average day, last 28 days (kWh per hour)", f"""select lpad(extract(hour from ts at time zone '{TZ}')::int::text,2,'0') || 'h' as hour,
  sum(kwh) / 28 as kWh from hourly where {H} and ts >= now() - interval '28 days' group by 1 order by 1""",
    0, 22, 12, 8, fmt="table", unit="kwatth", decimals=2,
    custom={"fillOpacity": 80, "lineWidth": 1, "gradientMode": "none", "axisPlacement": "auto"},
    options={"xField": "hour", "orientation": "vertical", "barWidth": 0.9, "groupWidth": 0.7, "showValue": "never", "stacking": "none",
             "xTickLabelRotation": 0, "xTickLabelSpacing": 0, "legend": {"displayMode": "hidden", "showLegend": False}, "tooltip": {"mode": "single"}}))

# --- standby trend
panels.append(panel("timeseries", "Night standby load per day (min of 02-06h hourly averages)", f"""select day::timestamp as time, night_min_w as "Standby (W)" from daily
  where {H} and night_min_w is not null and $__timeFilter(day::timestamp) order by day""", 12, 22, 12, 8, unit="watt",
    custom={"drawStyle": "line", "lineWidth": 2, "showPoints": "auto"}))

# --- per device (variable $device, multi-select)
DEV = "e.site = '$site' and e.kind = 'device' and e.label in ($device)"
panels.append(panel("timeseries", "Daily usage by device", f"""select d.day::timestamp as time, e.label as metric, d.kwh
  from daily d join entities e using (site, entity) where {DEV} and $__timeFilter(d.day::timestamp) order by 1""",
    0, 30, 12, 9, unit="kwatth", custom=stack, decimals=2, options={"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["sum"]}, "tooltip": {"mode": "multi"}}))
panels.append(panel("timeseries", "Device power, hourly average", f"""select h.ts as time, e.label as metric, h.kwh * 1000 as w
  from hourly h join entities e using (site, entity) where {DEV} and $__timeFilter(h.ts) order by 1""",
    12, 30, 12, 9, unit="watt", custom={"drawStyle": "line", "fillOpacity": 10, "lineWidth": 1, "showPoints": "never"},
    options={"legend": {"displayMode": "table", "placement": "bottom", "calcs": ["mean", "max"]}, "tooltip": {"mode": "multi"}}))

# --- EV charging (derived from the night load; sites with an `ev` table only), actual and forecast per night
panels.append(panel("timeseries", "EV charging per night (read from the night load), actual and forecast", f"""select d.day::timestamp as time, d.kwh as "EV", null::float as "Forecast"
  from daily d where d.{SITE} and d.entity='derived.ev_charging' and $__timeFilter(d.day::timestamp)
union all
select (ts at time zone '{TZ}')::date::timestamp, null, sum(kwh) from forecast
  where {SITE} and entity='derived.ev_charging' and made_at={LATEST}
  and ts > (select max(ts) from hourly where {H}) and $__timeFilter((ts at time zone '{TZ}')::date::timestamp)
  group by 1 order by 1""", 0, 39, 24, 8, unit="kwatth", custom=bars, decimals=1,
    extra={"fieldConfig": {"defaults": {"unit": "kwatth", "decimals": 1, "custom": bars}, "overrides": [
        {"matcher": {"id": "byName", "options": "Forecast"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "orange"}}]},
        {"matcher": {"id": "byName", "options": "EV"}, "properties": [{"id": "color", "value": {"mode": "fixed", "fixedColor": "green"}}]}]}},
    options={"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["sum"]}, "tooltip": {"mode": "multi"}}))

# --- devices table
panels.append(panel("table", "Circuits, tariffs and devices: last 7 days vs the 7 before", f"""select e.label as "Name", e.kind as "Type",
  round(sum(kwh) filter (where ts >= now() - interval '7 days')::numeric, 1) as "kWh (7d)",
  round(sum(kwh) filter (where ts < now() - interval '7 days')::numeric, 1) as "kWh (prev 7d)",
  round(((sum(kwh) filter (where ts >= now() - interval '7 days')) / nullif(sum(kwh) filter (where ts < now() - interval '7 days'),0) - 1)::numeric * 100) as "Change %"
  from hourly h join entities e using (site, entity) where h.{SITE} and e.kind <> 'house' and ts >= now() - interval '14 days'
  group by e.label, e.kind order by 3 desc nulls last""", 0, 47, 24, 9, fmt="table",
    extra={"fieldConfig": {"defaults": {}, "overrides": [{"matcher": {"id": "byName", "options": "Change %"}, "properties": [
        {"id": "unit", "value": "percent"}, {"id": "custom.cellOptions", "value": {"type": "color-text"}},
        {"id": "thresholds", "value": {"mode": "absolute", "steps": [{"color": "green", "value": None}, {"color": "text", "value": -10}, {"color": "red", "value": 10}]}}]}]}}))

# --- heating and indoor climate (sites with a [climate] table; empty for the others)
legend = {"legend": {"displayMode": "list", "placement": "bottom"}, "tooltip": {"mode": "multi"}}
line = {"drawStyle": "line", "fillOpacity": 0, "lineWidth": 2, "showPoints": "never", "spanNulls": 7200000}
panels.append(panel("timeseries", "Heating hours per day (relay on, burner on)", f"""select (ts at time zone '{TZ}')::date::timestamp as time,
  label as metric, sum(value) as value from climate_hourly where {SITE} and metric in ('heating_hours', 'burner_hours') and $__timeFilter(ts)
  group by 1, 2 order by 1""", 0, 56, 12, 8, unit="h", custom=bars, decimals=1,
    options={"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["sum"]}, "tooltip": {"mode": "multi"}}))
panels.append(panel("timeseries", "Gas per day (from meter readings)", f"""select (ts at time zone '{TZ}')::date::timestamp as time,
  sum(value) as "Gas" from climate_hourly where {SITE} and metric = 'gas' and $__timeFilter(ts) group by 1 order by 1""",
    12, 56, 12, 8, unit="suffix: m³", custom=bars, decimals=2,
    options={"legend": {"displayMode": "list", "placement": "bottom", "calcs": ["sum"]}, "tooltip": {"mode": "multi"}}))
panels.append(panel("timeseries", "Indoor temperature, hourly mean", f"""select ts as time, label as metric, value from climate_hourly
  where {SITE} and metric = 'temperature' and $__timeFilter(ts) order by 1""", 0, 64, 12, 9, unit="celsius", custom=line, decimals=1,
    options=legend))
panels.append(panel("timeseries", "Indoor humidity, hourly mean", f"""select ts as time, label as metric, value from climate_hourly
  where {SITE} and metric = 'humidity' and $__timeFilter(ts) order by 1""", 12, 64, 12, 9, unit="percent", custom=line, decimals=0,
    options=legend))

dash = {"uid": "power-overview", "title": "Power", "tags": ["power"], "timezone": TZ, "schemaVersion": 39, "version": 1,
        "time": {"from": "now-45d", "to": "now+14d"}, "refresh": "", "panels": panels, "templating": {"list": [{"name": "site", "label": "Home", "type": "query", "datasource": DS, "multi": False, "includeAll": False,
        "query": "select distinct site from entities order by 1", "definition": "select distinct site from entities order by 1",
        "refresh": 1, "sort": 1, "current": {"selected": True, "text": DEFAULT_SITE, "value": DEFAULT_SITE}},
        {"name": "device", "label": "Devices", "type": "query", "datasource": DS, "multi": True, "includeAll": True,
        "query": "select label from entities where kind='device' and site='$site' order by label", "definition": "select label from entities where kind='device' and site='$site' order by label",
        "refresh": 2, "sort": 1, "current": {"selected": True, "text": ["All"], "value": ["$__all"]}}]}, "annotations": {"list": []},
        "description": DESCRIPTION}
out = ARGS.out or GRAFANA.get("dashboard_out")
if not out:
    sys.exit("no --out and no [grafana] dashboard_out in the config")
json.dump(dash, open(out, "w"), indent=1)
print("panels:", len(panels))
