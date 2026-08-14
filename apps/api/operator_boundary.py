from __future__ import annotations

import re
from typing import Protocol

from packages.identity import IdentityService, RuntimeOperatorStatus

from .app_types import JsonObject

_SERVICE_NAME = re.compile(r"^[a-z][a-z0-9_-]{0,63}$")
_SAFE_VERSION = re.compile(r"^[A-Za-z0-9._-]+$")
_ERROR_CODE = re.compile(r"^[A-Z][A-Z0-9_]{0,127}$")
_SERVICE_STATUSES = {
    "RUNNING",
    "SUCCEEDED",
    "PARTIAL",
    "RATE_LIMITED",
    "AUTH_REQUIRED",
    "FAILED",
}


class OperatorStatusSource(Protocol):
    def read_status(self) -> RuntimeOperatorStatus: ...


class StaticOperatorStatusSource:
    def __init__(self, status: RuntimeOperatorStatus) -> None:
        self._status = status

    def read_status(self) -> RuntimeOperatorStatus:
        return self._status


class OperatorBoundary:
    """Role gate plus an explicit allowlist projection for operator data."""

    def __init__(
        self,
        *,
        identity_service: IdentityService,
        status_source: OperatorStatusSource,
    ) -> None:
        self._identity_service = identity_service
        self._status_source = status_source

    def status(self, session_token: str | None) -> JsonObject:
        self._identity_service.require_operator(session_token)
        runtime = self._status_source.read_status()
        self._validate_runtime_status(runtime)
        return {
            "deploymentVersion": runtime.deployment_version,
            "commit": runtime.commit,
            "startedAt": runtime.started_at,
            "services": [
                {
                    "name": service.name,
                    "status": service.status,
                    "lastSucceededAt": service.last_succeeded_at,
                    "errorCode": service.error_code,
                }
                for service in runtime.services
            ],
        }

    @staticmethod
    def _validate_runtime_status(runtime: RuntimeOperatorStatus) -> None:
        if not 1 <= len(runtime.deployment_version) <= 128:
            raise ValueError("unsafe deployment version")
        if _SAFE_VERSION.fullmatch(runtime.deployment_version) is None:
            raise ValueError("unsafe deployment version")
        if not 7 <= len(runtime.commit) <= 128 or _SAFE_VERSION.fullmatch(runtime.commit) is None:
            raise ValueError("unsafe commit identifier")
        if len(runtime.services) > 100:
            raise ValueError("too many operator service records")
        for service in runtime.services:
            if _SERVICE_NAME.fullmatch(service.name) is None:
                raise ValueError("unsafe operator service name")
            if service.status not in _SERVICE_STATUSES:
                raise ValueError("unsafe operator service status")
            if service.error_code is not None and _ERROR_CODE.fullmatch(service.error_code) is None:
                raise ValueError("unsafe operator error code")
