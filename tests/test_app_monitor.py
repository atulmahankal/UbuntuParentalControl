"""Unit tests for process inspection and pattern matching."""

from parentalcontrol.app_monitor import ProcessInfo, is_system_process, matches_process
from parentalcontrol.models import AppLimitRule


def test_is_system_process():
    # GNOME shell / core daemons should be protected
    proc_gnome = ProcessInfo(pid=100, uid=1000, name="gnome-shell", exe="/usr/bin/gnome-shell")
    assert is_system_process(proc_gnome) is True

    proc_pipewire = ProcessInfo(pid=101, uid=1000, name="pipewire", exe="/usr/bin/pipewire")
    assert is_system_process(proc_pipewire) is True

    # User applications should not be treated as system processes
    proc_chrome = ProcessInfo(pid=200, uid=1000, name="chrome", exe="/opt/google/chrome/chrome")
    assert is_system_process(proc_chrome) is False

    proc_game = ProcessInfo(pid=201, uid=1000, name="retroarch", exe="/home/himanshu/Downloads/retroarch")
    assert is_system_process(proc_game) is False


def test_matches_process_by_comm():
    rule = AppLimitRule(
        user="himanshu",
        app_name="Chrome",
        patterns=["chrome", "google-chrome"],
    )
    proc = ProcessInfo(pid=300, uid=1000, name="chrome", exe="/opt/google/chrome/chrome")
    assert matches_process(proc, rule) is True

    proc_unrelated = ProcessInfo(pid=301, uid=1000, name="vlc", exe="/usr/bin/vlc")
    assert matches_process(proc_unrelated, rule) is False


def test_matches_process_by_path_glob():
    rule = AppLimitRule(
        user="himanshu",
        app_name="Downloads Binaries",
        patterns=["*/Downloads/*"],
    )
    proc_dl = ProcessInfo(pid=400, uid=1000, name="unknown_binary", exe="/home/himanshu/Downloads/game.x86_64")
    assert matches_process(proc_dl, rule) is True

    proc_opt = ProcessInfo(pid=401, uid=1000, name="calc", exe="/usr/bin/gnome-calculator")
    assert matches_process(proc_opt, rule) is False


def test_matches_process_by_appimage():
    rule = AppLimitRule(
        user="himanshu",
        app_name="Kdenlive",
        patterns=["*.AppImage", "*kdenlive*"],
    )
    # AppImages run mounted squashfs binaries at /tmp/.mount_kdenliveXXXXXX/usr/bin/kdenlive
    proc_appimage = ProcessInfo(
        pid=500,
        uid=1000,
        name="kdenlive",
        exe="/tmp/.mount_kdenliveAbCdEf/usr/bin/kdenlive",
        appimage_path="/home/himanshu/Applications/kdenlive-23.08.AppImage",
    )
    assert matches_process(proc_appimage, rule) is True


def test_matches_process_by_cmdline():
    rule = AppLimitRule(
        user="himanshu",
        app_name="Minecraft",
        patterns=["*minecraft.jar*", "minecraft"],
    )
    # Python/Java script where binary name is just 'java' or 'python3'
    proc_java = ProcessInfo(
        pid=600,
        uid=1000,
        name="java",
        exe="/usr/lib/jvm/java-17-openjdk/bin/java",
        cmdline=["java", "-Xmx2G", "-jar", "/home/himanshu/Games/minecraft.jar"],
    )
    assert matches_process(proc_java, rule) is True
