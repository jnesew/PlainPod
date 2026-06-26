from __future__ import annotations

from .credentials import CredentialService
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .settings import AppSettings
from .sync_server import SyncServerConfig


class SyncServerConfigurationError(RuntimeError):
    pass


def build_sync_server_config(
    app_settings: "AppSettings",
    credential_service: CredentialService | None = None,
) -> SyncServerConfig:
    if not app_settings.sync_server_enabled:
        return SyncServerConfig(enabled=False)

    env_config = SyncServerConfig.from_env()
    password = None
    if app_settings.sync_server_require_auth:
        password = env_config.password
        if password is None:
            credential_service = credential_service or CredentialService()
            password = credential_service.get_password(app_settings.sync_server_username)
        if password is None:
            raise SyncServerConfigurationError(
                "Local sync authentication is enabled, but no password is configured."
            )

    return SyncServerConfig(
        host=app_settings.sync_server_host,
        port=app_settings.sync_server_port,
        username=app_settings.sync_server_username,
        password=password,
        enabled=env_config.enabled,
    )
