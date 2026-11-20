#!/usr/bin/env python3
"""
export_name.py - the one rule for the appraiser's name on his learning exports.

setup_workspace.py --name saves it; export_learning.py reads it back and checks
it again, because the name goes into the zip's file name.
"""
from __future__ import annotations

import re

# His name, as setup saves it: dcohen or d-cohen.
NAME_PATTERN = r"[a-z0-9]+(-[a-z0-9]+)*"
NAME_RULE_MESSAGE = "name looks like dcohen or d-cohen (lowercase letters and digits, hyphens between)"
# A contributor code saved by 0.7.0's `export_learning.py init`, like A-001.
OLD_CONTRIBUTOR_PATTERN = r"[A-Z]-\d{3}"


def is_valid_name(name: str) -> bool:
    """True for a name setup accepts."""
    return re.fullmatch(NAME_PATTERN, name) is not None


def is_valid_saved_name(name: str) -> bool:
    """True for a name setup accepts, or a 0.7.0 contributor code."""
    return is_valid_name(name) or re.fullmatch(OLD_CONTRIBUTOR_PATTERN, name) is not None
