"""Temporary parent override management for Parental Control."""

import json
import logging
import os
import time
from datetime import datetime, timedelta
from pathlib import Path
from typing import Dict, Optional

logger = logging.getLogger(__name__)

# Primary runtime directory for active override state
OVERRIDE_RUN_DIR = Path("/run/parental-control")
OVERRIDE_PERSIST_DIR = Path("/var/lib/parental-control")
OVERRIDE_FALLBACK_DIR = Path("/tmp/parental-control")
OVERRIDE_FILE_NAME = "overrides.json"
EXTENSIONS_FILE_NAME = "extensions_5m.json"


def _get_extension_file_path() -> Path:
    """Return the active path for storing 5-minute extension state."""
    env_ext = os.environ.get("PARENTAL_CONTROL_EXTENSIONS_FILE")
    if env_ext:
        return Path(env_ext)

    for base in [OVERRIDE_PERSIST_DIR, OVERRIDE_RUN_DIR, OVERRIDE_FALLBACK_DIR]:
        try:
            base.mkdir(parents=True, exist_ok=True)
            try:
                base.chmod(0o777)
            except Exception:
                pass
            test_file = base / ".write_test_ext"
            test_file.touch()
            test_file.unlink()
            return base / EXTENSIONS_FILE_NAME
        except Exception:
            continue
    return OVERRIDE_FALLBACK_DIR / EXTENSIONS_FILE_NAME


def load_all_extensions_state(file_path: Optional[Path] = None) -> Dict[str, dict]:
    """Load extension history for all users."""
    p = file_path or _get_extension_file_path()
    if not p.exists():
        return {}
    try:
        with open(p, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read 5m extensions state from {p}: {e}")
        return {}


def _save_extensions_state(data: Dict[str, dict], file_path: Optional[Path] = None) -> bool:
    """Save extension state atomically to disk."""
    p = file_path or _get_extension_file_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = p.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            tmp_path.chmod(0o666)
        except Exception:
            pass
        tmp_path.replace(p)
        try:
            p.chmod(0o666)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to write 5m extensions state to {p}: {e}")
        return False


def has_used_5m_extension_today(
    username: str,
    file_path: Optional[Path] = None,
    check_date: Optional[str] = None,
) -> bool:
    """Return True if the user has already used their 1-time 5-minute extension today."""
    today_str = check_date or datetime.now().strftime("%Y-%m-%d")
    state = load_all_extensions_state(file_path)
    user_record = state.get(username.lower().strip())
    if not user_record or not isinstance(user_record, dict):
        return False
    return user_record.get("date") == today_str


def grant_5m_work_extension(
    child_user: str,
    file_path: Optional[Path] = None,
    overrides_path: Optional[Path] = None,
) -> dict:
    """Grant a 1-time 5-minute emergency extension so the child can save unsaved work."""
    child_clean = child_user.lower().strip()
    today_str = datetime.now().strftime("%Y-%m-%d")

    p = file_path or _get_extension_file_path()
    if has_used_5m_extension_today(child_clean, file_path=p, check_date=today_str):
        raise ValueError("One-time 5-minute work extension has already been used today.")

    now_ts = time.time()
    # 1. Grant standard 5-minute override
    override_rec = grant_temporary_override(
        child_user=child_clean,
        parent_user="Self-Service (Save Work)",
        duration_minutes=5,
        file_path=overrides_path,
    )

    # 2. Record that extension was used today
    state = load_all_extensions_state(p)
    state[child_clean] = {
        "user": child_clean,
        "date": today_str,
        "used_at": now_ts,
        "expires_at": override_rec.get("expires_at", now_ts + 300),
    }
    _save_extensions_state(state, p)

    logger.info(f"Granted 1-time 5m work saving extension to '{child_clean}' for {today_str}.")
    return {
        "success": True,
        "duration_minutes": 5,
        "expires_at": override_rec.get("expires_at"),
        "date": today_str,
    }


def reset_5m_extension(username: str, file_path: Optional[Path] = None) -> bool:
    """Reset the 1-time 5-minute extension state for username (admin/testing)."""
    p = file_path or _get_extension_file_path()
    state = load_all_extensions_state(p)
    child_clean = username.lower().strip()
    if child_clean in state:
        del state[child_clean]
        _save_extensions_state(state, p)
        return True
    return False



def _get_override_file_path() -> Path:
    """Return the active path for storing temporary overrides."""
    env_override = os.environ.get("PARENTAL_CONTROL_OVERRIDES_FILE")
    if env_override:
        return Path(env_override)

    for base in [OVERRIDE_RUN_DIR, OVERRIDE_FALLBACK_DIR]:
        try:
            base.mkdir(parents=True, exist_ok=True)
            # Ensure permissions allow read/write
            try:
                base.chmod(0o777)
            except Exception:
                pass
            test_file = base / ".write_test"
            test_file.touch()
            test_file.unlink()
            return base / OVERRIDE_FILE_NAME
        except Exception:
            continue
    return OVERRIDE_FALLBACK_DIR / OVERRIDE_FILE_NAME


def load_all_overrides(file_path: Optional[Path] = None) -> Dict[str, dict]:
    """Load all overrides, pruning expired ones."""
    p = file_path or _get_override_file_path()
    if not p.exists():
        return {}

    try:
        with open(p, "r", encoding="utf-8") as f:
            data = json.load(f)
    except Exception as e:
        logger.warning(f"Failed to read overrides from {p}: {e}")
        return {}

    now_ts = time.time()
    valid_data = {}
    pruned = False

    for user, info in data.items():
        if isinstance(info, dict) and info.get("expires_at", 0) > now_ts:
            valid_data[user] = info
        else:
            pruned = True

    if pruned:
        _save_overrides(valid_data, p)

    return valid_data


def _save_overrides(data: Dict[str, dict], file_path: Optional[Path] = None) -> bool:
    """Save overrides dict atomically to disk."""
    p = file_path or _get_override_file_path()
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = p.with_suffix(".tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
        try:
            tmp_path.chmod(0o666)
        except Exception:
            pass
        tmp_path.replace(p)
        try:
            p.chmod(0o666)
        except Exception:
            pass
        return True
    except Exception as e:
        logger.error(f"Failed to write overrides to {p}: {e}")
        return False


def grant_temporary_override(
    child_user: str,
    parent_user: str,
    duration_minutes: int,
    file_path: Optional[Path] = None,
) -> dict:
    """Grant a temporary screen time extension for a child user."""
    p = file_path or _get_override_file_path()
    overrides = load_all_overrides(p)

    now_ts = time.time()
    expires_at = now_ts + (duration_minutes * 60)
    expiry_dt = datetime.fromtimestamp(expires_at)

    override_record = {
        "child_user": child_user,
        "granted_by": parent_user,
        "granted_at": now_ts,
        "duration_minutes": duration_minutes,
        "expires_at": expires_at,
        "expires_at_iso": expiry_dt.isoformat(),
    }

    overrides[child_user] = override_record
    _save_overrides(overrides, p)
    logger.info(
        f"Granted {duration_minutes}m override for '{child_user}' by '{parent_user}' (expires at {expiry_dt.strftime('%I:%M %p')})"
    )
    return override_record


def get_active_override(username: str, file_path: Optional[Path] = None) -> Optional[dict]:
    """Return active override for username if valid and unexpired."""
    overrides = load_all_overrides(file_path)
    record = overrides.get(username)
    if record and record.get("expires_at", 0) > time.time():
        return record
    return None


def revoke_override(username: str, file_path: Optional[Path] = None) -> bool:
    """Revoke any active override for the specified user."""
    p = file_path or _get_override_file_path()
    overrides = load_all_overrides(p)
    if username in overrides:
        del overrides[username]
        _save_overrides(overrides, p)
        logger.info(f"Revoked override for user '{username}'")
        return True
    return False
