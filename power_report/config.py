"""Configuration: /etc/power-report/config.toml (POWER_REPORT_CONFIG overrides). See config.example.toml."""

import os
import tomllib
from zoneinfo import ZoneInfo

DEFAULT_PATH = "/etc/power-report/config.toml"


class Config:
    def __init__(self, data: dict):
        self.data = data
        self.tz_name = data.get("timezone", "UTC")
        self.tz = ZoneInfo(self.tz_name)
        self.horizon_days = int(data.get("horizon_days", 40))
        self.no_send_dir = os.environ.get("POWER_REPORT_NO_SEND_DIR") or data.get("no_send_dir", "/tmp")
        self.deploy = data.get("deploy", {})
        mail = data.get("mail", {})
        self.sender = mail.get("sender", "Power report <root@localhost>")
        self.recipient = mail.get("to", "root")
        self.sendmail = mail.get("sendmail", "/usr/sbin/sendmail")
        self.db = data.get("database", {})
        self.exporter = data.get("exporter", {}).get("local", "/usr/local/lib/power-report/power-export")
        self.grafana = data.get("grafana", {})
        self.sites = data.get("sites", {})
        if not self.sites:
            raise ValueError("config has no [sites.<name>] table")
        for name, s in self.sites.items():
            s.setdefault("circuits", [])
            s.setdefault("labels", {})
            if not s.get("ha") and not s.get("ssh"):
                raise ValueError(f"site {name}: needs `ha` (local Home Assistant) or `ssh` (remote exporter)")


def load(path: str | None = None) -> Config:
    with open(path or os.environ.get("POWER_REPORT_CONFIG") or DEFAULT_PATH, "rb") as f:
        return Config(tomllib.load(f))
