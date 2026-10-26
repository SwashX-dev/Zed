import pytest

from zedcore.auth import Policy, _process_groups
from zedcore.cli import _parse_value, build_parser
from zedcore.errors import ZedError


def test_parser_covers_documented_commands():
    p = build_parser()
    for argv in (
        ["install", "firefox"],
        ["install", "-c", "forensics", "-y"],
        ["remove", "firefox", "--dry-run"],
        ["search", "nmap"],
        ["info", "firefox"],
        ["list"],
        ["update"],
        ["upgrade", "-y"],
        ["apply", "--dry-run"],
        ["rollback"],
        ["rollback", "3"],
        ["generations"],
        ["doctor"],
        ["services"],
        ["services", "restart", "zed-sshd"],
        ["config"],
        ["config", "set", "firewall.enable", "true"],
        ["help"],
    ):
        args = p.parse_args(argv)
        assert args.command == argv[0]


def test_parse_values():
    b = {"key": "k", "type": "bool"}
    assert _parse_value(b, "on") is True
    assert _parse_value(b, "false") is False
    with pytest.raises(ZedError):
        _parse_value(b, "maybe")
    assert _parse_value({"key": "k", "type": "int"}, "5") == 5
    ports = {"key": "k", "type": "list", "items": {"type": "int"}}
    assert _parse_value(ports, "22, 443") == [22, 443]
    assert _parse_value(ports, "[80]") == [80]
    gov = {"key": "k", "type": "enum", "nullable": True}
    assert _parse_value(gov, "none") is None


def test_process_groups(tmp_path):
    (tmp_path / "42").mkdir()
    (tmp_path / "42" / "status").write_text("Name:\tx\nGroups:\t1 27 100\n")
    assert _process_groups(42, tmp_path) == {1, 27, 100}
    assert _process_groups(43, tmp_path) == set()


def test_policy_root_always_admin():
    from zedcore.auth import Peer

    assert Policy().is_admin(Peer(pid=1, uid=0, gid=0))
    assert not Policy().is_admin(Peer(pid=1, uid=1000, gid=100))
