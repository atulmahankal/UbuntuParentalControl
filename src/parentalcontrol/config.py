"""Configuration management for Parental Control."""

import os
import getpass
from dataclasses import dataclass, field
from pathlib import Path
from typing import List, Optional
import yaml


DEFAULT_CONFIG_DIR = Path.home() / ".config" / "parental-control"
SYSTEM_CONFIG_DIR = Path("/etc/parental-control")
SYSTEM_CACHE_DIR = Path("/var/cache/parental-control")
SYSTEM_LOG_DIR = Path("/var/log")


@dataclass
class GoogleSheetConfig:
    url: str = ""
    service_account_path: Optional[str] = None
    sheet_name: Optional[str] = None
    sync_interval_minutes: int = 3


@dataclass
class RulesConfig:
    target_users: List[str] = field(default_factory=list)
    exempt_users: List[str] = field(default_factory=lambda: ["root", "admin", "parent"])
    offline_policy: str = "allow_cached"  # "allow_cached", "block", "grace_period"
    offline_grace_minutes: int = 15


@dataclass
class WarningsConfig:
    intervals_minutes: List[int] = field(default_factory=lambda: [30, 20, 10, 5, 2])
    show_notifications: bool = True
    show_modal_prompts: bool = True
    play_sound: bool = True


@dataclass
class EnforcementConfig:
    termination_grace_seconds: int = 30
    login_denial_grace_seconds: int = 15
    logout_command: Optional[str] = None
    lock_command: Optional[str] = None


@dataclass
class AppConfig:
    google_sheet: GoogleSheetConfig = field(default_factory=GoogleSheetConfig)
    rules: RulesConfig = field(default_factory=RulesConfig)
    warnings: WarningsConfig = field(default_factory=WarningsConfig)
    enforcement: EnforcementConfig = field(default_factory=EnforcementConfig)
    config_file_path: Optional[Path] = None

    @property
    def is_system_level(self) -> bool:
        if self.config_file_path and str(self.config_file_path).startswith("/etc/"):
            return True
        return os.geteuid() == 0 if hasattr(os, "geteuid") else False

    @property
    def cache_file_path(self) -> Path:
        if self.is_system_level:
            return SYSTEM_CONFIG_DIR / "schedule_cache.json"
        return DEFAULT_CONFIG_DIR / "schedule_cache.json"

    @property
    def log_file_path(self) -> Path:
        if self.is_system_level:
            return SYSTEM_LOG_DIR / "parental-control.log"
        return DEFAULT_CONFIG_DIR / "parental_control.log"

    def is_user_targeted(self, username: Optional[str] = None) -> bool:
        user = username or getpass.getuser()
        if user in self.rules.exempt_users:
            return False
        if not self.rules.target_users or "*" in self.rules.target_users or "all" in self.rules.target_users:
            return True
        return user in self.rules.target_users


def get_default_config_path() -> Path:
    """Return standard config path: system config if root or exists, else user config."""
    sys_cfg = SYSTEM_CONFIG_DIR / "config.yaml"
    user_cfg = DEFAULT_CONFIG_DIR / "config.yaml"
    if hasattr(os, "geteuid") and os.geteuid() == 0:
        return sys_cfg
    if sys_cfg.exists():
        return sys_cfg
    if user_cfg.exists():
        return user_cfg
    return sys_cfg


def load_config(config_path: Optional[Path] = None) -> AppConfig:
    """Load config from YAML file or return defaults."""
    path = config_path or get_default_config_path()
    if not path.exists():
        cfg = AppConfig()
        cfg.config_file_path = path
        return cfg

    try:
        with open(path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f) or {}

        gs_data = data.get("google_sheet", {})
        rules_data = data.get("rules", {})
        warn_data = data.get("warnings", {})
        enf_data = data.get("enforcement", {})

        cfg = AppConfig(
            google_sheet=GoogleSheetConfig(
                url=gs_data.get("url", ""),
                service_account_path=gs_data.get("service_account_path"),
                sheet_name=gs_data.get("sheet_name"),
                sync_interval_minutes=gs_data.get("sync_interval_minutes", 3),
            ),
            rules=RulesConfig(
                target_users=rules_data.get("target_users", []),
                exempt_users=rules_data.get("exempt_users", ["root", "admin", "parent"]),
                offline_policy=rules_data.get("offline_policy", "allow_cached"),
                offline_grace_minutes=rules_data.get("offline_grace_minutes", 15),
            ),
            warnings=WarningsConfig(
                intervals_minutes=warn_data.get("intervals_minutes", [30, 20, 10, 5, 2]),
                show_notifications=warn_data.get("show_notifications", True),
                show_modal_prompts=warn_data.get("show_modal_prompts", True),
                play_sound=warn_data.get("play_sound", True),
            ),
            enforcement=EnforcementConfig(
                termination_grace_seconds=enf_data.get("termination_grace_seconds", 30),
                login_denial_grace_seconds=enf_data.get("login_denial_grace_seconds", 15),
                logout_command=enf_data.get("logout_command"),
                lock_command=enf_data.get("lock_command"),
            ),
            config_file_path=path,
        )
        return cfg
    except Exception as e:
        print(f"Warning: Failed to load config from {path}: {e}. Using defaults.")
        cfg = AppConfig()
        cfg.config_file_path = path
        return cfg


def save_config(config: AppConfig, config_path: Optional[Path] = None) -> Path:
    """Save config to YAML file."""
    path = config_path or config.config_file_path or get_default_config_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    data = {
        "google_sheet": {
            "url": config.google_sheet.url,
            "service_account_path": config.google_sheet.service_account_path,
            "sheet_name": config.google_sheet.sheet_name,
            "sync_interval_minutes": config.google_sheet.sync_interval_minutes,
        },
        "rules": {
            "target_users": config.rules.target_users,
            "exempt_users": config.rules.exempt_users,
            "offline_policy": config.rules.offline_policy,
            "offline_grace_minutes": config.rules.offline_grace_minutes,
        },
        "warnings": {
            "intervals_minutes": config.warnings.intervals_minutes,
            "show_notifications": config.warnings.show_notifications,
            "show_modal_prompts": config.warnings.show_modal_prompts,
            "play_sound": config.warnings.play_sound,
        },
        "enforcement": {
            "termination_grace_seconds": config.enforcement.termination_grace_seconds,
            "login_denial_grace_seconds": config.enforcement.login_denial_grace_seconds,
            "logout_command": config.enforcement.logout_command,
            "lock_command": config.enforcement.lock_command,
        },
    }

    with open(path, "w", encoding="utf-8") as f:
        yaml.dump(data, f, default_flow_style=False, sort_keys=False)

    return path
