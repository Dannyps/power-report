"""What bin/deploy installs where, shared by deploy, drift and check. Paths are this project's standard layout;
host-specific ones (the web endpoint, remote exporters, the Grafana dashboard) come from the config."""

import hashlib
import shlex
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
from power_report import config  # noqa: E402

LIB = Path("/usr/local/lib/power-report")
UNIT_DIR = Path("/etc/systemd/system")
ENABLE = ["power-sync.timer", "power-forecast.timer", "power-report.timer", "power-report-request.path"]
BACKUPS = Path("/var/backups/power-report")


def local_pairs(cfg):
    """(repo file, live path) for everything installed on this host."""
    pairs = [(p, LIB / "power_report" / p.name) for p in sorted((REPO / "power_report").glob("*.py"))]
    pairs += [(REPO / "exporter" / "power-export", LIB / "power-export"),
              (REPO / "bin" / "power-report", Path("/usr/local/bin/power-report.py")),
              (REPO / "systemd" / "tmpfiles-power-report.conf", Path("/etc/tmpfiles.d/power-report.conf"))]
    pairs += [(p, UNIT_DIR / p.name) for p in sorted((REPO / "systemd").glob("power-*")) if p.suffix in (".service", ".timer", ".path")]
    if cfg.deploy.get("web"):
        pairs.append((REPO / "web" / "power-report.php", Path(cfg.deploy["web"])))
    return pairs


def remote_exporters(cfg):
    """(site, ssh target, path) for sites whose exporter bin/deploy installs over SSH."""
    return [(name, s["install_ssh"], s.get("install_path", "/usr/local/bin/power-export"))
            for name, s in cfg.sites.items() if s.get("install_ssh")]


def ssh(target, cmd, data=None):
    """Run a command on a remote host as root (bash, whatever the login shell is)."""
    # ssh joins its arguments into one string for the remote login shell (which may be fish), so quote the
    # whole command for `bash -c` instead of passing it as separate words.
    return subprocess.run(["ssh", "-o", "BatchMode=yes", "-o", "ConnectTimeout=20", target, "bash -c " + shlex.quote(cmd)],
                          input=data, capture_output=True, timeout=120)


def remote_sha(target, path):
    r = ssh(target, f"sha256sum {path} 2>/dev/null | cut -d' ' -f1")
    return r.stdout.decode().strip() if r.returncode == 0 else None


def sha(p: Path):
    return hashlib.sha256(p.read_bytes()).hexdigest()


def load():
    return config.load()
