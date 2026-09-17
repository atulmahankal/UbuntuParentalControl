import time
from unittest.mock import patch
from parentalcontrol.ipc import ParentalControlIPCServer, send_ipc_request

def test_ipc_server_ping_and_override(tmp_path, monkeypatch):
    monkeypatch.setenv("PARENTAL_CONTROL_OVERRIDES_FILE", str(tmp_path / "test_overrides.json"))
    sock_path = tmp_path / "test.sock"
    server = ParentalControlIPCServer(
        exempt_users=["atul", "parent"],
        socket_path=sock_path,
    )
    server.start()
    try:
        # Test ping
        res = send_ipc_request({"action": "ping"}, socket_path=sock_path)
        assert res["success"] is True
        assert res["message"] == "pong"

        # Test unauthorized parent rejection
        res_unauth = send_ipc_request({
            "action": "authenticate_override",
            "child_user": "himanshu",
            "parent_user": "stranger",
            "password": "secret",
            "duration_minutes": 30,
        }, socket_path=sock_path)
        assert res_unauth["success"] is False
        assert "not an authorized" in res_unauth["error"]

        # Test successful authentication with mock PAM
        with patch("pam.pam.authenticate", return_value=True):
            res_auth = send_ipc_request({
                "action": "authenticate_override",
                "child_user": "himanshu",
                "parent_user": "atul",
                "password": "good_password",
                "duration_minutes": 45,
            }, socket_path=sock_path)
            assert res_auth["success"] is True
            assert res_auth["duration_minutes"] == 45

        # Test poweroff request
        poweroff_called = [False]
        server.on_poweroff = lambda: poweroff_called.__setitem__(0, True)
        res_power = send_ipc_request({"action": "poweroff_request"}, socket_path=sock_path)
        assert res_power["success"] is True
        assert poweroff_called[0] is True

        # Test 5m extension status and request
        ext_file = tmp_path / "test_extensions_5m.json"
        monkeypatch.setenv("PARENTAL_CONTROL_EXTENSIONS_FILE", str(ext_file))
        res_ext_check = send_ipc_request({
            "action": "check_5m_extension_status",
            "child_user": "himanshu",
        }, socket_path=sock_path)
        assert res_ext_check["success"] is True
        assert res_ext_check["can_extend"] is True

        # Request extension
        res_ext_req = send_ipc_request({
            "action": "request_5m_extension",
            "child_user": "himanshu",
        }, socket_path=sock_path)
        assert res_ext_req["success"] is True
        assert res_ext_req["duration_minutes"] == 5

        # Second request should fail
        res_ext_req2 = send_ipc_request({
            "action": "request_5m_extension",
            "child_user": "himanshu",
        }, socket_path=sock_path)
        assert res_ext_req2["success"] is False
        assert "already been used" in res_ext_req2["error"]

    finally:
        server.stop()

