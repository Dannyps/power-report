"""Turn the figures from report.build() into the plain-text and HTML email bodies. No database access."""

import datetime as dt
import html


def pct(a, b):
    return None if not b else (a - b) / b * 100


def fmt_pct(p):
    return "n/a" if p is None else f"{p:+.0f}%"


def text_report(r):
    p28 = r["p28"]
    H, C = r["H"], r["costs"]
    L = []
    L.append(f"Power report ({r['site']}): {r['w1']:%a %d %b} to {r['today'] - dt.timedelta(days=1):%a %d %b %Y}")
    if r["stale"]:
        L.append(f"WARNING: no data since {r['stale'].astimezone(r['tz']):%a %d %b %H:%M}; the figures below are incomplete.")
    L.append("")
    L.append(f"House: {H['cur']:.1f} kWh = EUR {C['cur']:.2f}  ({fmt_pct(pct(H['cur'], H['prev']))} vs previous week {H['prev']:.1f} kWh, "
             f"{fmt_pct(pct(H['cur'], H['avg4']))} vs 4-week average {H['avg4']:.1f} kWh)")
    if r["last_year"] is not None:
        L.append(f"Same 7 days a year ago: {r['last_year']:.1f} kWh ({fmt_pct(pct(H['cur'], r['last_year']))} now).")
    L.append("Daily: " + "  ".join(f"{d:%a} {v:.1f}" for d, v in H["days"].items() if v is not None))
    if r["away_since"]:
        L.append(f"Away? No EV charging for {r['away_nights']} nights in a row, since {r['away_since']:%a %d %b}.")
    elif r["away_week"]:
        L.append(f"{r['away_week']} days this week looked like being away ({r['away_min']}+ nights in a row without EV charging).")
    L.append("")
    E = r["E"]
    if E:
        L.append(f"EV charging (read from the night load): {E['cur']:.1f} kWh on {E['nights']} nights = EUR {E['cur'] * E['price']:.2f} at "
                 f"EUR {E['price']:.3f}/kWh, {E['share'] or 0:.0f}% of the house ({fmt_pct(pct(E['cur'], E['prev']))} vs previous week, "
                 f"{fmt_pct(pct(E['cur'], E['avg4']))} vs 4-week average). Rest of the house: {H['cur'] - E['cur']:.1f} kWh.")
        L.append("")
    if r["tariffs"]:
        L.append("By tariff (kWh, share, EUR, vs previous week):")
        for t in r["tariffs"]:
            L.append(f"  {t['label']:<36}{t['cur']:7.1f}  {t['share'] or 0:4.0f}%  {t['cur'] * t['price']:6.2f}  {fmt_pct(pct(t['cur'], t['prev'])):>6}")
        L.append("")
    if r["circuits"]:
        L.append("Where it went (kWh, vs previous week, vs 4-week avg):")
        for c in r["circuits"] + [r["other"]]:
            L.append(f"  {c['label']:<36}{c['cur']:7.1f}  {fmt_pct(pct(c['cur'], c['prev'])):>6}  {fmt_pct(pct(c['cur'], c['avg4'])):>6}")
        L.append("  Individually metered (already inside the lines above, not additive):")
    elif r["devices"]:
        L.append("Individually metered (part of the house total; kWh, vs previous week, vs 4-week avg):")
    for c in r["devices"]:
        L.append(f"  {c['label']:<36}{c['cur']:7.1f}  {fmt_pct(pct(c['cur'], c['prev'])):>6}  {fmt_pct(pct(c['cur'], c['avg4'])):>6}")
    L.append("")
    L.append("Patterns (last 4 weeks):")
    L.append(f"  Weekday avg {r['wd']:.1f} kWh/day, weekend avg {r['we']:.1f} kWh/day.")
    if r["peak_h"] is not None:
        L.append(f"  Busiest hour of the day: {r['peak_h']:02d}:00-{(r['peak_h'] + 1) % 24:02d}:00.")
    s1, s2, s5 = r["sb"]
    if s1 is not None:
        L.append(f"  Night standby load (02-06h, median of the daily minimum): {s1:.0f} W this week"
                 + (f", {s2:.0f} W previous week" if s2 is not None else "")
                 + (f", {s5:.0f} W 4-week avg" if s5 is not None else "") + f" = about {s1 * 24 / 1000 * 365:.0f} kWh / EUR {s1 * 24 / 1000 * 365 * p28:.0f} a year.")
    L.append("")
    L.append("Forecast:")
    L.append(f"  Next 7 days: {r['next7']:.0f} kWh (+/-{r['band7']:.0f}) = EUR {r['next7'] * p28:.2f}")
    L.append(f"  {r['m0']:%B} so far {r['mtd']:.0f} kWh; projected month total {r['proj']:.0f} kWh (+/-{r['band']:.0f}), EUR {r['proj'] * p28:.2f}, "
             f"{fmt_pct(pct(r['proj'], r['lastm']))} vs {r['lm1']:%B} ({r['lastm']:.0f} kWh).")
    if r["season_text"]:
        L.append(f"  {r['season_text']}")
    L.append(f"  {r['forecast_text']} History since {r['first']}.")
    L.append(f"  {r['tariff_text']}")
    return "\n".join(L)


def html_report(r):
    pw, p28 = r["p_week"], r["p28"]
    H, C = r["H"], r["costs"]
    e = html.escape

    def delta(a, b):
        v = pct(a, b)
        if v is None:
            return "<td class=n>n/a</td>"
        col = "#b3261e" if v > 10 else ("#1b7a3a" if v < -10 else "inherit")
        return f"<td class=n style='color:{col}'>{v:+.0f}%</td>"

    def table(rows, head):
        t = f"<table><tr><th>{head}</th><th class=n>kWh</th><th class=n>EUR</th><th class=n>vs prev week</th><th class=n>vs 4-wk avg</th></tr>"
        for c in rows:
            t += (f"<tr><td>{e(c['label'])}</td><td class=n>{c['cur']:.1f}</td><td class=n>{c['cur'] * pw:.2f}</td>"
                  f"{delta(c['cur'], c['prev'])}{delta(c['cur'], c['avg4'])}</tr>")
        return t + "</table>"

    def tariff_table(rows):
        t = "<table><tr><th>Tariff</th><th class=n>kWh</th><th class=n>share</th><th class=n>EUR/kWh</th><th class=n>EUR</th><th class=n>vs prev week</th></tr>"
        for c in rows:
            t += (f"<tr><td>{e(c['label'])}</td><td class=n>{c['cur']:.1f}</td><td class=n>{c['share'] or 0:.0f}%</td>"
                  f"<td class=n>{c['price']:.3f}</td><td class=n>{c['cur'] * c['price']:.2f}</td>{delta(c['cur'], c['prev'])}</tr>")
        return t + "</table>"

    mx = max([v for v in H["days"].values() if v is not None] or [1])
    bars = "<table class=bars>" + "".join(
        f"<tr><td class=bl>{d:%a %d}</td><td class=bt><div style='width:{(v or 0) / mx * 100:.0f}%'></div></td><td class='n bv'>{(v or 0):.1f}</td></tr>"
        for d, v in H["days"].items()) + "</table>"
    hmx = max(r["hp"].values() or [1]) or 1
    hours = "".join(f"<i title='{h:02d}h: {r['hp'].get(h, 0) / 28:.2f} kWh avg' style='height:{r['hp'].get(h, 0) / hmx * 60 + 2:.0f}px'></i>" for h in range(24))
    s1, s2, s5 = r["sb"]
    sb = ""
    if s1 is not None:
        sb = (f"<p>Night standby load (02-06h): <b>{s1:.0f} W</b> this week"
              + (f", {s2:.0f} W the week before" if s2 is not None else "")
              + (f", {s5:.0f} W 4-week average" if s5 is not None else "")
              + f". Left on all year that is about {s1 * 24 / 1000 * 365:.0f} kWh (EUR {s1 * 24 / 1000 * 365 * p28:.0f}).</p>")
    stale = (f"<p style='color:#b3261e'><b>No data since {r['stale'].astimezone(r['tz']):%a %d %b %H:%M}</b>; the figures below are incomplete.</p>"
             if r["stale"] else "")
    ly = (f"<br>Same 7 days a year ago: {r['last_year']:.1f} kWh ({fmt_pct(pct(H['cur'], r['last_year']))} now)"
          if r["last_year"] is not None else "")
    away = ""
    if r["away_since"]:
        away = f"<p style='color:#8a5a00'><b>Away?</b> No EV charging for {r['away_nights']} nights in a row, since {r['away_since']:%a %d %b}.</p>"
    elif r["away_week"]:
        away = f"<p class=fine>{r['away_week']} days this week looked like being away ({r['away_min']}+ nights in a row without EV charging).</p>"
    parts = []
    E = r["E"]
    if E:
        evbars = "<table class=bars>" + "".join(
            f"<tr><td class=bl>{d:%a %d}</td><td class=bt><div style='width:{(v or 0) / mx * 100:.0f}%;background:#1b7a3a'></div></td><td class='n bv'>{(v or 0):.1f}</td></tr>"
            for d, v in E["days"].items()) + "</table>"
        f0, f1 = r["ev_window"]
        parts.append(f"""<h2>EV charging</h2>
<p><b>{E['cur']:.1f} kWh</b> on {E['nights']} nights, EUR {E['cur'] * E['price']:.2f} at EUR {E['price']:.3f}/kWh, {E['share'] or 0:.0f}% of the house.
{fmt_pct(pct(E['cur'], E['prev']))} vs previous week, {fmt_pct(pct(E['cur'], E['avg4']))} vs 4-week average. Rest of the house: {H['cur'] - E['cur']:.1f} kWh.</p>
{evbars}<div class=fine>Read from the night load ({f0:02d}-{f1:02d}h above that night's lowest hour); same scale as the house bars above.</div>""")
    if r["tariffs"]:
        parts.append(f"<h2>By tariff</h2>{tariff_table(r['tariffs'])}")
    if r["circuits"]:
        parts.append(f"<h2>Where it went</h2>{table(r['circuits'] + [r['other']], 'Circuit')}")
        if r["devices"]:
            parts.append(f"<h2>Individually metered</h2><div class=fine>Already counted in the circuits above, not additive.</div>{table(r['devices'], 'Device')}")
    elif r["devices"]:
        parts.append(f"<h2>Individually metered</h2><div class=fine>Part of the house total.</div>{table(r['devices'], 'Device')}")
    breakdown = "\n".join(parts)
    return f"""<!doctype html><html><head><meta charset=utf-8><style>
body{{font:15px/1.45 system-ui,sans-serif;max-width:640px;margin:0 auto;padding:16px;color:#1b1b1f}}
h1{{font-size:20px;margin:0 0 4px}} h2{{font-size:16px;margin:24px 0 6px}} .big{{font-size:30px;font-weight:650}}
table{{border-collapse:collapse;width:100%}} th,td{{padding:4px 6px;border-bottom:1px solid #ddd;text-align:left}} .n{{text-align:right}}
.bars td{{border:0;padding:2px 6px}} .bl{{width:64px;color:#555;white-space:nowrap}} .bt{{width:100%}} .bt div{{height:14px;background:#2d6cdf;border-radius:3px}} .bv{{width:44px;white-space:nowrap}}
.hours{{display:flex;align-items:flex-end;gap:2px;height:64px}} .hours i{{flex:1;background:#2d6cdf;border-radius:2px 2px 0 0}}
.ax{{display:flex;justify-content:space-between;color:#666;font-size:12px}} .fine{{color:#666;font-size:13px}}
</style></head><body>
<h1>Power report: {e(r['site'])}</h1><div class=fine>{r['w1']:%a %d %b} to {r['today'] - dt.timedelta(days=1):%a %d %b %Y}</div>
{stale}
<p><span class=big>{H['cur']:.1f} kWh</span> &nbsp; EUR {C['cur']:.2f}<br>
{fmt_pct(pct(H['cur'], H['prev']))} vs previous week ({H['prev']:.1f} kWh), {fmt_pct(pct(H['cur'], H['avg4']))} vs 4-week average ({H['avg4']:.1f} kWh){ly}</p>
{bars}
{away}
{breakdown}
<h2>Patterns, last 4 weeks</h2>
<p>Weekdays {r['wd']:.1f} kWh/day, weekends {r['we']:.1f} kWh/day. Busiest hour: <b>{(r['peak_h'] or 0):02d}:00</b>.</p>
<div class=hours>{hours}</div><div class=ax><span>00h</span><span>06h</span><span>12h</span><span>18h</span><span>23h</span></div>
{sb}
<h2>Forecast</h2>
<p>Next 7 days: <b>{r['next7']:.0f} kWh</b> (&plusmn;{r['band7']:.0f}), EUR {r['next7'] * p28:.2f}.<br>
{r['m0']:%B}: {r['mtd']:.0f} kWh so far, projected <b>{r['proj']:.0f} kWh</b> (&plusmn;{r['band']:.0f}), EUR {r['proj'] * p28:.2f},
{fmt_pct(pct(r['proj'], r['lastm']))} vs {r['lm1']:%B} ({r['lastm']:.0f} kWh).</p>
{f"<p>{e(r['season_text'])}</p>" if r['season_text'] else ""}
<p class=fine>{e(r['forecast_text'])} History since {r['first']}. {e(r['tariff_text'])} Data: Home Assistant hourly statistics.</p>
</body></html>"""
