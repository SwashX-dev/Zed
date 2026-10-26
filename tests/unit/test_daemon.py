import grp
import os
import socket
import threading

import pytest

from zedcore.auth import Policy
from zedcore.client import Client, DaemonUnavailable
from zedcore.daemon import Zedd, _Server
from zedcore.errors import InvalidInput, NotFound, PermissionDenied


def start(paths, nix, policy):
    zedd = Zedd(paths, policy)
    zedd.nix = nix
    zedd.engine.nix = nix
    sock = paths.socket
    server = _Server(str(sock), zedd)
    t = threading.Thread(target=server.serve_forever, daemon=True)
    t.start()
    return server, Client(sock)


@pytest.fixture
def my_group():
    return grp.getgrgid(os.getgid()).gr_name


def test_unauthorised_client_is_read_only(paths, nix):
    server, client = start(paths, nix, Policy(admin_group=None))
    try:
        if os.geteuid() == 0:
            pytest.skip("running as root: every caller is an administrator")
        assert client.call("ping")["admin"] is False
        assert client.call("list")["packages"] == []
        with pytest.raises(PermissionDenied):
            client.call("plan.create", {"changes": {"install": ["firefox"]}})
        with pytest.raises(PermissionDenied):
            client.call("services.control", {"name": "zed-sshd", "action": "stop"})
        with pytest.raises(NotFound):
            client.call("exec", {"argv": ["id"]})
    finally:
        server.shutdown()
        server.server_close()


def test_admin_group_round_trip(paths, nix, my_group):
    server, client = start(paths, nix, Policy(admin_group=my_group))
    events = []
    try:
        assert client.call("ping")["admin"] is True
        plan = client.call("plan.create", {"changes": {"install": ["hello"]}}, on_event=events.append)
        assert any(e.get("type") == "step" for e in events)
        assert plan["summary"] == ["install package hello 2.12"]
        res = client.call("plan.commit", {"id": plan["id"]})
        assert res["generation"] == 2
        assert client.call("list")["packages"] == ["hello"]
        gens = client.call("generations")["generations"]
        assert [g["number"] for g in gens] == [1, 2]
        assert gens[1]["packages"] == ["hello"]
        with pytest.raises(InvalidInput):
            client.call("plan.create", {"changes": {"install": ["$(reboot)"]}})
        schema = client.call("config.schema")
        assert any(s["key"] == "firewall.enable" for s in schema["settings"])
    finally:
        server.shutdown()
        server.server_close()


def test_malformed_requests(paths, nix):
    server, _ = start(paths, nix, Policy())
    try:
        for payload in (b"not json\n", b"[1,2]\n", b'{"method": 5}\n'):
            s = socket.socket(socket.AF_UNIX)
            s.connect(str(paths.socket))
            s.sendall(payload)
            reply = s.makefile("rb").readline()
            s.close()
            assert b'"invalid_input"' in reply
    finally:
        server.shutdown()
        server.server_close()


def test_daemon_unavailable(tmp_path):
    with pytest.raises(DaemonUnavailable):
        Client(tmp_path / "missing.sock").call("ping")
