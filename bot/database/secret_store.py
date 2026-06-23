"""
Local secret store for encrypting credentials at rest.

Uses Fernet (AES-128-CBC + HMAC-SHA256) authenticated encryption with a
locally generated master key.

Security boundary
-----------------
The default local key file (``.master_key``) is stored in the application
data volume alongside the database.  This protects against database-only
exposure and accidental disclosure of the data file, but **does not** protect
against an attacker with full access to the application data volume.

For stronger protection, store the master key outside the application volume
and point to it with the ``MASTER_KEY_FILE`` environment variable, or use a
Docker secret mounted at ``/run/secrets/master_key``.
"""

import logging
import os
import stat

from cryptography.fernet import Fernet

logger = logging.getLogger(__name__)

DEFAULT_KEY_PERMISSIONS = 0o600


class SecretStoreError(Exception):
    """Raised when a secret-store operation fails."""


class SecretStore:
    """Manages a local Fernet master key and provides encrypt/decrypt."""

    def __init__(self, key_path: str):
        """
        Parameters
        ----------
        key_path:
            Absolute or relative path to the master key file.
        """
        self._key_path = key_path
        self._fernet: Fernet | None = None

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    def initialize(self) -> bool:
        """Load an existing key or generate a new one.

        Returns
        -------
        bool
            ``True`` when a new key was generated (first run).
        """
        if os.path.isfile(self._key_path):
            self._load_key()
            return False

        self._generate_key()
        return True

    def _generate_key(self) -> None:
        """Generate a new Fernet key and persist it with restrictive permissions."""
        key = Fernet.generate_key()
        dir_path = os.path.dirname(os.path.abspath(self._key_path))
        os.makedirs(dir_path, exist_ok=True)

        # Create the file exclusively so we never overwrite an existing key.
        fd = os.open(self._key_path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, DEFAULT_KEY_PERMISSIONS)
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(key)
        except BaseException:
            os.close(fd)
            raise

        self._fernet = Fernet(key)
        logger.info("Generated new master key at %s", self._key_path)

    def _load_key(self) -> None:
        """Load an existing master key from disk."""
        if not os.path.isfile(self._key_path):
            raise SecretStoreError(f"Master key file not found: {self._key_path}")

        st = os.stat(self._key_path)
        if st.st_mode & 0o077:
            logger.warning(
                "Master key file %s has overly permissive permissions (%o); "
                "consider restricting to 600",
                self._key_path,
                st.st_mode & 0o777,
            )

        with open(self._key_path, "rb") as f:
            key_data = f.read()

        try:
            self._fernet = Fernet(key_data)
        except Exception as exc:
            raise SecretStoreError(
                f"Invalid master key file at {self._key_path}: {exc}"
            ) from exc

        logger.debug("Loaded master key from %s", self._key_path)

    # ------------------------------------------------------------------
    # Properties
    # ------------------------------------------------------------------

    @property
    def key_available(self) -> bool:
        """``True`` when the store is initialized and ready."""
        return self._fernet is not None

    @property
    def key_path(self) -> str:
        """Filesystem path to the master key file."""
        return self._key_path

    # ------------------------------------------------------------------
    # Encryption / decryption
    # ------------------------------------------------------------------

    def encrypt(self, plaintext: str) -> str:
        """Encrypt *plaintext* and return a base64-encoded Fernet token.

        Raises
        ------
        SecretStoreError
            If the store has not been initialized.
        """
        if self._fernet is None:
            raise SecretStoreError("SecretStore has not been initialized")
        return self._fernet.encrypt(plaintext.encode()).decode()

    def decrypt(self, ciphertext: str) -> str:
        """Decrypt a Fernet token and return the original plaintext.

        Raises
        ------
        SecretStoreError
            If the store has not been initialized or the token is invalid.
        """
        if self._fernet is None:
            raise SecretStoreError("SecretStore has not been initialized")
        try:
            return self._fernet.decrypt(ciphertext.encode()).decode()
        except Exception as exc:
            raise SecretStoreError(f"Decryption failed: {exc}") from exc
