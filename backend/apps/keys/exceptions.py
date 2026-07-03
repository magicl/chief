# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~


class KeyNotFoundError(LookupError):
    """Raised when a credential name does not exist or is not set."""


class KeyValidationError(ValueError):
    """Raised when command input fails validation."""


class KeyTypeMismatchError(ValueError):
    """Raised when a key_ref type does not match the expected consumer type."""


class KeyStorageMisconfiguredError(RuntimeError):
    """Raised when ciphertext cannot be decrypted (e.g. rotated CREDENTIALS_KEY)."""
