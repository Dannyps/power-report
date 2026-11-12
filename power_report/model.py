"""The daily model behind the forecast and the report: house per local day split into EV and the rest,
away days, seasonal (day-of-year) indices, weekday levels and hourly shapes. Pure functions except daily_model."""

import datetime as dt
import math
import statistics

EV = "derived.ev_charging"   # entity id of the EV charging derived from the night load
SEASON_MIN_DAYS = 365        # history needed before the forecast uses a seasonal index
AWAY_NIGHTS = 3              # this many nights in a row without an EV session = away (car or everybody)


def clamp_day(d, tz):
    return dt.datetime.combine(d, dt.time(0), tz)


def doy(d):
    return min(d.timetuple().tm_yday, 365)


def season_index(series, before):
    """Day-of-year index (mean 1) from the days < before, or None with less than SEASON_MIN_DAYS of history.
    Each day is divided by the mean of the 365 days around it (clipped at the ends of the data) so a trend
    does not leak in; the ratios are averaged per day of year and smoothed (Gaussian, sigma 10 days, circular)."""
    ds = sorted(d for d in series if d < before)
    if not ds or (before - ds[0]).days < SEASON_MIN_DAYS:
        return None
    pre = [0.0]
    for d in ds:
        pre.append(pre[-1] + series[d])
    ratio = {}
    for i, d in enumerate(ds):
        hi = min(len(ds), max(0, i - 182) + 365)
        lo = max(0, hi - 365)
        m = (pre[hi] - pre[lo]) / (hi - lo)
        if m > 0:
            ratio.setdefault(doy(d), []).append(series[d] / m)
    raw = {k: statistics.mean(v) for k, v in ratio.items()}
    out = {}
    for k in range(1, 366):
        num = den = 0.0
        for j in range(-30, 31):
            kk = (k - 1 + j) % 365 + 1
            if kk in raw:
                w = math.exp(-j * j / 200)
                num, den = num + w * raw[kk], den + w
        out[k] = num / den if den else 1.0
    mean = statistics.mean(out.values())
    return {k: v / mean for k, v in out.items()}


def recent_weights(days, origin, span=56):
    """(day, weight) for the given days in the span before origin, half-life 3 weeks."""
    return [(d, 0.5 ** ((origin - d).days / 21)) for d in days if 0 < (origin - d).days <= span]


def weekday_level(series, origin):
    """Recency-weighted mean per weekday over the 8 weeks before origin; a missing weekday gets the mean of the others."""
    s, w = {}, {}
    for d, wt in recent_weights(series, origin):
        s[d.weekday()] = s.get(d.weekday(), 0) + wt * series[d]
        w[d.weekday()] = w.get(d.weekday(), 0) + wt
    if not w:
        return None
    lv = {k: s[k] / w[k] for k in w}
    fill = statistics.mean(lv.values())
    return {k: lv.get(k, fill) for k in range(7)}


def away_days(days, ev):
    """Days in runs of AWAY_NIGHTS or more consecutive days without EV charging."""
    away, run = set(), []
    for d in days + [None]:
        if d is not None and ev[d] == 0 and (not run or (d - run[-1]).days == 1):
            run.append(d)
            continue
        if len(run) >= AWAY_NIGHTS:
            away.update(run)
        run = [d] if d is not None and ev[d] == 0 else []
    return away


def daily_model(cur, cfg, site, today):
    """The house per local day split into EV and the rest, away days, seasonal indices and hourly shapes.
    Only complete days without gap-filled hours count."""
    tz = cfg.tz_name
    cur.execute("select entity from entities where site=%s and kind='house'", (site,))
    house = cur.fetchone()[0]
    has_ev = bool(cfg.sites[site].get("ev"))
    hourly, est = {}, set()
    cur.execute("""select (ts at time zone %s)::date, extract(hour from ts at time zone %s)::int, kwh, estimated
                   from hourly where site=%s and entity=%s""", (tz, tz, site, house))
    for d, h, k, e in cur.fetchall():
        hourly.setdefault(d, {})[h] = k
        if e:
            est.add(d)
    ev_hourly = {}
    if has_ev:
        cur.execute("""select (ts at time zone %s)::date, extract(hour from ts at time zone %s)::int, kwh
                       from hourly where site=%s and entity=%s""", (tz, tz, site, EV))
        for d, h, k in cur.fetchall():
            ev_hourly.setdefault(d, {})[h] = k
    days = sorted(d for d, hs in hourly.items() if len(hs) >= 23 and d not in est and (not has_ev or d in ev_hourly))
    ev = {d: sum(ev_hourly[d].values()) if has_ev else 0.0 for d in days}
    rest = {d: sum(hourly[d].values()) - ev[d] for d in days}
    away = away_days(days, ev) if has_ev else set()
    home = [d for d in days if d not in away]
    rest_home = {d: rest[d] for d in home}
    ev_home = {d: ev[d] for d in home}
    # hourly shapes from recent home days: rest of the house per weekday and hour, EV per hour
    rs, ws = {}, {}
    for d, wt in recent_weights(home, today):
        for h, k in hourly[d].items():
            key = (d.weekday(), h)
            rs[key] = rs.get(key, 0) + wt * (k - ev_hourly.get(d, {}).get(h, 0))
            ws[key] = ws.get(key, 0) + wt
    rest_shape = {}
    for wd in range(7):
        prof = {h: max(rs[(wd, h)] / ws[(wd, h)], 0) for h in range(24) if (wd, h) in ws}
        tot_ = sum(prof.values())
        rest_shape[wd] = {h: v / tot_ for h, v in prof.items()} if tot_ else {}
    es = {}
    for d, wt in recent_weights(home, today):
        for h, k in ev_hourly.get(d, {}).items():
            es[h] = es.get(h, 0) + wt * k
    ev_shape = {h: v / sum(es.values()) for h, v in es.items()} if sum(es.values()) else {}
    return dict(house=house, has_ev=has_ev, days=days, ev=ev, rest=rest, away=away, home=home, today=today,
                rest_home=rest_home, ev_home=ev_home, S_rest=season_index(rest_home, today),
                S_ev=season_index(ev_home, today) if has_ev else None, rest_shape=rest_shape, ev_shape=ev_shape)


def daily_forecast(series, S, origin, targets):
    """kWh per target day: weekday level of the last 8 weeks, rescaled from the season of those weeks to the target's."""
    lv = weekday_level(series, origin)
    if lv is None:
        return None
    if S is None:
        return {d: lv[d.weekday()] for d in targets}
    rw = recent_weights(series, origin)
    base = sum(wt * S[doy(d)] for d, wt in rw) / sum(wt for _, wt in rw)
    return {d: lv[d.weekday()] * S[doy(d)] / base for d in targets}


def monthly_index(S):
    return {mo: statistics.mean(S[doy(dt.date(2025, mo, 1) + dt.timedelta(days=i))] for i in range(28)) for mo in range(1, 13)}
