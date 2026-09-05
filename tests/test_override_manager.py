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
