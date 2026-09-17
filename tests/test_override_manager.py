import time
from datetime import datetime
from parentalcontrol.override_manager import (
    grant_temporary_override,
    get_active_override,
    load_all_overrides,
    revoke_override,
)

def test_grant_and_get_active_override(tmp_path):
    test_file = tmp_path / "test_overrides.json"
    
    assert get_active_override("himanshu", file_path=test_file) is None
    
    rec = grant_temporary_override("himanshu", "atul", 30, file_path=test_file)
    assert rec["child_user"] == "himanshu"
    assert rec["granted_by"] == "atul"
    assert rec["duration_minutes"] == 30
    assert rec["expires_at"] > time.time()
    
    active = get_active_override("himanshu", file_path=test_file)
    assert active is not None
    assert active["granted_by"] == "atul"
    
    # Revoke
    assert revoke_override("himanshu", file_path=test_file) is True
    assert get_active_override("himanshu", file_path=test_file) is None

def test_expired_override_pruning(tmp_path):
    test_file = tmp_path / "test_overrides.json"
    
    # Grant with negative duration (already expired)
    grant_temporary_override("himanshu", "atul", -5, file_path=test_file)
    
    assert get_active_override("himanshu", file_path=test_file) is None
    all_active = load_all_overrides(file_path=test_file)
    assert "himanshu" not in all_active


def test_5m_work_extension(tmp_path):
    import pytest
    from parentalcontrol.override_manager import (
        has_used_5m_extension_today,
        grant_5m_work_extension,
        reset_5m_extension,
    )

    ext_file = tmp_path / "test_extensions_5m.json"
    ovr_file = tmp_path / "test_overrides.json"

    # Initially unused
    assert has_used_5m_extension_today("himanshu", file_path=ext_file) is False

    # Grant 1-time extension
    res = grant_5m_work_extension("himanshu", file_path=ext_file, overrides_path=ovr_file)
    assert res["success"] is True
    assert res["duration_minutes"] == 5
    assert res["expires_at"] > time.time()

    # Active override is present in overrides
    active = get_active_override("himanshu", file_path=ovr_file)
    assert active is not None
    assert active["duration_minutes"] == 5

    # Should now be marked as used today
    assert has_used_5m_extension_today("himanshu", file_path=ext_file) is True

    # Re-granting on same day should raise ValueError
    with pytest.raises(ValueError, match="already been used today"):
        grant_5m_work_extension("himanshu", file_path=ext_file, overrides_path=ovr_file)

    # Different date should return False
    assert has_used_5m_extension_today("himanshu", file_path=ext_file, check_date="2099-01-01") is False

    # Reset allows granting again
    assert reset_5m_extension("himanshu", file_path=ext_file) is True
    assert has_used_5m_extension_today("himanshu", file_path=ext_file) is False

