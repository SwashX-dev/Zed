"""Errors that cross the zedd API boundary.

Every error carries a stable machine-readable code so clients can react
(prompt for a password, offer a retry) without parsing messages.
"""

from __future__ import annotations


class ZedError(Exception):
    code = "error"

    def __init__(self, message: str, *, detail: str | None = None, hint: str | None = None):
        super().__init__(message)
        self.message = message
        self.detail = detail
        self.hint = hint

    def to_dict(self) -> dict:
        d = {"code": self.code, "message": self.message}
        if self.detail:
            d["detail"] = self.detail
        if self.hint:
            d["hint"] = self.hint
        return d


class InvalidInput(ZedError):
    code = "invalid_input"


class NotFound(ZedError):
    code = "not_found"


class AuthRequired(ZedError):
    code = "auth_required"


class PermissionDenied(ZedError):
    code = "permission_denied"


class Busy(ZedError):
    code = "busy"


class Conflict(ZedError):
    """The system changed since a plan was made, or settings conflict."""

    code = "conflict"


class BuildFailed(ZedError):
    code = "build_failed"


class ActivationFailed(ZedError):
    code = "activation_failed"


class Unsupported(ZedError):
    code = "unsupported"


class Cancelled(ZedError):
    code = "cancelled"


ERRORS_BY_CODE = {
    cls.code: cls
    for cls in (
        ZedError,
        InvalidInput,
        NotFound,
        AuthRequired,
        PermissionDenied,
        Busy,
        Conflict,
        BuildFailed,
        ActivationFailed,
        Unsupported,
        Cancelled,
    )
}


def from_dict(d: dict) -> ZedError:
    cls = ERRORS_BY_CODE.get(d.get("code", "error"), ZedError)
    return cls(d.get("message", "unknown error"), detail=d.get("detail"), hint=d.get("hint"))
