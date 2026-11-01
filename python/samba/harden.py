# The Harden-Box options of smb.conf
#
# Copyright (C) HardenSamba contributors 2026
#
# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful,
# but WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
# GNU General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program.  If not, see <http://www.gnu.org/licenses/>.
#

"""The "harden:" options of smb.conf.

Every feature added by the Harden-Box branches is off by default: with
none of these options set, the DC behaves as an upstream Samba DC.
"""

MULTI_DOMAIN_FOREST = "harden:multi-domain forest"


def lp_bool(lp, name, default=False):
    """The boolean value of the (parametric) option name in lp."""
    if lp is None:
        return default
    value = lp.get(name)
    if value is None:
        return default
    if isinstance(value, bool):
        return value
    value = str(value).strip().lower()
    if value in ("yes", "true", "on", "1"):
        return True
    if value in ("no", "false", "off", "0"):
        return False
    return default


def multi_domain_forest(lp):
    """Is the support of multi-domain forests enabled?"""
    return lp_bool(lp, MULTI_DOMAIN_FOREST)
