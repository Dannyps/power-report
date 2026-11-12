#!/usr/bin/env python3
"""Run every panel query of the provisioned Power dashboard through Grafana's API, per site (read-only).

  grafana/test-dashboard.py [site ...]     default: every site in the power-report config
Grafana at http://127.0.0.1:3000 (GRAFANA_URL overrides); the admin password is read from GRAFANA_ADMIN_PASSWORD
in the file named by [grafana] env_file in the config. Prints row counts and series names, not values of rows.
"""
import base64, json, os, re, sys, urllib.request
from pathlib import Path
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from power_report import config  # noqa: E402
CFG = config.load()
pw = re.search(r"^GRAFANA_ADMIN_PASSWORD=(.*)$", open(CFG.grafana["env_file"]).read(), re.M).group(1).strip()
auth = "Basic " + base64.b64encode(f"admin:{pw}".encode()).decode()
def call(path, body=None):
    req = urllib.request.Request(os.environ.get("GRAFANA_URL", "http://127.0.0.1:3000") + path, data=json.dumps(body).encode() if body else None,
                                 headers={"Authorization": auth, "Content-Type": "application/json"})
    try:
        return json.load(urllib.request.urlopen(req, timeout=60))
    except urllib.error.HTTPError as e:
        return json.load(e)
dash = call("/api/dashboards/uid/power-overview")["dashboard"]
ds = {"type": "postgres", "uid": "power-pg"}
for site in sys.argv[1:] or list(CFG.sites):
    q = call("/api/ds/query", {"from": "now-1h", "to": "now", "queries": [{"refId": "A", "datasource": ds, "rawSql":
             f"select label from entities where kind='device' and site='{site}' order by label", "format": "table"}]})
    labels = q["results"]["A"]["frames"][0]["data"]["values"][0] if q["results"]["A"].get("frames") else []
    devs = ",".join("'" + l.replace("'", "''") + "'" for l in labels) or "''"
    print(f"== {site} (devices: {len(labels)})")
    for p in dash["panels"]:
        sql = p["targets"][0]["rawSql"].replace("$site", site).replace("$device", devs)
        r = call("/api/ds/query", {"from": "now-45d", "to": "now+14d", "queries": [{"refId": "A", "datasource": ds, "rawSql": sql,
                 "format": p["targets"][0]["format"]}]})["results"]["A"]
        if r.get("error"):
            print(f"  ERROR {p['title']}: {r['error'][:200]}")
            continue
        frames = r.get("frames", [])
        rows = sum(len(f["data"]["values"][0]) if f["data"]["values"] else 0 for f in frames)
        sample = ""
        if p["type"] == "stat" and rows:
            sample = f" value={frames[0]['data']['values'][-1][0]}"
        series = sorted({fl.get("config", {}).get("displayNameFromDS") or fl.get("labels", {}).get("metric") or fl["name"]
                         for f in frames for fl in f["schema"]["fields"][1:]})
        print(f"  {p['title'][:60]:<60} rows={rows}{sample} series={series[:6]}")
