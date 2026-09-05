import pytest
from datetime import datetime, date, time
from parentalcontrol.models import ScheduleRule, TimeSlot
from parentalcontrol.evaluator import matches_day, evaluate_access

def test_matches_day():
    # 2026-09-01 is a Tuesday (weekday index 1)
    tuesday = date(2026, 9, 1)
    saturday = date(2026, 9, 5)
    
    assert matches_day("All", tuesday) is True
    assert matches_day("all", saturday) is True
    assert matches_day("*", tuesday) is True
    
    assert matches_day("Weekday", tuesday) is True
    assert matches_day("Weekday", saturday) is False
    assert matches_day("Weekend", saturday) is True
    assert matches_day("Weekend", tuesday) is False

    assert matches_day("Monday-Friday", tuesday) is True
    assert matches_day("Monday-Friday", saturday) is False

    assert matches_day("Tue, Thu, Sat", tuesday) is True
    assert matches_day("Tue, Thu, Sat", saturday) is True
    assert matches_day("Mon, Wed, Fri", tuesday) is False

    assert matches_day("2026-09-01", tuesday) is True
    assert matches_day("2026-09-02", tuesday) is False

def test_evaluate_access_within_allowed_window():
    rules = [
        ScheduleRule(
            user="alex",
            day="Monday-Friday",
            start_time=time(16, 0),
            end_time=time(20, 0),
            allowed=True,
            max_minutes=120,
            message="Homework time",
        )
    ]
    # Tuesday at 17:30 (5:30 PM) -> Within 16:00 - 20:00
    check_dt = datetime(2026, 9, 1, 17, 30)
    result = evaluate_access("alex", rules, check_dt=check_dt)
    
    assert result.is_allowed is True
    assert result.active_slot is not None
    assert result.active_slot.start_time == time(16, 0)
    assert result.active_slot.end_time == time(20, 0)
    # Remaining from 17:30 to 20:00 is 2.5 hours = 150 minutes
    assert result.remaining_minutes == 150.0

def test_evaluate_access_outside_allowed_window():
    rules = [
        ScheduleRule(
            user="alex",
            day="Monday-Friday",
            start_time=time(16, 0),
            end_time=time(20, 0),
            allowed=True,
        )
    ]
    # Tuesday at 14:00 (2:00 PM) -> Before 16:00
    check_dt = datetime(2026, 9, 1, 14, 0)
    result = evaluate_access("alex", rules, check_dt=check_dt)
    
    assert result.is_allowed is False
    assert result.next_slot is not None
    assert result.next_slot.start_time == time(16, 0)

def test_evaluate_access_explicit_block_override():
    rules = [
        ScheduleRule(
            user="alex",
            day="All",
            start_time=time(0, 0),
            end_time=time(23, 59),
            allowed=False,
            message="Grounded: Clean your room first!",
        )
    ]
    check_dt = datetime(2026, 9, 1, 18, 0)
    result = evaluate_access("alex", rules, check_dt=check_dt)
    
    assert result.is_allowed is False
    assert "Clean your room first!" in result.reason

def test_evaluate_access_user_priority():
    rules = [
        ScheduleRule(
            user="*",
            day="All",
            start_time=time(12, 0),
            end_time=time(14, 0),
            allowed=True,
        ),
        ScheduleRule(
            user="alex",
            day="All",
            start_time=time(16, 0),
            end_time=time(20, 0),
            allowed=True,
        ),
    ]
    # Alex at 13:00 -> Wildcard has 12:00-14:00, but Alex specific rule is 16:00-20:00
    check_dt = datetime(2026, 9, 1, 13, 0)
    result_alex = evaluate_access("alex", rules, check_dt=check_dt)
    assert result_alex.is_allowed is False

    # Another user "sam" uses wildcard rule -> 13:00 is allowed
    result_sam = evaluate_access("sam", rules, check_dt=check_dt)
    assert result_sam.is_allowed is True

def test_matches_device():
    from parentalcontrol.evaluator import matches_device
    assert matches_device("*", "optiplex-3050") is True
    assert matches_device("", "optiplex-3050") is True
    assert matches_device("all", "optiplex-3050") is True
    assert matches_device("optiplex-3050", "optiplex-3050") is True
    assert matches_device("OptiPlex-3050", "optiplex-3050") is True
    assert matches_device("optiplex-3050", "OptiPlex-3050") is True
    assert matches_device("optiplex-3050, dell-laptop", "dell-laptop") is True
    assert matches_device("optiplex-3050, dell-laptop", "hp-pc") is False
    assert matches_device("laptop", "optiplex-3050") is False


def test_evaluate_access_device_specific_rules():
    rules = [
        # Himanshu on Desktop has 10:00 - 12:00
        ScheduleRule(
            user="himanshu",
            device="desktop-pc",
            day="All",
            start_time=time(10, 0),
            end_time=time(12, 0),
            allowed=True,
            message="Desktop study session",
        ),
        # Himanshu on Laptop has 14:00 - 16:00
        ScheduleRule(
            user="himanshu",
            device="laptop",
            day="All",
            start_time=time(14, 0),
            end_time=time(16, 0),
            allowed=True,
            message="Laptop afternoon session",
        ),
    ]

    check_dt_morning = datetime(2026, 9, 1, 11, 0)
    # On desktop-pc at 11:00 AM -> Allowed
    res_desktop = evaluate_access("himanshu", rules, check_dt=check_dt_morning, device="desktop-pc")
    assert res_desktop.is_allowed is True
    assert "Desktop study session" in res_desktop.reason or res_desktop.custom_message == "Desktop study session"

    # On laptop at 11:00 AM -> Denied (laptop rule starts at 14:00)
    res_laptop_morning = evaluate_access("himanshu", rules, check_dt=check_dt_morning, device="laptop")
    assert res_laptop_morning.is_allowed is False

    # On laptop at 15:00 (3:00 PM) -> Allowed
    check_dt_afternoon = datetime(2026, 9, 1, 15, 0)
    res_laptop_afternoon = evaluate_access("himanshu", rules, check_dt=check_dt_afternoon, device="laptop")
    assert res_laptop_afternoon.is_allowed is True


def test_evaluate_access_device_fallback_and_wildcard():
    rules = [
        # Global fallback for all users and all devices
        ScheduleRule(
            user="*",
            device="*",
            day="All",
            start_time=time(16, 0),
            end_time=time(18, 0),
            allowed=True,
        ),
        # Specific override for gaming PC
        ScheduleRule(
            user="*",
            device="gaming-rig",
            day="All",
            start_time=time(18, 0),
            end_time=time(20, 0),
            allowed=True,
        ),
    ]

    check_dt = datetime(2026, 9, 1, 17, 0)
    # Normal laptop at 17:00 -> Matches global rule (16:00 - 18:00)
    res_laptop = evaluate_access("himanshu", rules, check_dt=check_dt, device="normal-laptop")
    assert res_laptop.is_allowed is True

    # Gaming rig at 17:00 -> Gaming rig has specific device rule (18:00 - 20:00) which takes precedence over wildcard
    res_gaming = evaluate_access("himanshu", rules, check_dt=check_dt, device="gaming-rig")
    assert res_gaming.is_allowed is False


def test_evaluate_access_with_active_override(monkeypatch):
    from parentalcontrol.evaluator import evaluate_access

    fake_override = {
        "child_user": "himanshu",
        "granted_by": "atul",
        "expires_at": datetime(2026, 9, 1, 17, 30).timestamp(),
        "duration_minutes": 30,
    }
    monkeypatch.setattr(
        "parentalcontrol.override_manager.get_active_override",
        lambda u: fake_override if u == "himanshu" else None,
    )

    # Even with empty rules, active override allows access
    check_dt = datetime(2026, 9, 1, 17, 10)
    res = evaluate_access("himanshu", rules=[], check_dt=check_dt)
    assert res.is_allowed is True
    assert "Temporary access granted by atul" in res.reason
    assert res.remaining_minutes == 20.0
