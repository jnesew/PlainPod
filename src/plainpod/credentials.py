from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

SERVICE_NAME = "io.github.jnesew.PlainPod.LocalSync"


class CredentialService:
    """Small wrapper around Secret Service/keyring for local sync passwords."""

    def __init__(self, service_name: str = SERVICE_NAME) -> None:
        self.service_name = service_name

    def is_available(self) -> bool:
        try:
            import keyring  # type: ignore
            from keyring.errors import KeyringError  # type: ignore

            keyring.get_password(self.service_name, "__plainpod_availability_check__")
            return True
        except (ImportError, KeyringError, RuntimeError) as exc:
            logger.debug("Local sync credential storage is unavailable: %s", exc)
            return False

    def get_password(self, account: str) -> str | None:
        try:
            import keyring  # type: ignore
            from keyring.errors import KeyringError  # type: ignore

            return keyring.get_password(self.service_name, account) or None
        except (ImportError, KeyringError, RuntimeError) as exc:
            logger.warning("Unable to read local sync password from keyring: %s", exc)
            return None

    def set_password(self, account: str, password: str) -> None:
        import keyring  # type: ignore

        keyring.set_password(self.service_name, account, password)

    def delete_password(self, account: str) -> None:
        try:
            import keyring  # type: ignore
            from keyring.errors import KeyringError, PasswordDeleteError  # type: ignore

            keyring.delete_password(self.service_name, account)
        except (ImportError, KeyringError, PasswordDeleteError, RuntimeError) as exc:
            logger.debug("Unable to delete local sync password from keyring: %s", exc)
