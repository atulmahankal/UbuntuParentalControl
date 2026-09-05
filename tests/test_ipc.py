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

    finally:
        server.stop()
