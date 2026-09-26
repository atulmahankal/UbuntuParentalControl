"""Unit tests for local application usage store."""

import json
from pathlib import Path
from parentalcontrol.app_usage_store import AppUsageStore


def test_app_usage_store_record_and_get(tmp_path: Path):
    store_file = tmp_path / "app_usage.json"
    store = AppUsageStore(store_file)

    assert store.get_seconds_used("himanshu", "Chrome") == 0
    assert store.get_minutes_used("himanshu", "Chrome") == 0.0

    # Record 120 seconds of Chrome usage
    total_secs = store.record_usage(
        user="himanshu",
        app_key="chrome",
        app_name="Google Chrome",
        binary_pattern="google-chrome",
        exe_path="/opt/google/chrome/chrome",
        elapsed_seconds=120,
        daily_limit=60,
    )
    assert total_secs == 120
    assert store.get_seconds_used("himanshu", "chrome") == 120
    assert store.get_minutes_used("himanshu", "chrome") == 2.0

    # Persist and reload
    store.save()
    assert store_file.exists()

    new_store = AppUsageStore(store_file)
    assert new_store.get_seconds_used("himanshu", "chrome") == 120
    assert new_store.get_minutes_used("himanshu", "chrome") == 2.0


def test_app_usage_store_warning_milestones(tmp_path: Path):
    store_file = tmp_path / "app_usage.json"
    store = AppUsageStore(store_file)

    assert store.is_warning_sent("himanshu", "chrome", 10) is False
    store.mark_warning_sent("himanshu", "chrome", 10)
    assert store.is_warning_sent("himanshu", "chrome", 10) is True
    assert store.is_warning_sent("himanshu", "chrome", 5) is False


def test_app_usage_store_sync_records(tmp_path: Path):
    store_file = tmp_path / "app_usage.json"
    store = AppUsageStore(store_file)

    store.record_usage(
        user="himanshu",
        app_key="vlc",
        app_name="VLC Media Player",
        binary_pattern="vlc",
        exe_path="/usr/bin/vlc",
        elapsed_seconds=300,
        daily_limit=45,
    )

    records = store.get_usage_records_for_sync(device="optiplex-3050")
    assert len(records) == 1
    rec = records[0]
    assert rec.user == "himanshu"
    assert rec.app_name == "VLC Media Player"
    assert rec.device == "optiplex-3050"
    assert rec.minutes_used == 5.0
    assert rec.daily_limit_minutes == 45
    assert rec.remaining_minutes == 40.0
