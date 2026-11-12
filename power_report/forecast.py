"""forecast: hourly forecast for the next horizon_days per entity."""

import datetime as dt

import psycopg2.extras

from . import db
from .model import EV, daily_forecast, daily_model


def forecast(cfg, site, now=None):
    """Forecast the next horizon_days per hour. With a year of history the house (and EV) use the seasonal model:
    EV and the rest of the house each follow their recent weekday level from home days (away days left out),
    scaled by their own day-of-year index; a normal week at home, absences are not predicted.
    Otherwise, and for every other entity, a recency-weighted hour-of-week profile of the last 8 weeks.
    `now` (aware datetime) defaults to the current time; tests pass a fixed one."""
    clock = now or dt.datetime.now(cfg.tz)
    con = db.connect(cfg)
    cur = con.cursor()
    cur.execute("select entity from entities where site=%s", (site,))
    ents = [r[0] for r in cur.fetchall()]
    now = clock.astimezone(cfg.tz).replace(minute=0, second=0, microsecond=0)
    made = clock.astimezone(dt.timezone.utc)
    hours_ahead = [now + dt.timedelta(hours=i + 1) for i in range(cfg.horizon_days * 24)]
    out = []
    m = daily_model(cur, cfg, site, clock.astimezone(cfg.tz).date())
    seasonal = m["S_rest"] is not None
    if seasonal:
        targets = sorted({t.date() for t in hours_ahead})
        fr = daily_forecast(m["rest_home"], m["S_rest"], m["today"], targets)
        fe = daily_forecast(m["ev_home"], m["S_ev"], m["today"], targets) if m["has_ev"] else {d: 0.0 for d in targets}
        for t in hours_ahead:
            d = t.date()
            r = fr[d] * m["rest_shape"][d.weekday()].get(t.hour, 0)
            e = fe[d] * m["ev_shape"].get(t.hour, 0)
            out.append((site, m["house"], t.astimezone(dt.timezone.utc), r + e, made))
            if m["has_ev"] and t.hour in m["ev_shape"]:
                out.append((site, EV, t.astimezone(dt.timezone.utc), e, made))
    for ent in ents:
        if seasonal and ent in (m["house"], EV):
            continue
        cur.execute("select ts, kwh from hourly where site=%s and entity=%s and ts >= %s order by ts",
                    (site, ent, clock - dt.timedelta(days=56)))
        hist = [(t.astimezone(cfg.tz), k) for t, k in cur.fetchall()]
        if len(hist) < 24 * 7:
            continue
        # recency-weighted profile (half-life 3 weeks) per hour of week
        prof, w = {}, {}
        for t, k in hist:
            age = (now - t).total_seconds() / 86400
            wt = 0.5 ** (age / 21)
            key = (t.weekday(), t.hour)
            prof[key] = prof.get(key, 0) + wt * k
            w[key] = w.get(key, 0) + wt
        prof = {k: prof[k] / w[k] for k in prof}
        for t in hours_ahead:
            v = prof.get((t.weekday(), t.hour))
            if v is not None:
                out.append((site, ent, t.astimezone(dt.timezone.utc), v, made))
    cur.execute("delete from forecast where site=%s and made_at < %s", (site, clock - dt.timedelta(days=14)))
    psycopg2.extras.execute_values(cur, "insert into forecast (site, entity, ts, kwh, made_at) values %s on conflict do nothing", out, page_size=5000)
    con.commit()
    print(f"forecast {site}: {len(out)} rows" + (" (seasonal model for the house)" if seasonal else ""))
