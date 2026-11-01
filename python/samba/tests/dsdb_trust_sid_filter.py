# Unix SMB/CIFS implementation.
#
# Tests for the SID filtering of the intra-forest trusts
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

"""Tests of dsdb_trust_filter_within_forest_sids(), the SID filtering of
the authorization data (PAC, NTLM pass-through) that comes from another
domain of the forest, see [MS-PAC] 4.1.2.2."""

from samba import dsdb, NTSTATUSError, ntstatus
from samba.dcerpc import lsa
from samba.tests import TestCase

CHILD = "S-1-5-21-1111-2222-3333"
ROOT = "S-1-5-21-4444-5555-6666"
OTHER = "S-1-5-21-7777-8888-9999"

WITHIN_FOREST = lsa.LSA_TRUST_ATTRIBUTE_WITHIN_FOREST
QUARANTINED = (lsa.LSA_TRUST_ATTRIBUTE_WITHIN_FOREST |
               lsa.LSA_TRUST_ATTRIBUTE_QUARANTINED_DOMAIN)


def filter_sids(attributes, trusted, sids):
    return dsdb._dsdb_trust_filter_within_forest_sids(attributes, trusted,
                                                      sids)


class TrustSidFilterTests(TestCase):

    user = ["%s-1104" % CHILD, "%s-513" % CHILD]

    def test_not_within_forest(self):
        """Other trusts are not filtered here"""
        sids = self.user + ["S-1-5-32-544", "S-1-1-0"]
        self.assertEqual(filter_sids(0, CHILD, sids), sids)

    def test_within_forest_keeps_forest_sids(self):
        """Inside the forest, the SIDs of every domain pass, privileged
        ones included (ForestSpecific), and so do S-1-5-9 (EDC), the
        claims SIDs and the asserted identity SIDs"""
        sids = self.user + ["%s-519" % ROOT, "%s-512" % ROOT,
                            "%s-1234" % OTHER, "S-1-5-9",
                            "S-1-5-21-0-0-0-497", "S-1-18-1",
                            "S-1-5-15", "S-1-5-1000-1"]
        self.assertEqual(filter_sids(WITHIN_FOREST, CHILD, sids), sids)

    def test_within_forest_always_filter(self):
        """The well-known, built-in, logon and partially formed SIDs never
        cross a trust"""
        always = ["S-1-0-0", "S-1-1-0", "S-1-2-0", "S-1-3-0",
                  "S-1-5-2", "S-1-5-4", "S-1-5-5-0-1234", "S-1-5-11",
                  "S-1-5-18", "S-1-5-32-544", "S-1-5-32-551",
                  "S-1-5-64-10", "S-1-5-21", "S-1-5-21-1-2",
                  ROOT, "%s-500-1" % ROOT, "S-1-6-1", "S-1-9-1"]
        sids = self.user + always + ["%s-1105" % ROOT]
        self.assertEqual(filter_sids(WITHIN_FOREST, CHILD, sids),
                         self.user + ["%s-1105" % ROOT])

    def test_quarantined_within_forest(self):
        """A quarantined parent-child trust only lets the SIDs of the
        trusted domain, S-1-5-9 and the never filtered SIDs pass"""
        sids = self.user + ["%s-1105" % CHILD, "%s-519" % ROOT,
                            "%s-1234" % OTHER, "S-1-5-9",
                            "S-1-5-21-0-0-0-497", "S-1-5-32-544"]
        self.assertEqual(filter_sids(QUARANTINED, CHILD, sids),
                         self.user + ["%s-1105" % CHILD, "S-1-5-9",
                                      "S-1-5-21-0-0-0-497"])

    def test_user_sid_filtered(self):
        """The user and its primary group can't be removed: the logon is
        refused"""
        sids = ["%s-1104" % ROOT, "%s-513" % ROOT]
        try:
            filter_sids(QUARANTINED, CHILD, sids)
            self.fail("a user of another domain passed a quarantined trust")
        except NTSTATUSError as e:
            self.assertEqual(e.args[0], ntstatus.NT_STATUS_ACCESS_DENIED)

        try:
            filter_sids(WITHIN_FOREST, CHILD, ["S-1-5-32-544",
                                               "%s-513" % CHILD])
            self.fail("a built-in SID passed as a user SID")
        except NTSTATUSError as e:
            self.assertEqual(e.args[0], ntstatus.NT_STATUS_ACCESS_DENIED)
