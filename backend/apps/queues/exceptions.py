# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~


class QueueCommandError(Exception):
    """Base class for queue command failures."""


class QueueValidationError(QueueCommandError, ValueError):
    """Raised when command input fails validation."""


class QueuePayloadTooLargeError(QueueValidationError):
    """Raised when a put payload exceeds the JSON size limit."""


class QueueItemNotFoundError(QueueCommandError, LookupError):
    """Raised when a queue item does not exist."""


class QueueItemStateError(QueueCommandError):
    """Raised when an item is not in the expected lifecycle state."""


class QueueNotTakerError(QueueItemStateError):
    """Raised when complete/fail is invoked by a non-taker session."""
