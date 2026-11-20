"""report: build a site's weekly figures from the database and mail them (text + HTML)."""

import datetime as dt
import email.utils
import math
import os
import statistics
import subprocess
from email.message import EmailMessage

from . import db
from .model import AWAY_NIGHTS, SEASON_MIN_DAYS, clamp_day, daily_model, monthly_index
from .render import fmt_pct, html_report, pct, text_report


def daily_series(cur, site, ent, d0, d1):
    """kWh per local day for [d0, d1) (dates); missing days -> None."""
    cur.execute("select day, kwh, hours from daily where site=%s and entity=%s and day>=%s and day<%s", (site, ent, d0, d1))
    got = {d: (k, h) for d, k, h in cur.fetchall()}
    return {d0 + dt.timedelta(days=i): got.get(d0 + dt.timedelta(days=i), (None, 0))[0]
            for i in range((d1 - d0).days)}


def tot(series):
    return sum(v for v in series.values() if v is not None)


def season_text(m, today):
    """One sentence on the time of year: this month, next month, and the high and low months."""
    if m["S_rest"] is None:
        return None
    r = monthly_index(m["S_rest"])
    e = monthly_index(m["S_ev"]) if m["S_ev"] else None

    def f(mo):
        return f"{r[mo] - 1:+.0%}" + (f" / {e[mo] - 1:+.0%}" if e else "")
    name = lambda mo: dt.date(2025, mo, 1).strftime("%B")  # noqa: E731
    nxt = today.month % 12 + 1
    hi, lo = max(r, key=r.get), min(r, key=r.get)
    who = "rest of the house / EV charging" if e else "house"
    return (f"Time of year vs an average day ({who}): {name(today.month)} {f(today.month)}, {name(nxt)} {f(nxt)}; "
            f"highest {name(hi)} {f(hi)}, lowest {name(lo)} {f(lo)}.")


def climate_summary(cur, cfg, site, w2, w1, today):
    """Heating hours, gas and indoor climate for [w1, today) and the week before, from climate_hourly.
    None for a site without a [climate] table."""
    c = cfg.sites[site].get("climate")
    if not c:
        return None
    tz = cfg.tz_name
    limit = float(c.get("humidity_limit", 65))
    cur.execute("""select metric, label, (ts at time zone %s)::date, sum(value), count(*), min(value), max(value),
                          count(*) filter (where value > %s)
                   from climate_hourly where site=%s and (ts at time zone %s)::date >= %s and (ts at time zone %s)::date < %s
                   group by 1, 2, 3""", (tz, limit, site, tz, w2, tz, today))
    rows = cur.fetchall()

    def week(day):
        return "cur" if day >= w1 else "prev"
    counters = {}
    for metric in ("heating_hours", "burner_hours", "gas"):
        if c.get(metric):
            got = [r for r in rows if r[0] == metric]
            counters[metric] = dict(cur=sum(r[3] for r in got if week(r[2]) == "cur"),
                                    prev=sum(r[3] for r in got if week(r[2]) == "prev"),
                                    hours=sum(r[4] for r in got if week(r[2]) == "cur"),
                                    days={w1 + dt.timedelta(days=i): None for i in range((today - w1).days)})
            for r in got:
                if r[2] >= w1:
                    counters[metric]["days"][r[2]] = r[3]
    rooms = []
    for room in c.get("rooms", []):
        out = dict(label=room["label"])
        for metric in ("temperature", "humidity"):
            got = [r for r in rows if r[0] == metric and r[1] == room["label"]]
            for k in ("cur", "prev"):
                g = [r for r in got if week(r[2]) == k]
                n = sum(r[4] for r in g)
                out[f"{metric}_{k}"] = sum(r[3] for r in g) / n if n else None
                if k == "cur":
                    out[f"{metric}_min"] = min((r[5] for r in g), default=None)
                    out[f"{metric}_max"] = max((r[6] for r in g), default=None)
                    out[f"{metric}_hours"] = n
                    if metric == "humidity":
                        out["damp_hours"] = sum(r[7] for r in g)
        rooms.append(out)
    cur.execute("select min(ts) from climate_hourly where site=%s", (site,))
    first = cur.fetchone()[0]
    return dict(counters=counters, rooms=rooms, limit=limit, gas_kwh=c.get("gas_kwh_per_m3"),
                first=first.astimezone(cfg.tz).date() if first else None)


def build(cfg, site, now=None):
    """All figures of a site's report as a dict (see render.py). `now` defaults to the current time."""
    clock = now or dt.datetime.now(cfg.tz)
    tz, TZ = cfg.tz_name, cfg.tz
    con = db.connect(cfg)
    cur = con.cursor()
    cur.execute("select entity, label, kind, parent, price from entities where site=%s", (site,))
    ents = {e: dict(label=l, kind=k, parent=p, price=pr) for e, l, k, p, pr in cur.fetchall()}  # noqa: E741
    house = next(e for e, m in ents.items() if m["kind"] == "house")
    today = clock.astimezone(TZ).date()
    w1 = today - dt.timedelta(days=7)         # last 7 complete days: [w1, today)
    w2 = w1 - dt.timedelta(days=7)
    w5 = w2 - dt.timedelta(days=28)
    rows = []
    for e, m in ents.items():
        s1, s2, s5 = (daily_series(cur, site, e, a, b) for a, b in ((w1, today), (w2, w1), (w5, w2)))
        rows.append(dict(entity=e, **m, cur=tot(s1), prev=tot(s2), avg4=tot(s5) / 4, days=s1,
                         have=sum(v is not None for v in s1.values())))
    H = next(r for r in rows if r["entity"] == house)
    tariffs = sorted([r for r in rows if r["kind"] == "tariff"], key=lambda r: -r["cur"])
    circuits = [r for r in rows if r["kind"] == "circuit"]
    # devices with no use in any of the windows (unplugged, seasonal) are left out
    devices = sorted([r for r in rows if r["kind"] == "device" and (r["cur"] or r["prev"] or r["avg4"])], key=lambda r: -r["cur"])
    other = dict(label="Other (lights, hardwired, standby)", cur=H["cur"] - sum(c["cur"] for c in circuits),
                 prev=H["prev"] - sum(c["prev"] for c in circuits), avg4=H["avg4"] - sum(c["avg4"] for c in circuits))
    E = next((r for r in rows if r["kind"] == "ev"), None)
    if E:
        E["nights"] = sum(1 for v in E["days"].values() if v)
        E["share"] = E["cur"] / H["cur"] * 100 if H["cur"] else None

    # cost: flat house price, or the sum over tariff registers at their own prices
    def cost(key, d0=None, d1=None):
        if not tariffs:
            kwh = H[key] if d0 is None else tot(daily_series(cur, site, house, d0, d1))
            return kwh * H["price"]
        if d0 is None:
            return sum(t[key] * t["price"] for t in tariffs)
        return sum(tot(daily_series(cur, site, t["entity"], d0, d1)) * t["price"] for t in tariffs)
    costs = {k: cost(k) for k in ("cur", "prev", "avg4")}
    p_week = costs["cur"] / H["cur"] if H["cur"] else (H["price"] or 0)
    k28 = tot(daily_series(cur, site, house, today - dt.timedelta(days=28), today))
    p28 = cost(None, today - dt.timedelta(days=28), today) / k28 if k28 and tariffs else (H["price"] or p_week)
    for t in tariffs:
        t["share"] = t["cur"] / H["cur"] * 100 if H["cur"] else None

    # hour-of-day profile and weekday/weekend, last 4 weeks, house
    cur.execute("""select extract(hour from ts at time zone %s)::int h,
                          extract(isodow from ts at time zone %s)::int dow, sum(kwh), count(distinct (ts at time zone %s)::date)
                   from hourly where site=%s and entity=%s and (ts at time zone %s)::date >= %s and (ts at time zone %s)::date < %s
                   group by 1,2""", (tz, tz, tz, site, house, tz, today - dt.timedelta(days=28), tz, today))
    hp = {}
    wk = {"wd": [0, 0], "we": [0, 0]}
    dayset = {}
    for h, dow, k, nd in cur.fetchall():
        hp.setdefault(h, 0)
        hp[h] += k
        g = "we" if dow >= 6 else "wd"
        wk[g][0] += k
        dayset[(dow)] = nd
    nwd = sum(v for d, v in dayset.items() if d < 6)
    nwe = sum(v for d, v in dayset.items() if d >= 6)
    wd_avg = wk["wd"][0] / nwd if nwd else 0
    we_avg = wk["we"][0] / nwe if nwe else 0
    peak_h = max(hp, key=hp.get) if hp else None

    # standby: median over days of the night minimum (W)
    def standby(a, b):
        cur.execute("select night_min_w from daily where site=%s and entity=%s and day>=%s and day<%s and night_min_w is not null", (site, house, a, b))
        v = [r[0] for r in cur.fetchall()]
        return statistics.median(v) if v else None
    sb1, sb2, sb5 = standby(w1, today), standby(w2, w1), standby(w5, w2)

    # same 7 days a year ago (52 weeks back, so weekdays line up), only when complete
    ly = daily_series(cur, site, house, w1 - dt.timedelta(weeks=52), today - dt.timedelta(weeks=52))
    last_year = tot(ly) if all(v is not None for v in ly.values()) else None

    # month so far / projection / last month
    m0 = today.replace(day=1)
    lm1 = (m0 - dt.timedelta(days=1)).replace(day=1)
    nm = (m0 + dt.timedelta(days=32)).replace(day=1)
    cur.execute("select min(ts), max(ts) from hourly where site=%s and entity=%s", (site, house))
    first, newest = cur.fetchone()
    # actual readings up to the newest hour, forecast after it (the latest forecast may be from earlier today)
    cur.execute("select coalesce(sum(kwh),0) from hourly where site=%s and entity=%s and ts >= %s",
                (site, house, clamp_day(m0, TZ).astimezone(dt.timezone.utc)))
    mtd = cur.fetchone()[0]
    lastm = tot(daily_series(cur, site, house, lm1, m0))
    latest = "(select max(made_at) from forecast where site=%s)"
    fsum = f"select coalesce(sum(kwh),0) from forecast where site=%s and entity=%s and ts > %s and ts < %s and made_at={latest}"
    cur.execute(fsum, (site, house, newest, clamp_day(nm, TZ).astimezone(dt.timezone.utc), site))
    proj = mtd + cur.fetchone()[0]
    cur.execute(fsum, (site, house, newest, newest + dt.timedelta(days=7, seconds=1), site))
    next7 = cur.fetchone()[0]
    # band: spread of the daily totals over the last 28 days
    ds = [v for v in daily_series(cur, site, house, today - dt.timedelta(days=28), today).values() if v is not None]
    sd = statistics.pstdev(ds) if len(ds) > 5 else 0
    ndays_left = (nm - today).days
    band = 1.96 * sd * math.sqrt(ndays_left)
    band7 = 1.96 * sd * math.sqrt(7)
    # away days and seasons, from the same daily model as the forecast
    m = daily_model(cur, cfg, site, today)
    away_week = sum(1 for d in m["away"] if w1 <= d < today)
    away_since = away_nights = None
    if m["days"] and m["days"][-1] in m["away"] and m["days"][-1] >= today - dt.timedelta(days=2):
        d = m["days"][-1]
        while d - dt.timedelta(days=1) in m["away"]:
            d -= dt.timedelta(days=1)
        away_since, away_nights = d, (m["days"][-1] - d).days + 1
    if m["S_rest"] is not None:
        years = (today - m["home"][0]).days / 365.25
        forecast_text = ("The forecast assumes a normal week at home: "
                         + ("EV charging and the rest of the house each follow their" if m["has_ev"] else "the house follows its")
                         + " weekday level of the last 8 weeks" + (" (away days left out)" if m["has_ev"] else "")
                         + f", adjusted to the time of year with an index learned from {years:.1f} years of history."
                         + (" Away periods are not predicted, so it runs high while you are away." if m["has_ev"] else ""))
    else:
        forecast_text = ("The forecast repeats the weekly rhythm of the last 8 weeks (recent weeks count more). "
                         f"It learns the seasons once there is a year of history (from {first.astimezone(TZ).date() + dt.timedelta(days=SEASON_MIN_DAYS)}).")
    stale = newest if clock.astimezone(dt.timezone.utc) - newest > dt.timedelta(hours=6) else None
    if tariffs:
        tariff_text = ", ".join(f"{t['label'].split(' (')[0]} EUR {t['price']:.3f}" for t in sorted(tariffs, key=lambda t: t["price"]))
        tariff_text = f"Tariffs per kWh: {tariff_text} (HA's current prices, applied to all history); last 4 weeks averaged EUR {p28:.3f}/kWh."
    else:
        tariff_text = f"Flat tariff EUR {H['price']:.2f}/kWh."
    ev = cfg.sites[site].get("ev") or {}
    return dict(site=site, tz=TZ, today=today, w1=w1, w2=w2, H=H, costs=costs, p_week=p_week, p28=p28, tariffs=tariffs,
                circuits=circuits, devices=devices, other=other, hp=hp, wd=wd_avg, we=we_avg, peak_h=peak_h,
                sb=(sb1, sb2, sb5), last_year=last_year, mtd=mtd, lastm=lastm, lm1=lm1, m0=m0, proj=proj, band=band,
                next7=next7, band7=band7, first=first.astimezone(TZ).date(), stale=stale, ndays_left=ndays_left,
                tariff_text=tariff_text, E=E, away_week=away_week, away_since=away_since, away_nights=away_nights,
                away_min=AWAY_NIGHTS, ev_window=(ev.get("first_hour", 0), ev.get("last_hour", 6)),
                forecast_text=forecast_text, season_text=season_text(m, today),
                climate=climate_summary(cur, cfg, site, w2, w1, today))


def report(cfg, site, send=True, now=None):
    r = build(cfg, site, now)
    msg = EmailMessage()
    msg["From"] = cfg.sender
    msg["To"] = cfg.recipient
    msg["Date"] = email.utils.formatdate(localtime=True)
    msg["Subject"] = (f"Power report {site} {r['w1']:%d %b}-{r['today'] - dt.timedelta(days=1):%d %b}: "
                      f"{r['H']['cur']:.0f} kWh ({fmt_pct(pct(r['H']['cur'], r['H']['prev']))})")
    msg["X-Power-Report"] = "yes"
    msg["X-Power-Site"] = site
    msg.set_content(text_report(r))
    msg.add_alternative(html_report(r), subtype="html")
    if not send:
        print(text_report(r))
        with open(os.path.join(cfg.no_send_dir, f"power-report-{site}.html"), "w") as f:
            f.write(html_report(r))
        return
    subprocess.run([cfg.sendmail, "-t"], input=msg.as_bytes(), timeout=60, check=True)
    print(f"report {site} sent")
