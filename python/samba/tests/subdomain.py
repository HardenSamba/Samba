# Unix SMB/CIFS implementation.
#
# Tests for a child domain (subdomain) in a multi-domain forest
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

"""Tests run against the subdom_dc environment: a DC of a new child
domain (sub.samba.example.com) joined with
'samba-tool domain join ... SUBDOMAIN' to the forest of ad_dc_ntvfs
(samba.example.com).

They check the state the join leaves behind on both sides. Things
that are known not to work yet (Kerberos and NTLM across the
intra-forest trust, LSA lookups, the global catalog) are not tested
here yet.
"""

import ldb
import samba
from samba import dsdb
from samba.auth import system_session
from samba.credentials import Credentials
from samba.dcerpc import lsa, security
from samba.ndr import ndr_unpack
from samba.samdb import SamDB
from samba.tests import TestCase, env_get_var_value, env_loadparm


class SubdomainTests(TestCase):

    def setUp(self):
        super().setUp()
        self.lp = env_loadparm()

        # The child domain DC, with the Administrator of the child domain
        self.child_server = env_get_var_value("SUBDOM_DC_SERVER")
        child_creds = Credentials()
        child_creds.guess(self.lp)
        child_creds.set_username(env_get_var_value("USERNAME"))
        child_creds.set_password(env_get_var_value("PASSWORD"))
        child_creds.set_domain(env_get_var_value("DOMAIN"))
        self.child = SamDB(url="ldap://%s" % self.child_server,
                           session_info=system_session(),
                           credentials=child_creds, lp=self.lp)

        # The parent (forest root) domain DC, with its Administrator
        self.parent_server = env_get_var_value("PARENT_SERVER")
        parent_creds = Credentials()
        parent_creds.guess(self.lp)
        parent_creds.set_username(env_get_var_value("PARENT_USERNAME"))
        parent_creds.set_password(env_get_var_value("PARENT_PASSWORD"))
        parent_creds.set_domain(env_get_var_value("PARENT_DOMAIN"))
        parent_creds.set_realm(env_get_var_value("PARENT_REALM"))
        self.parent = SamDB(url="ldap://%s" % self.parent_server,
                            session_info=system_session(),
                            credentials=parent_creds, lp=self.lp)

        self.child_dn = self.child.get_default_basedn()
        self.parent_dn = self.parent.get_default_basedn()
        self.child_sid = security.dom_sid(self.child.get_domain_sid())
        self.parent_sid = security.dom_sid(self.parent.get_domain_sid())

    def _crossref(self, samdb, nc_dn):
        res = samdb.search(base=samdb.get_partitions_dn(),
                           scope=ldb.SCOPE_ONELEVEL,
                           expression="(&(objectClass=crossRef)(nCName=%s))" %
                           ldb.binary_encode(str(nc_dn)),
                           attrs=["nCName", "dnsRoot", "nETBIOSName",
                                  "trustParent", "systemFlags"])
        self.assertEqual(len(res), 1,
                         "no single crossRef for %s on %s" % (nc_dn, samdb.url))
        return res[0]

    def test_rootdse(self):
        """The child DC is in its own domain but in the parent's forest"""
        self.assertEqual(str(self.child_dn),
                         "DC=sub,%s" % self.parent_dn)
        self.assertEqual(str(self.child.get_root_basedn()),
                         str(self.parent_dn))
        self.assertEqual(str(self.child.get_config_basedn()),
                         str(self.parent.get_config_basedn()))
        self.assertEqual(str(self.child.get_schema_basedn()),
                         str(self.parent.get_schema_basedn()))
        self.assertNotEqual(self.child_sid, self.parent_sid)

    def test_crossref(self):
        """Both DCs know the crossRef of the child domain, which points to
        the crossRef of the parent domain as its trustParent"""
        parent_xref = self._crossref(self.parent, self.parent_dn)
        for samdb in (self.child, self.parent):
            xref = self._crossref(samdb, self.child_dn)
            self.assertEqual(str(xref["dnsRoot"][0]), "sub.samba.example.com")
            self.assertEqual(str(xref["nETBIOSName"][0]), "SAMBASUBDOM")
            self.assertEqual(str(xref["trustParent"][0]).lower(),
                             str(parent_xref.dn).lower())
            flags = int(xref["systemFlags"][0])
            self.assertTrue(flags & dsdb.SYSTEM_FLAG_CR_NTDS_NC)
            self.assertTrue(flags & dsdb.SYSTEM_FLAG_CR_NTDS_DOMAIN)

    def _tdo(self, samdb, partner):
        res = samdb.search(base="CN=System,%s" % samdb.get_default_basedn(),
                           scope=ldb.SCOPE_ONELEVEL,
                           expression="(&(objectClass=trustedDomain)(trustPartner=%s))" %
                           ldb.binary_encode(partner),
                           attrs=["trustAttributes", "trustDirection",
                                  "trustType", "securityIdentifier",
                                  "flatName"])
        self.assertEqual(len(res), 1,
                         "no trust to %s on %s" % (partner, samdb.url))
        return res[0]

    def test_intra_forest_trust(self):
        """The parent and the child have a two-way WITHIN_FOREST trust"""
        for samdb, partner, sid in ((self.child, "samba.example.com", self.parent_sid),
                                    (self.parent, "sub.samba.example.com", self.child_sid)):
            tdo = self._tdo(samdb, partner)
            attrs = int(tdo["trustAttributes"][0])
            self.assertTrue(attrs & lsa.LSA_TRUST_ATTRIBUTE_WITHIN_FOREST)
            self.assertEqual(int(tdo["trustDirection"][0]),
                             lsa.LSA_TRUST_DIRECTION_INBOUND |
                             lsa.LSA_TRUST_DIRECTION_OUTBOUND)
            self.assertEqual(int(tdo["trustType"][0]),
                             lsa.LSA_TRUST_TYPE_UPLEVEL)
            tdo_sid = ndr_unpack(security.dom_sid, tdo["securityIdentifier"][0])
            self.assertEqual(tdo_sid, sid)

    def test_no_forest_root_groups(self):
        """Enterprise Admins, Schema Admins and Enterprise Read-only
        Domain Controllers only exist in the forest root domain"""
        for rid in (security.DOMAIN_RID_ENTERPRISE_ADMINS,
                    security.DOMAIN_RID_SCHEMA_ADMINS,
                    security.DOMAIN_RID_ENTERPRISE_READONLY_DCS):
            child = self.child.search(base=self.child_dn,
                                      scope=ldb.SCOPE_SUBTREE,
                                      expression="(objectSid=%s-%d)" %
                                      (self.child_sid, rid),
                                      attrs=[])
            self.assertEqual(len(child), 0,
                             "RID %d must not exist in the child domain" % rid)
            parent = self.parent.search(base=self.parent_dn,
                                        scope=ldb.SCOPE_SUBTREE,
                                        expression="(objectSid=%s-%d)" %
                                        (self.parent_sid, rid),
                                        attrs=[])
            self.assertEqual(len(parent), 1,
                             "RID %d must exist in the forest root domain" % rid)

    def _assert_forest_sids(self, sd, where):
        """EA and SA in the descriptor must be the forest root groups"""
        sddl = sd.as_sddl()
        for rid in (security.DOMAIN_RID_ENTERPRISE_ADMINS,
                    security.DOMAIN_RID_SCHEMA_ADMINS):
            self.assertNotIn("%s-%d" % (self.child_sid, rid), sddl,
                             "%s refers to a child domain RID %d: %s" %
                             (where, rid, sddl))

    def _get_sd(self, dn):
        res = self.child.search(base=dn, scope=ldb.SCOPE_BASE,
                                attrs=["nTSecurityDescriptor"])
        return ndr_unpack(security.descriptor,
                          res[0]["nTSecurityDescriptor"][0])

    def test_domain_nc_descriptors(self):
        """The descriptors set by the provision of the child domain use the
        groups of the forest root domain"""
        sd = self._get_sd(self.child_dn)
        self._assert_forest_sids(sd, str(self.child_dn))
        # The domain root grants EA full control
        self.assertIn("%s-%d" % (self.parent_sid,
                                 security.DOMAIN_RID_ENTERPRISE_ADMINS),
                      sd.as_sddl())
        for rdn in ("CN=Users", "CN=Computers", "CN=System",
                    "CN=Builtin", "OU=Domain Controllers"):
            dn = "%s,%s" % (rdn, self.child_dn)
            self._assert_forest_sids(self._get_sd(dn), dn)

    def test_default_descriptor(self):
        """The schema defaultSecurityDescriptor of a new object is resolved
        with the forest root domain SID (descriptor.c)"""
        ou_dn = "OU=subdomain_test_default_sd,%s" % self.child_dn
        self.child.delete(ou_dn, ["tree_delete:1"]) if self._exists(ou_dn) else None
        self.child.add({"dn": ou_dn, "objectClass": "organizationalUnit"})
        self.addCleanup(self.child.delete, ou_dn, ["tree_delete:1"])
        self._assert_forest_sids(self._get_sd(ou_dn), ou_dn)

    def _exists(self, dn):
        try:
            self.child.search(base=dn, scope=ldb.SCOPE_BASE, attrs=[])
        except ldb.LdbError as e:
            if e.args[0] == ldb.ERR_NO_SUCH_OBJECT:
                return False
            raise
        return True
