# Licensed under the Apache License, Version 2.0 (the "License");
# Copyright 2024 Øivind Loe
# See LICENSE file or http://www.apache.org/licenses/LICENSE-2.0 for details.
# ~


class UnsupportedSpecVersionError(ValueError):
    """Stored spec version is newer than this Chief build supports."""


class SpecMigrationError(ValueError):
    """A spec migration step failed."""
