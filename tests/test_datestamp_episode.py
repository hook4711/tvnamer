#!/usr/bin/env python

"""Tests episodes based on dates, not season/episode numbers
"""

from functional_runner import run_tvnamer, verify_out_data
from helpers import attr
import pytest


@attr("functional")
def test_issue_56_dated_episode():
    """Season and episode should set correctly for date-parsed episodes
    """

    conf = """
    {"batch": true,
    "select_first": true,
    "filename_with_episode":  "%(seriesname)s %(date)s - %(episodename)s%(ext)s"}
    """

    out_data = run_tvnamer(
        with_files = ['tonight.show.conan.2009.06.05.hdtv.blah.avi'],
        with_config = conf)

    expected_files = ['The Tonight Show with Conan O\'Brien - [2009-06-05] - Ryan Seacrest, Patton Oswalt, Chickenfoot.avi']

    verify_out_data(out_data, expected_files)


@attr("functional")
@pytest.mark.xfail(reason="dependant on episode objects all having consistent data - issue #125")
def test_date_in_s01e01_out():
    """File with date-stamp, outputs s01e01-ish name
    """

    conf = """
    {"always_rename": true,
    "select_first": true,
    "filename_with_date_and_episode": "%(seriesname)s - [%(seasonnumber)02dx%(episode)s] - %(episodename)s%(ext)s"}
    """

    out_data = run_tvnamer(
        with_files = ['scrubs.2001.10.02.avi'],
        with_config = conf,
        with_input = "")

    expected_files = ['Scrubs - [01x01] - My First Day.avi']

    verify_out_data(out_data, expected_files)


def test_issue_31_twochar_year():
    """Fix for parsing rather ambigious dd.mm.yy being parsed as "0011"
    """

    from tvnamer.files import intepret_year

    assert intepret_year("99") == 1999
    assert intepret_year("79") == 1979

    assert intepret_year("00") == 2000
    assert intepret_year("20") == 2020
