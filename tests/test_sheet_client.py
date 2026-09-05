import pytest
from datetime import time
from parentalcontrol.sheet_client import (
    extract_sheet_id_and_gid,
    convert_to_csv_export_url,
    parse_time_str,
    parse_duration_minutes,
    parse_boolean_str,
    GoogleSheetClient,
)

def test_extract_sheet_id_and_gid():
    url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit?usp=sharing#gid=12345"
    sheet_id, gid = extract_sheet_id_and_gid(url)
    assert sheet_id == "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms"
    assert gid == "12345"

def test_convert_to_csv_export_url():
    url = "https://docs.google.com/spreadsheets/d/1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms/edit"
    csv_url = convert_to_csv_export_url(url)
    assert "gviz/tq?tqx=out:csv" in csv_url
    assert "1BxiMVs0XRA5nFMdKvBdBZjgmUUqptlbs74OgvE2upms" in csv_url

def test_parse_time_str():
    assert parse_time_str("16:30") == time(16, 30)
    assert parse_time_str("4:30 PM") == time(16, 30)
    assert parse_time_str("4:30pm") == time(16, 30)
    assert parse_time_str("9:15 AM") == time(9, 15)
    assert parse_time_str("00:00") == time(0, 0)
    assert parse_time_str("12:00 PM") == time(12, 0)
    assert parse_time_str("12:00 AM") == time(0, 0)

def test_parse_duration_minutes():
    assert parse_duration_minutes("120") == 120
    assert parse_duration_minutes("2h") == 120
    assert parse_duration_minutes("1.5 hours") == 90
    assert parse_duration_minutes("45m") == 45
    assert parse_duration_minutes("30 mins") == 30

def test_parse_boolean_str():
    assert parse_boolean_str("TRUE") is True
    assert parse_boolean_str("yes") is True
    assert parse_boolean_str("1") is True
    assert parse_boolean_str("FALSE") is False
    assert parse_boolean_str("no") is False
    assert parse_boolean_str("0") is False
    assert parse_boolean_str("blocked") is False

def test_parse_csv_content(tmp_path):
    csv_data = """Child,Days,From,To,Permitted,Daily Quota,Notes
alex,Monday-Friday,16:00,20:00,TRUE,120,Homework screen time
alex,Saturday-Sunday,10:00,13:00,TRUE,180,Weekend morning
sam,All,17:00,19:30,TRUE,90,Evening only
*,*,21:00,06:00,FALSE,,Overnight lock
"""
    client = GoogleSheetClient(sheet_url="", cache_path=tmp_path / "cache.json")
    rules = client._parse_csv_content(csv_data)
    assert len(rules) == 4
    
    r0 = rules[0]
    assert r0.user == "alex"
    assert r0.day == "Monday-Friday"
    assert r0.start_time == time(16, 0)
    assert r0.end_time == time(20, 0)
    assert r0.allowed is True
    assert r0.max_minutes == 120
    assert r0.message == "Homework screen time"

    r3 = rules[3]
    assert r3.user == "*"
    assert r3.allowed is False

def test_parse_csv_with_device_column():
    csv_data = """User,Device,Day,Start Time,End Time,Allowed,Max Minutes,Message
himanshu,optiplex-3050,Monday,16:00,20:00,TRUE,120,Desktop study
himanshu,,Tuesday,16:00,20:00,TRUE,120,All devices
himanshi,*,Wednesday,16:00,20:00,TRUE,120,All devices explicit star
"""
    client = GoogleSheetClient(sheet_url="")
    rules = client._parse_csv_content(csv_data)
    assert len(rules) == 3
    assert rules[0].user == "himanshu"
    assert rules[0].device == "optiplex-3050"
    # Empty device column parses as '*'
    assert rules[1].user == "himanshu"
    assert rules[1].device == "*"
    # Star device column parses as '*'
    assert rules[2].user == "himanshi"
    assert rules[2].device == "*"
