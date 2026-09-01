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
