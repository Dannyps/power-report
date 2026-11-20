"""Unit tests on synthetic data only (this repo is public: never put real readings here). Run: bin/test.

The real-data check is tools/equivalence.py, which compares this code with a previous version on a copy of the
database and keeps its data outside the repo.
"""

import datetime as dt
import json
import os
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from zoneinfo import ZoneInfo

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))

from power_report import cli, config, model, render  # noqa: E402
from power_report.sync import climate_entities, climate_rows, pretty, site_entities  # noqa: E402

TZ = ZoneInfo("Europe/Lisbon")


def cfg(extra=""):
    d = tempfile.mkdtemp()
    p = Path(d) / "c.toml"
    p.write_text('timezone = "Europe/Lisbon"\n[mail]\nsender = "x <x@example.org>"\n'
                 '[sites.home]\nha = "/nonexistent"\ncircuits = ["sensor.c1_energy"]\n' + extra +
                 '[sites.home.labels]\n"sensor.c1_energy" = "Sockets"\n')
    return config.load(str(p))


class Config(unittest.TestCase):
    def test_example_config_loads(self):
        c = config.load(str(REPO / "config.example.toml"))
        self.assertEqual(list(c.sites), ["home", "cabin"])
        self.assertEqual(c.sites["cabin"]["ev"]["last_hour"], 6)
        self.assertEqual(str(c.tz), "Europe/Lisbon")

    def test_site_needs_a_source(self):
        p = Path(tempfile.mkdtemp()) / "c.toml"
        p.write_text("[sites.x]\ncircuits = []\n")
        with self.assertRaises(ValueError):
            config.load(str(p))

    def test_env_var_selects_the_file(self):
        os.environ["POWER_REPORT_CONFIG"] = str(Path(tempfile.mkdtemp()) / "missing.toml")
        try:
            with self.assertRaises(FileNotFoundError):
                config.load()
        finally:
            del os.environ["POWER_REPORT_CONFIG"]


class Model(unittest.TestCase):
    def test_recent_weights_half_life(self):
        o = dt.date(2026, 3, 1)
        w = dict(model.recent_weights([o - dt.timedelta(days=21), o, o - dt.timedelta(days=57)], o))
        self.assertAlmostEqual(w[o - dt.timedelta(days=21)], 0.5)
        self.assertEqual(len(w), 1)                      # today and > 56 days ago are out

    def test_weekday_level_fills_missing_weekdays(self):
        o = dt.date(2026, 3, 2)                          # a Monday
        series = {o - dt.timedelta(days=i): 10.0 for i in range(1, 57) if (o - dt.timedelta(days=i)).weekday() != 2}
        lv = model.weekday_level(series, o)
        self.assertEqual(set(lv), set(range(7)))
        self.assertAlmostEqual(lv[2], 10.0)              # Wednesday missing -> mean of the others

    def test_season_index_needs_a_year_and_has_mean_one(self):
        start = dt.date(2024, 1, 1)
        short = {start + dt.timedelta(days=i): 10.0 for i in range(200)}
        self.assertIsNone(model.season_index(short, start + dt.timedelta(days=200)))
        # winter high, summer low
        series = {}
        for i in range(800):
            d = start + dt.timedelta(days=i)
            series[d] = 10 + 5 * (1 if d.month in (12, 1, 2) else -1 if d.month in (6, 7, 8) else 0)
        S = model.season_index(series, start + dt.timedelta(days=800))
        self.assertAlmostEqual(sum(S.values()) / len(S), 1.0, places=9)
        self.assertGreater(S[15], 1.2)                   # mid January
        self.assertLess(S[196], 0.8)                     # mid July

    def test_away_days_need_three_nights(self):
        d0 = dt.date(2026, 1, 1)
        days = [d0 + dt.timedelta(days=i) for i in range(10)]
        ev = {d: 5.0 for d in days}
        for i in (1, 2):                                  # 2 nights: not away
            ev[days[i]] = 0
        for i in (5, 6, 7):                               # 3 nights: away
            ev[days[i]] = 0
        self.assertEqual(model.away_days(days, ev), {days[5], days[6], days[7]})

    def test_daily_forecast_without_season_is_the_weekday_level(self):
        o = dt.date(2026, 3, 2)
        series = {o - dt.timedelta(days=i): float((o - dt.timedelta(days=i)).weekday()) for i in range(1, 57)}
        f = model.daily_forecast(series, None, o, [o, o + dt.timedelta(days=3)])
        self.assertAlmostEqual(f[o], 0.0)
        self.assertAlmostEqual(f[o + dt.timedelta(days=3)], 3.0)


class Entities(unittest.TestCase):
    def test_flat_tariff_site(self):
        c = cfg()
        hacfg = {"names": {"sensor.dev_today_energy": "Fridge"},
                 "energy": {"energy_sources": [{"type": "grid", "stat_energy_from": "sensor.house_energy",
                                                "number_energy_price": 0.14}],
                            "device_consumption": [{"stat_consumption": "sensor.c1_energy"},
                                                   {"stat_consumption": "sensor.dev_today_energy", "included_in_stat": "sensor.c1_energy"}]}}
        house, rows = site_entities(c, "home", hacfg)
        self.assertEqual(house, "sensor.house_energy")
        self.assertEqual(rows["sensor.c1_energy"]["kind"], "circuit")
        self.assertEqual(rows["sensor.c1_energy"]["label"], "Sockets")           # config label wins
        self.assertEqual(rows["sensor.dev_today_energy"]["label"], "Fridge")     # HA device name
        self.assertEqual(rows["sensor.dev_today_energy"]["parent"], "sensor.c1_energy")
        self.assertEqual(pretty(c, "home", "sensor.boiler_total_energy", {}), "Boiler")

    def test_tariff_registers_need_a_house_meter(self):
        c = cfg('house = "sensor.meter"\n')
        hacfg = {"names": {}, "energy": {"device_consumption": [], "energy_sources": [
            {"type": "grid", "stat_energy_from": "sensor.vazio", "number_energy_price": 0.1},
            {"type": "grid", "stat_energy_from": "sensor.ponta", "number_energy_price": 0.2}]}}
        house, rows = site_entities(c, "home", hacfg)
        self.assertEqual(house, "sensor.meter")
        self.assertEqual({e: r["kind"] for e, r in rows.items()},
                         {"sensor.meter": "house", "sensor.vazio": "tariff", "sensor.ponta": "tariff"})


def figures(**over):
    """A synthetic, complete set of report figures."""
    today = dt.date(2026, 3, 9)
    days = {today - dt.timedelta(days=7 - i): 10.0 + i for i in range(7)}
    H = dict(label="House", cur=sum(days.values()), prev=70.0, avg4=80.0, days=days, price=0.14)
    r = dict(site="home", tz=TZ, today=today, w1=today - dt.timedelta(days=7), w2=today - dt.timedelta(days=14), H=H,
             costs={"cur": H["cur"] * 0.14, "prev": 9.8, "avg4": 11.2}, p_week=0.14, p28=0.14, tariffs=[],
             circuits=[dict(label="Sockets", cur=40.0, prev=35.0, avg4=0.0)], devices=[dict(label="Fridge & Co <1>", cur=5.0, prev=5.0, avg4=5.0)],
             other=dict(label="Other (lights, hardwired, standby)", cur=H["cur"] - 40, prev=35.0, avg4=80.0),
             hp={h: 1.0 + h for h in range(24)}, wd=11.0, we=12.0, peak_h=23, sb=(150.0, 160.0, None), last_year=None,
             mtd=100.0, lastm=300.0, lm1=dt.date(2026, 2, 1), m0=dt.date(2026, 3, 1), proj=320.0, band=20.0, next7=80.0,
             band7=10.0, first=dt.date(2025, 9, 1), stale=None, ndays_left=23, tariff_text="Flat tariff EUR 0.14/kWh.",
             E=None, away_week=0, away_since=None, away_nights=None, away_min=3, ev_window=(0, 6),
             forecast_text="The forecast repeats the weekly rhythm.", season_text=None)
    r.update(over)
    return r


class Render(unittest.TestCase):
    def test_text_report(self):
        t = render.text_report(figures())
        self.assertIn("Power report (home): Mon 02 Mar to Sun 08 Mar 2026", t)
        self.assertIn("House: 91.0 kWh = EUR 12.74  (+30% vs previous week 70.0 kWh, +14% vs 4-week average 80.0 kWh)", t)
        self.assertIn("Other (lights, hardwired, standby)", t)
        self.assertIn("vs 4-week avg", t)
        self.assertIn("  " + "Sockets".ljust(36) + "   40.0    +14%     n/a", t)    # avg4 0 -> n/a
        self.assertIn("Busiest hour of the day: 23:00-00:00.", t)
        self.assertNotIn("WARNING", t)

    def test_stale_and_away_warnings(self):
        stale = dt.datetime(2026, 3, 8, 12, tzinfo=dt.timezone.utc)
        t = render.text_report(figures(stale=stale, away_since=dt.date(2026, 3, 6), away_nights=3))
        self.assertIn("WARNING: no data since Sun 08 Mar 12:00", t)
        self.assertIn("Away? No EV charging for 3 nights in a row, since Fri 06 Mar.", t)

    def test_html_escapes_labels_and_uses_the_ev_window(self):
        E = dict(cur=20.0, prev=10.0, avg4=15.0, price=0.1, nights=3, share=22.0, days=figures()["H"]["days"])
        h = render.html_report(figures(E=E, ev_window=(1, 5)))
        self.assertIn("Fridge &amp; Co &lt;1&gt;", h)
        self.assertNotIn("Fridge & Co <1>", h)
        self.assertIn("Read from the night load (01-05h above", h)

    def test_pct(self):
        self.assertIsNone(render.pct(5, 0))
        self.assertEqual(render.fmt_pct(None), "n/a")
        self.assertEqual(render.fmt_pct(render.pct(110, 100)), "+10%")


class Climate(unittest.TestCase):
    def test_example_config_has_rooms(self):
        c = config.load(str(REPO / "config.example.toml"))
        ents = climate_entities(c.sites["home"])
        self.assertEqual(ents["sensor.heating_hours_today"], ("heating_hours", "Heating (relay on)"))
        self.assertEqual(ents["sensor.living_room_humidity"], ("humidity", "Living room"))
        self.assertEqual(climate_entities(c.sites["cabin"]), {})          # no [climate] table: nothing

    def test_rows_counters_are_hourly_changes_measurements_means(self):
        ents = {"sensor.h": ("heating_hours", "Heating"), "sensor.t": ("temperature", "Room")}
        t0 = 1_772_000_000 - 1_772_000_000 % 3600
        data = {"sensor.h": [[t0, 1.0, None], [t0 + 3600, 1.5, None], [t0 + 7200, None, None], [t0 + 4 * 3600, 2.5, None]],
                "sensor.t": [[t0, None, 19.0], [t0 + 3600, None, 19.5]]}
        rows = climate_rows("home", ents, data, {"sensor.h": t0 + 1, "sensor.t": t0})
        h = [(r[2].timestamp() - t0, r[5]) for r in rows if r[1] == "sensor.h"]
        self.assertEqual(h, [(3600, 0.5), (4 * 3600, 1.0)])             # before `since` skipped, gap -> next hour
        self.assertEqual([r[5] for r in rows if r[1] == "sensor.t"], [19.0, 19.5])
        self.assertEqual(rows[0][3:5], ("heating_hours", "Heating"))

    def climate(self):
        days = {dt.date(2026, 3, 2) + dt.timedelta(days=i): (1.5 if i else None) for i in range(7)}
        return dict(counters={"heating_hours": dict(cur=9.0, prev=6.0, hours=168, days=days),
                              "burner_hours": dict(cur=3.0, prev=2.0, hours=168, days=days),
                              "gas": dict(cur=2.5, prev=2.0, hours=168, days=days)},
                    rooms=[dict(label="Up <stairs>", temperature_cur=20.04, temperature_prev=19.5, temperature_min=18.0,
                                temperature_max=21.5, temperature_hours=168, humidity_cur=66.0, humidity_prev=60.0,
                                humidity_min=55.0, humidity_max=70.0, humidity_hours=168, damp_hours=30),
                           dict(label="Attic", temperature_cur=None, temperature_prev=None, temperature_min=None,
                                temperature_max=None, temperature_hours=0)],
                    limit=65.0, gas_kwh=11.0, first=dt.date(2026, 2, 20))

    def test_text_section(self):
        t = render.text_report(figures(climate=self.climate()))
        self.assertIn("Heating and indoor climate:", t)
        self.assertIn("Heating on 9.0 h (+50% vs previous week, 6.0 h), burner 3.0 h (33% of it).", t)
        self.assertIn("Gas 2.50 m³ = about 28 kWh (+25% vs previous week; from meter readings).", t)
        self.assertIn("Up <stairs>: 20.0 °C average (+0.5 °C vs previous week), hourly 18.0 to 21.5 °C; "
                      "humidity 66% average, 30 h above 65%.", t)
        self.assertNotIn("Attic", t)                                       # no data: no line
        self.assertNotIn("Heating and indoor climate", render.text_report(figures()))
        empty = self.climate()
        for k in empty["counters"].values():
            k.update(cur=0.0, prev=0.0, hours=0)
        t = render.text_report(figures(climate=empty))
        self.assertIn("Heating: no data for this week yet.", t)
        self.assertIn("Gas: no data for this week yet.", t)
        self.assertNotIn("Daily heating hours", t)
        self.assertNotIn("class=bars><tr><td class=bl>Mon 02</td><td class=bt><div style='width:0%;background:#d9822b'",
                         render.html_report(figures(climate=empty)))

    def test_html_section(self):
        h = render.html_report(figures(climate=self.climate()))
        self.assertIn("<h2>Heating and indoor climate</h2>", h)
        self.assertIn("Up &lt;stairs&gt;", h)
        self.assertIn("1.5 h</td>", h)
        self.assertIn("Climate data since Fri 20 Feb 2026.", h)


class Run(unittest.TestCase):
    def test_run_carries_on_past_a_failing_site_and_step(self):
        calls = []

        def step(name):
            def f(c, site, send):
                calls.append((name, site))
                if name == "sync" and site == "cabin":
                    raise RuntimeError("cabin unreachable")
            return f
        saved = dict(cli.STEPS)
        cli.STEPS.update({k: step(k) for k in ("sync", "forecast", "report")})
        os.environ["POWER_REPORT_CONFIG"] = str(REPO / "config.example.toml")
        try:
            rc = cli.main(["run", "--no-send"])
        finally:
            cli.STEPS.clear()
            cli.STEPS.update(saved)
            del os.environ["POWER_REPORT_CONFIG"]
        self.assertEqual(rc, 1)
        self.assertEqual(calls, [(s, x) for s in ("sync", "forecast", "report") for x in ("home", "cabin")])


class Exporter(unittest.TestCase):
    """The HA-side exporter against a synthetic Home Assistant config directory."""

    def setUp(self):
        self.ha = Path(tempfile.mkdtemp())
        st = self.ha / ".storage"
        st.mkdir()
        (st / "energy").write_text(json.dumps({"data": {"energy_sources": [{"type": "grid", "stat_energy_from": "sensor.house_energy"}],
                                                        "device_consumption": [{"stat_consumption": "sensor.dev_energy"}]}}))
        (st / "core.entity_registry").write_text(json.dumps({"data": {"entities": [{"entity_id": "sensor.dev_energy", "device_id": "d1"}]}}))
        (st / "core.device_registry").write_text(json.dumps({"data": {"devices": [{"id": "d1", "name": "Plug", "name_by_user": "Kettle"}]}}))
        db = sqlite3.connect(self.ha / "home-assistant_v2.db")
        db.execute("create table statistics_meta (id integer, statistic_id text, unit_of_measurement text)")
        db.execute("create table statistics (metadata_id integer, start_ts real, sum real, mean real)")
        db.executemany("insert into statistics_meta values (?,?,?)", [(1, "sensor.house_energy", "kWh"), (2, "sensor.dev_energy", "Wh")])
        db.executemany("insert into statistics values (?,?,?,?)", [(1, 3600 * i, float(i), None) for i in range(5)])
        db.commit()

    def run_exporter(self, req):
        return subprocess.run([sys.executable, str(REPO / "exporter" / "power-export")], capture_output=True, text=True,
                              env=dict(os.environ, HA_CONFIG=str(self.ha), SSH_ORIGINAL_COMMAND=req))

    def test_config(self):
        r = self.run_exporter("config")
        out = json.loads(r.stdout)
        self.assertEqual(out["names"]["sensor.dev_energy"], "Kettle")
        self.assertEqual(out["units"], {"sensor.house_energy": "kWh", "sensor.dev_energy": "Wh"})

    def test_stats_since(self):
        out = json.loads(self.run_exporter("stats 7200 sensor.house_energy").stdout)
        self.assertEqual([row[0] for row in out["sensor.house_energy"]], [7200, 10800, 14400])

    def test_refuses_bad_requests(self):
        self.assertNotEqual(self.run_exporter("stats 0 sensor.x;rm").returncode, 0)
        self.assertNotEqual(self.run_exporter("drop everything").returncode, 0)
        self.assertNotEqual(self.run_exporter("").returncode, 0)


if __name__ == "__main__":
    unittest.main()
