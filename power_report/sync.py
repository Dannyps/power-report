"""sync: copy hourly kWh / mean W from a site's Home Assistant statistics into the database, then spread meter
feed outages and derive EV charging from the night load (sites with an `ev` table)."""

import datetime as dt
import sys

import psycopg2.extras

from . import db
from .exporter import export
from .model import EV, clamp_day

TO_KWH = {"Wh": 0.001, "kWh": 1.0, "MWh": 1000.0}
TO_W = {"W": 1.0, "kW": 1000.0}


def pretty(cfg, site, entity, names, name=None):
    if entity in cfg.sites[site]["labels"]:
        return cfg.sites[site]["labels"][entity]
    if names.get(entity):
        return names[entity]
    if name:
        return name
    s = entity.split(".", 1)[1]
    for suf in ("_total_energy", "_today_energy", "_energy"):
        if s.endswith(suf):
            s = s[: -len(suf)]
    return s.replace("_", " ").strip().capitalize()


def site_entities(cfg, site, hacfg):
    """{entity: dict(kind, parent, rate, price, label)} from the HA energy config and the site config."""
    s = cfg.sites[site]
    names = hacfg["names"]
    grids = [g for g in hacfg["energy"]["energy_sources"] if g.get("type") == "grid" and g.get("stat_energy_from")]
    rows = {}
    if s.get("house"):
        house = s["house"]
        rows[house] = dict(kind="house", parent=None, rate=s.get("house_rate"), price=None)
        for g in grids:
            rows[g["stat_energy_from"]] = dict(kind="tariff", parent=house, rate=None, price=g.get("number_energy_price"))
    else:
        if len(grids) != 1:
            raise RuntimeError(f"{site}: {len(grids)} grid sources and no house meter configured")
        house = grids[0]["stat_energy_from"]
        rows[house] = dict(kind="house", parent=None, rate=grids[0].get("stat_rate"), price=grids[0].get("number_energy_price"))
    for d in hacfg["energy"]["device_consumption"]:
        e = d["stat_consumption"]
        circuit = e in s["circuits"]
        rows[e] = dict(kind="circuit" if circuit else "device", parent=house if circuit else d.get("included_in_stat"),
                       rate=d.get("stat_rate"), price=None, name=d.get("name"))
    for e, r in rows.items():
        r["label"] = pretty(cfg, site, e, names, r.pop("name", None))
    return house, rows


def utc(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc)


def sync(cfg, site):
    s = cfg.sites[site]
    hacfg = export(cfg, site, "config", *[e for e in (s.get("house"), s.get("house_rate")) if e])
    house, rows = site_entities(cfg, site, hacfg)
    units = hacfg["units"]
    con = db.connect(cfg)
    cur = con.cursor()
    wanted, since = {}, {}
    for ent, r in rows.items():
        if ent not in units:
            print(f"{site}: no statistics for {ent}", file=sys.stderr)
            continue
        if units[ent] not in TO_KWH:
            print(f"{site}: {ent} has unit {units[ent]!r}, skipped", file=sys.stderr)
            continue
        cur.execute("""insert into entities (site, entity, label, kind, parent, price) values (%s,%s,%s,%s,%s,%s)
                       on conflict (site, entity) do update set label=excluded.label, kind=excluded.kind,
                       parent=excluded.parent, price=excluded.price""",
                    (site, ent, r["label"], r["kind"], r["parent"], r["price"]))
        cur.execute("select max(ts) from hourly where site=%s and entity=%s", (site, ent))
        last = cur.fetchone()[0]
        # re-read the last 3 days so late-corrected statistics are picked up
        since[ent] = (last - dt.timedelta(days=3)).timestamp() if last else 0
        wanted[ent] = r
    if not wanted:
        raise RuntimeError(f"{site}: nothing to sync")
    rates = {r["rate"] for r in wanted.values() if r["rate"] in units and units[r["rate"]] in TO_W}
    data = export(cfg, site, "stats", min(since.values()) - 3600, *sorted(set(wanted) | rates))
    n = 0
    for ent, r in wanted.items():
        k = TO_KWH[units[ent]]
        w = TO_W[units[r["rate"]]] if r["rate"] in rates else None
        power = {ts: mean for ts, _, mean in data.get(r["rate"], [])} if w else {}
        prev = None
        out = []
        for ts, total, _ in data.get(ent, []):
            if ts < since[ent] - 3600:
                continue
            if prev is not None and total is not None and prev[1] is not None and ts >= since[ent]:
                kwh = max(total - prev[1], 0.0) * k
                if ts - prev[0] <= 3600:
                    m = power.get(ts)
                    out.append((site, ent, utc(ts), kwh, m * w if m is not None else None, False))
                else:
                    # HA kept no statistics for a while (HA down): spread the delta over the missing hours
                    hours = round((ts - prev[0]) / 3600)
                    out += [(site, ent, utc(prev[0] + 3600 * (i + 1)), kwh / hours, None, True) for i in range(hours)]
            prev = (ts, total)
        # rows already estimated (spread over a gap) are kept as they are
        psycopg2.extras.execute_values(cur, """insert into hourly (site, entity, ts, kwh, avg_w, estimated) values %s
            on conflict (site, entity, ts) do update set kwh=excluded.kwh, avg_w=excluded.avg_w, estimated=excluded.estimated
            where not hourly.estimated""", out, page_size=5000)
        n += len(out)
    fixed = spread_outages(cur, site, house, [e for e, r in wanted.items() if r["kind"] == "tariff"])
    nev = 0
    if s.get("ev"):
        prices = [r["price"] for r in wanted.values() if r["kind"] == "tariff" and r["price"]] or [rows[house]["price"]]
        nev = derive_ev(cfg, cur, site, house, min(prices), since.get(house, 0))
    con.commit()
    print(f"sync {site}: {n} hourly rows" + (f", {fixed} meter outages spread" if fixed else "") + (f", {nev} EV nights" if nev else ""))
    if climate_entities(s):
        nc = sync_climate(cfg, cur, site)
        con.commit()
        print(f"sync {site}: {nc} climate rows")


# metrics read from a site's optional [climate] table: counters are stored as the change over each hour
COUNTERS = {"heating_hours": "Heating (relay on)", "burner_hours": "Burner on", "gas": "Gas"}
MEASUREMENTS = ("temperature", "humidity")


def climate_entities(s):
    """{entity: (metric, label)} for a site's optional [climate] table (see config.example.toml)."""
    c = s.get("climate") or {}
    out = {c[k]: (k, label) for k, label in COUNTERS.items() if c.get(k)}
    for room in c.get("rooms", []):
        out.update({room[k]: (k, room["label"]) for k in MEASUREMENTS if room.get(k)})
    return out


def climate_rows(site, ents, data, since):
    """Rows (site, entity, ts, metric, label, value) from the exporter's [start_ts, sum, mean] statistics, for hours
    starting at or after since[entity]: counters as the change of HA's running sum over the hour (a gap in the
    statistics puts the whole change on the hour after it), measurements as HA's hourly mean."""
    out = []
    for ent, (metric, label) in ents.items():
        prev = None
        for ts, total, mean in data.get(ent, []):
            if metric in COUNTERS:
                if prev is not None and total is not None and ts >= since[ent]:
                    out.append((site, ent, utc(ts), metric, label, max(total - prev, 0.0)))
                if total is not None:
                    prev = total
            elif mean is not None and ts >= since[ent]:
                out.append((site, ent, utc(ts), metric, label, mean))
    return out


def sync_climate(cfg, cur, site):
    ents = climate_entities(cfg.sites[site])
    since = {}
    for ent in ents:
        cur.execute("select max(ts) from climate_hourly where site=%s and entity=%s", (site, ent))
        last = cur.fetchone()[0]
        since[ent] = (last - dt.timedelta(days=3)).timestamp() if last else 0
    data = export(cfg, site, "stats", min(since.values()) - 3600, *sorted(ents))
    for ent in ents:
        if not data.get(ent):
            print(f"{site}: no statistics for {ent} (climate)", file=sys.stderr)
    out = climate_rows(site, ents, data, since)
    psycopg2.extras.execute_values(cur, """insert into climate_hourly (site, entity, ts, metric, label, value) values %s
        on conflict (site, entity, ts) do update set metric=excluded.metric, label=excluded.label, value=excluded.value""",
        out, page_size=5000)
    return len(out)


def derive_ev(cfg, cur, site, house, price, since):
    """EV charging, read from the house meter: in the night window (first_hour..last_hour, local time) a session
    is a night with an hour of at least min_peak_kwh; its energy is each hour's kWh above that night's lowest hour.
    Nights without a session get 0 kWh rows, nights with estimated (gap-filled) hours get no rows at all."""
    ev = cfg.sites[site]["ev"]
    tz = cfg.tz_name
    first, last = ev["first_hour"], ev["last_hour"]
    cur.execute("""insert into entities (site, entity, label, kind, parent, price) values (%s,%s,%s,'ev',%s,%s)
                   on conflict (site, entity) do update set label=excluded.label, parent=excluded.parent, price=excluded.price""",
                (site, EV, "EV charging (from the night load)", house, price))
    cur.execute("select exists (select 1 from hourly where site=%s and entity=%s)", (site, EV))
    if not cur.fetchone()[0]:
        since = 0  # first run: derive the whole history
    d0 = (dt.datetime.fromtimestamp(since, cfg.tz) - dt.timedelta(days=1)).date()
    cur.execute("""select (ts at time zone %s)::date, extract(hour from ts at time zone %s)::int, ts, kwh, estimated
                   from hourly where site=%s and entity=%s and (ts at time zone %s)::date >= %s
                   and extract(hour from ts at time zone %s) between %s and %s order by ts""",
                (tz, tz, site, house, tz, d0, tz, first, last))
    nights = {}
    for d, h, ts, kwh, est in cur.fetchall():
        nights.setdefault(d, []).append((h, ts, kwh, est))
    out = []
    for d, hrs in nights.items():
        # hours that exist in the window that night (one less on the spring-forward night, one more in autumn)
        u = lambda h: clamp_day(d, cfg.tz).replace(hour=h).astimezone(dt.timezone.utc)  # noqa: E731 (aware datetimes subtract by wall clock otherwise)
        expected = round((u(last) - u(first)).total_seconds() / 3600) + 1
        if len({ts for _, ts, _, _ in hrs}) != expected or any(e for *_, e in hrs):
            continue  # incomplete or gap-filled night: unknown
        base = min(k for _, _, k, _ in hrs)
        session = max(k for _, _, k, _ in hrs) >= ev["min_peak_kwh"]
        out += [(site, EV, ts, (k - base) if session else 0.0) for _, ts, k, _ in hrs]
    cur.execute("delete from hourly where site=%s and entity=%s and (ts at time zone %s)::date >= %s", (site, EV, tz, d0))
    psycopg2.extras.execute_values(cur, "insert into hourly (site, entity, ts, kwh) values %s", out, page_size=5000)
    return len({r[2].astimezone(cfg.tz).date() for r in out if r[3] > 0})  # nights with a session


def spread_outages(cur, site, house, tariffs):
    """Meter feed outages: the house meter reads 0 kWh for 2+ hours, then the next hour carries everything
    that accumulated meanwhile. Spread that hour's kWh evenly over the gap and itself, for the house and for
    the tariff registers (same meter, same outage). Device meters are separate and stay as they are."""
    cur.execute("""with h as (select ts, kwh, estimated,
                                     lag(ts) over (order by ts) as prev_ts, lag(kwh) over (order by ts) as prev_kwh
                              from hourly where site=%s and entity=%s)
                   select ts from h where kwh > 0 and not estimated and prev_kwh = 0 and prev_ts = ts - interval '1 hour'
                   order by ts""", (site, house))
    fixed = 0
    for (end,) in cur.fetchall():
        # walk back over consecutive zero, not yet estimated hours
        cur.execute("""select ts, kwh, estimated from hourly where site=%s and entity=%s and ts < %s order by ts desc limit 10000""", (site, house, end))
        start, expect = end, end - dt.timedelta(hours=1)
        for ts, kwh, est in cur.fetchall():
            if ts != expect or kwh != 0 or est:
                break
            start, expect = ts, ts - dt.timedelta(hours=1)
        hours = round((end - start).total_seconds() / 3600) + 1
        if hours < 3:  # fewer than 2 zero hours before it
            continue
        for ent in [house] + tariffs:
            cur.execute("""update hourly set kwh = s.total / s.n, avg_w = null, estimated = true
                           from (select sum(kwh) total, count(*) n from hourly where site=%s and entity=%s and ts between %s and %s) s
                           where site=%s and entity=%s and ts between %s and %s""",
                        (site, ent, start, end, site, ent, start, end))
        fixed += 1
    return fixed
