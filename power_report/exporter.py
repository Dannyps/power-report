"""Client side of the read-only Home Assistant exporter (exporter/power-export), local or over SSH."""

import json
import os
import subprocess


def export(cfg, site, *args):
    """Run the read-only exporter for a site and return its JSON."""
    s = cfg.sites[site]
    req = " ".join(str(a) for a in args)
    if "ssh" in s:
        p = subprocess.run(s["ssh"] + [req], capture_output=True, timeout=600)
    else:
        p = subprocess.run([cfg.exporter], capture_output=True, timeout=600,
                           env=dict(os.environ, HA_CONFIG=s["ha"], SSH_ORIGINAL_COMMAND=req))
    if p.returncode:
        raise RuntimeError(f"{site} exporter failed ({p.returncode}): {p.stderr.decode(errors='replace').strip()}")
    return json.loads(p.stdout)
