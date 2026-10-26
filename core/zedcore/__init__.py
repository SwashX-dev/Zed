"""Zed system management core.

zedcore holds everything that changes a Zed system: the managed-state model,
input validation, the configuration transaction engine, and zedd, the
privileged daemon that runs it.  The `zed` CLI and the desktop applications
are clients of zedd and never modify the system themselves.
"""

__version__ = "0.1.0a1"
