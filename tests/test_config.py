import pytest
from pathlib import Path
from parentalcontrol.config import AppConfig, GoogleSheetConfig, RulesConfig, load_config, save_config

def test_config_save_and_load(tmp_path):
    cfg_file = tmp_path / "config.yaml"
    cfg = AppConfig(
        google_sheet=GoogleSheetConfig(url="https://docs.google.com/spreadsheets/d/12345/edit"),
        rules=RulesConfig(target_users=["child1", "child2"], exempt_users=["atul", "root"]),
        config_file_path=cfg_file,
    )
    save_config(cfg, cfg_file)
    assert cfg_file.exists()

    loaded = load_config(cfg_file)
    assert loaded.google_sheet.url == "https://docs.google.com/spreadsheets/d/12345/edit"
    assert loaded.rules.target_users == ["child1", "child2"]
    assert loaded.rules.exempt_users == ["atul", "root"]
    assert loaded.is_user_targeted("child1") is True
    assert loaded.is_user_targeted("atul") is False

def test_user_targeting():
    cfg = AppConfig(
        rules=RulesConfig(target_users=["alex"], exempt_users=["parent", "root"])
    )
    assert cfg.is_user_targeted("alex") is True
    assert cfg.is_user_targeted("parent") is False
    assert cfg.is_user_targeted("root") is False
    assert cfg.is_user_targeted("someone_else") is False


def test_system_users_never_targeted():
    # Even if target_users is '*', system users like gdm, lightdm, root must NEVER be targeted
    cfg = AppConfig(
        rules=RulesConfig(target_users=["*"], exempt_users=["parent"])
    )
    assert cfg.is_user_targeted("gdm") is False
    assert cfg.is_user_targeted("gdm3") is False
    assert cfg.is_user_targeted("lightdm") is False
    assert cfg.is_user_targeted("sddm") is False
    assert cfg.is_user_targeted("root") is False
    assert cfg.is_user_targeted("nobody") is False
    assert cfg.is_user_targeted("daemon") is False


def test_has_wildcard_target_users():
    cfg_wildcard = AppConfig(rules=RulesConfig(target_users=["*"]))
    assert cfg_wildcard.has_wildcard_target_users() is True

    cfg_all = AppConfig(rules=RulesConfig(target_users=["all"]))
    assert cfg_all.has_wildcard_target_users() is True

    cfg_empty = AppConfig(rules=RulesConfig(target_users=[]))
    assert cfg_empty.has_wildcard_target_users() is True

    cfg_explicit = AppConfig(rules=RulesConfig(target_users=["himanshu", "himanshi"]))
    assert cfg_explicit.has_wildcard_target_users() is False
