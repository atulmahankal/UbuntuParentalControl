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
OVERRIDE_FALLBACK_DIR = Path("/tmp/parental-control")
OVERRIDE_FILE_NAME = "overrides.json"


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
