#!/usr/bin/env python

import re
from .parse import PTN

__author__ = "Giorgio Momigliano"
__email__ = "gmomigliano@protonmail.com"
__version__ = "2.8.2"
__license__ = "MIT"


def parse(name: str, standardise: bool = True, coherent_types: bool = False) -> dict:
    """
    Parse the torrent title into its components.

    :param name: The torrent name to parse.
    :param standardise: Whether to standardise the parsed values.
    :param coherent_types: Whether to ensure coherent types in the parsed results.
    :return: A dictionary of parsed components.
    """
    return PTN().parse(name, standardise, coherent_types)
