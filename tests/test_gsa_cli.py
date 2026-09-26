"""Tests for GSA key validation, installation, and recheck CLI commands."""

import json
import argparse
from pathlib import Path
from unittest.mock import MagicMock, patch

from parentalcontrol.config import AppConfig
from parentalcontrol.sheet_client import GoogleSheetClient, validate_service_account_file
from parentalcontrol.cli import cmd_gsa, cmd_recheck


def test_validate_service_account_file_success(tmp_path: Path):
    key_file = tmp_path / "valid.json"
    key_data = {
        "type": "service_account",
        "client_email": "bot@project.iam.gserviceaccount.com",
        "private_key": "-----BEGIN PRIVATE KEY-----\nMIIEvgIBADANBgkqhkiG9w0BAQEF\n-----END PRIVATE KEY-----\n",
    }
    with open(key_file, "w", encoding="utf-8") as f:
        json.dump(key_data, f)

    valid, email, data = validate_service_account_file(key_file)
    assert valid is True
    assert email == "bot@project.iam.gserviceaccount.com"
    assert data["type"] == "service_account"


def test_validate_service_account_file_invalid(tmp_path: Path):
    # Non-existent file
    valid, err, _ = validate_service_account_file(tmp_path / "missing.json")
    assert valid is False
    assert "does not exist" in err

    # Invalid type
    bad_type = tmp_path / "bad.json"
    with open(bad_type, "w", encoding="utf-8") as f:
        json.dump({"type": "authorized_user"}, f)
    valid, err, _ = validate_service_account_file(bad_type)
    assert valid is False
    assert "expected 'service_account'" in err


def test_cmd_gsa_install(tmp_path: Path, monkeypatch):
    source_key = tmp_path / "source_key.json"
    with open(source_key, "w", encoding="utf-8") as f:
        json.dump({
            "type": "service_account",
            "client_email": "test-gsa@project.iam.gserviceaccount.com",
            "private_key": "dummy-key",
        }, f)

    # Direct config and dest_dir to tmp_path
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr("os.geteuid", lambda: 1000)

    cfg = AppConfig()
    cfg.google_sheet.url = "https://docs.google.com/spreadsheets/d/dummy/edit"
    cfg.config_file_path = tmp_path / "config.yaml"

    args = argparse.Namespace(file=str(source_key), url=None, recheck=False)

    with patch("parentalcontrol.cli._verify_or_create_spreadsheet") as mock_verify, \
         patch("parentalcontrol.cli.save_config") as mock_save:
        cmd_gsa(args, cfg)
        mock_verify.assert_called_once()
        mock_save.assert_called_once()

    installed_key = tmp_path / ".config" / "parental-control" / "service_account.json"
    assert installed_key.exists()
    mode = oct(installed_key.stat().st_mode)[-3:]
    assert mode == "600"


def test_ensure_default_worksheets_creates_missing():
    client = GoogleSheetClient(sheet_url="https://docs.google.com/spreadsheets/d/test/edit")

    mock_sh = MagicMock()
    # Initially only Sheet1 exists
    mock_ws = MagicMock()
    mock_ws.title = "Screen Time"
    mock_sh.worksheets.return_value = [mock_ws]

    status = client.ensure_default_worksheets(mock_sh, create_missing=True)
    assert status["Screen Time"] is True
    assert status["Apps Limit"] is True
    assert status["Apps Usages"] is True
    assert mock_sh.add_worksheet.call_count == 2
