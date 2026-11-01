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

They check the state the join leaves behind on both sides, and that
the two domains work together across the intra-forest trust:
Kerberos referrals, NTLM pass-through, LSA name and SID lookups, the
forest root groups in the child domain, DRS replication in both
directions and the partial replica of the child domain on the global
catalog of the forest root.

Phase 3: the forest partitions on the child DC, the global catalog of
the child domain, the backlinks on the partial replicas, the members of
a group from another domain and the universal groups in the tokens,
Netlogon (DsrEnumerateDomainTrusts), the domain naming master and the
infrastructure master.
"""

import ldb
import samba
from samba import dsdb, drs_utils
from samba.auth import system_session
from samba.credentials import (Credentials, DONT_USE_KERBEROS,
                               MUST_USE_KERBEROS)
from samba.dcerpc import drsuapi, lsa, netlogon, security
from samba.infrastructure import InfrastructureUpdate
import uuid
from samba.ndr import ndr_unpack
from samba.samdb import SamDB
from samba.tests import TestCase, env_get_var_value, env_loadparm


class SubdomainTests(TestCase):

    def setUp(self):
        super().setUp()
        self.lp = env_loadparm()

        # The child domain DC and its Administrator
        self.child_server = env_get_var_value("SUBDOM_DC_SERVER")
        self.child_realm = env_get_var_value("REALM")
        self.child_domain = env_get_var_value("DOMAIN")
        self.child_fqdn = "%s.%s" % (self.child_server,
                                     self.child_realm.lower())
        self.child_account = (env_get_var_value("USERNAME"),
                              env_get_var_value("PASSWORD"),
                              self.child_domain,
                              self.child_realm)

        # The parent (forest root) domain DC and its Administrator
        self.parent_server = env_get_var_value("PARENT_SERVER")
        self.parent_realm = env_get_var_value("PARENT_REALM")
        self.parent_domain = env_get_var_value("PARENT_DOMAIN")
        self.parent_fqdn = "%s.%s" % (self.parent_server,
                                      self.parent_realm.lower())
        self.parent_account = (env_get_var_value("PARENT_USERNAME"),
                               env_get_var_value("PARENT_PASSWORD"),
                               self.parent_domain,
                               self.parent_realm)

        self.child = self._connect(self.child_fqdn, self.child_account)
        self.parent = self._connect(self.parent_fqdn, self.parent_account)

        self.child_dn = self.child.get_default_basedn()
        self.parent_dn = self.parent.get_default_basedn()
        self.child_sid = security.dom_sid(self.child.get_domain_sid())
        self.parent_sid = security.dom_sid(self.parent.get_domain_sid())

    def _creds(self, account, kerberos_state=None):
        (username, password, domain, realm) = account
        creds = Credentials()
        creds.guess(self.lp)
        creds.set_username(username)
        creds.set_password(password)
        creds.set_domain(domain)
        creds.set_realm(realm)
        if kerberos_state is not None:
            creds.set_kerberos_state(kerberos_state)
        return creds

    def _connect(self, host, account, kerberos_state=None, port=None):
        url = "ldap://%s" % host
        if port is not None:
            url += ":%d" % port
        return SamDB(url=url,
                     session_info=system_session(),
                     credentials=self._creds(account, kerberos_state),
                     lp=self.lp)

    def _token_sids(self, samdb):
        res = samdb.search(base="", scope=ldb.SCOPE_BASE,
                           attrs=["tokenGroups"])
        return [str(ndr_unpack(security.dom_sid, v))
                for v in res[0]["tokenGroups"]]

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

    def _cross_domain_binds(self, kerberos_state):
        """Each Administrator binds to the DC of the other domain, and is
        known there as a member of the Domain Users of its own domain"""
        for account, sid in ((self.child_account, self.child_sid),
                             (self.parent_account, self.parent_sid)):
            for host in (self.child_fqdn, self.parent_fqdn):
                samdb = self._connect(host, account, kerberos_state)
                self.assertIn("%s-%d" % (sid, security.DOMAIN_RID_USERS),
                              self._token_sids(samdb),
                              "%s\\%s on %s" % (account[2], account[0], host))

    def test_kerberos_cross_domain(self):
        """Kerberos works in both directions across the intra-forest
        trust (cross-realm referrals of the KDCs)"""
        self._cross_domain_binds(MUST_USE_KERBEROS)

    def test_ntlm_cross_domain(self):
        """NTLM works in both directions across the intra-forest trust
        (pass-through authentication through winbindd and netlogon)"""
        self._cross_domain_binds(DONT_USE_KERBEROS)

    def _member_sids(self, samdb, dn):
        res = samdb.search(base=dn, scope=ldb.SCOPE_BASE,
                           attrs=["member"], controls=["extended_dn:1:1"])
        sids = []
        for m in res[0].get("member", []):
            sid = ldb.Dn(samdb, str(m)).get_extended_component("SID")
            if sid is not None:
                sids.append(str(ndr_unpack(security.dom_sid, sid)))
        return sids

    def test_forest_root_memberships(self):
        """The join of the child domain made the forest root groups
        members of its Administrators and of its Denied RODC Password
        Replication Group"""
        ea = "%s-%d" % (self.parent_sid, security.DOMAIN_RID_ENTERPRISE_ADMINS)
        sa = "%s-%d" % (self.parent_sid, security.DOMAIN_RID_SCHEMA_ADMINS)

        admins = self._member_sids(self.child,
                                   "CN=Administrators,CN=Builtin,%s" %
                                   self.child_dn)
        self.assertIn(ea, admins)

        denied = self._member_sids(self.child,
                                   "CN=Denied RODC Password Replication Group,"
                                   "CN=Users,%s" % self.child_dn)
        self.assertIn(ea, denied)
        self.assertIn(sa, denied)

    def test_forest_root_admin_token(self):
        """On a DC of the child domain, the Administrator of the forest root
        domain is a member of Enterprise Admins and, through it, of the
        Administrators of the child domain"""
        for kerberos_state in (MUST_USE_KERBEROS, DONT_USE_KERBEROS):
            samdb = self._connect(self.child_fqdn, self.parent_account,
                                  kerberos_state)
            sids = self._token_sids(samdb)
            self.assertIn("%s-%d" % (self.parent_sid,
                                     security.DOMAIN_RID_ENTERPRISE_ADMINS),
                          sids)
            self.assertIn(security.SID_BUILTIN_ADMINISTRATORS, sids)

    def _lsa(self, host, account):
        conn = lsa.lsarpc("ncacn_np:%s[seal]" % host, self.lp,
                          self._creds(account))
        attr = lsa.ObjectAttribute()
        attr.sec_qos = lsa.QosInfo()
        handle = conn.OpenPolicy2('', attr, security.SEC_FLAG_MAXIMUM_ALLOWED)
        return conn, handle

    def _lookup_name(self, conn, handle, name):
        string = lsa.String()
        string.string = name
        sids = lsa.TransSidArray()
        (domains, sids, count) = conn.LookupNames(handle, [string], sids,
                                                  lsa.LSA_LOOKUP_NAMES_ALL, 0)
        self.assertEqual(count, 1, name)
        item = sids.sids[0]
        domain = domains.domains[item.sid_index]
        return (item.sid_type, str(domain.sid), item.rid,
                domain.name.string)

    def _lookup_sid(self, conn, handle, sid):
        ptr = lsa.SidPtr()
        ptr.sid = security.dom_sid(sid)
        sids = lsa.SidArray()
        sids.sids = [ptr]
        sids.num_sids = 1
        names = lsa.TransNameArray()
        (domains, names, count) = conn.LookupSids(handle, sids, names,
                                                  lsa.LSA_LOOKUP_NAMES_ALL, 0)
        self.assertEqual(count, 1, sid)
        item = names.names[0]
        return (item.sid_type, item.name.string,
                domains.domains[item.sid_index].name.string)

    def test_lsa_lookups_cross_domain(self):
        """Both DCs resolve the names and the SIDs of both domains of the
        forest: NT4 names, UPNs, domain names"""
        domains = ((self.child_domain, self.child_realm, self.child_sid),
                   (self.parent_domain, self.parent_realm, self.parent_sid))
        for host, account in ((self.child_fqdn, self.child_account),
                              (self.parent_fqdn, self.parent_account)):
            conn, handle = self._lsa(host, account)
            for netbios, realm, sid in domains:
                where = "%s on %s" % (netbios, host)
                for name in ("%s\\Administrator" % netbios,
                             "Administrator@%s" % realm.lower()):
                    self.assertEqual(self._lookup_name(conn, handle, name),
                                     (lsa.SID_NAME_USER, str(sid),
                                      security.DOMAIN_RID_ADMINISTRATOR,
                                      netbios),
                                     "%s on %s" % (name, host))

                (sid_type, dom_sid, rid, dom_name) = \
                    self._lookup_name(conn, handle, "%s\\" % netbios)
                self.assertEqual((sid_type, dom_sid, dom_name),
                                 (lsa.SID_NAME_DOMAIN, str(sid), netbios),
                                 where)

                self.assertEqual(
                    self._lookup_sid(conn, handle, "%s-%d" % (
                        sid, security.DOMAIN_RID_ADMINS)),
                    (lsa.SID_NAME_DOM_GRP, "Domain Admins", netbios),
                    where)

    def _dsa_guid(self, samdb):
        res = samdb.search(base="", scope=ldb.SCOPE_BASE,
                           attrs=["dsServiceName"])
        dn = str(res[0]["dsServiceName"][0])
        res = samdb.search(base=dn, scope=ldb.SCOPE_BASE,
                           attrs=["objectGUID"])
        return res[0]["objectGUID"][0]

    def _replicate(self, dest_fqdn, source, nc_dn):
        """DsReplicaSync on dest_fqdn of nc_dn from the source DSA, as the
        Administrator of the forest root domain"""
        (drs, handle, _) = drs_utils.drsuapi_connect(
            dest_fqdn, self.lp, self._creds(self.parent_account))
        drs_utils.sendDsReplicaSync(drs, handle, self._dsa_guid(source),
                                    str(nc_dn), drsuapi.DRSUAPI_DRS_WRIT_REP)
        return drs, handle

    def test_replication_cross_domain(self):
        """The Configuration NC replicates in both directions between the
        DCs of the two domains"""
        config_dn = self.parent.get_config_basedn()
        self._replicate(self.child_fqdn, self.parent, config_dn)
        self._replicate(self.parent_fqdn, self.child, config_dn)

    def test_global_catalog(self):
        """The KCC of the forest root DC, a global catalog, adds a partial
        replica of the child domain, which replicates and is searchable
        over the global catalog port"""
        (drs, handle) = self._replicate(self.parent_fqdn, self.child,
                                        self.parent.get_config_basedn())
        drs.DsExecuteKCC(handle, 1, drsuapi.DsExecuteKCC1())

        res = self.parent.search(base=self.parent.get_dsServiceName(),
                                 scope=ldb.SCOPE_BASE,
                                 attrs=["options", "hasPartialReplicaNCs"])
        options = int(res[0].get("options", idx=0) or 0)
        self.assertTrue(options & dsdb.DS_NTDSDSA_OPT_IS_GC)
        partial = [str(v).lower() for v in res[0].get("hasPartialReplicaNCs", [])]
        self.assertIn(str(self.child_dn).lower(), partial)

        self._replicate(self.parent_fqdn, self.child, self.child_dn)

        gc = self._connect(self.parent_fqdn, self.parent_account, port=3268)

        # The NC head is instantiated: a partial (read-only) replica
        res = gc.search(base=self.child_dn, scope=ldb.SCOPE_BASE,
                        attrs=["instanceType"])
        self.assertEqual(int(res[0]["instanceType"][0]),
                         dsdb.INSTANCE_TYPE_IS_NC_HEAD |
                         dsdb.INSTANCE_TYPE_NC_ABOVE)

        # Both Administrators are found from the root of the forest
        res = gc.search(base="", scope=ldb.SCOPE_SUBTREE,
                        expression="(&(objectClass=user)"
                        "(sAMAccountName=Administrator))",
                        attrs=["objectSid"])
        sids = sorted(str(ndr_unpack(security.dom_sid, m["objectSid"][0]))
                      for m in res)
        self.assertEqual(sids, sorted(["%s-%d" % (s, security.DOMAIN_RID_ADMINISTRATOR)
                                       for s in (self.child_sid, self.parent_sid)]))

    def _connections_from(self, samdb):
        """The fromServer of the nTDSConnection objects of the DSA of
        samdb"""
        res = samdb.search(base=samdb.get_dsServiceName(),
                           scope=ldb.SCOPE_ONELEVEL,
                           expression="(objectClass=nTDSConnection)",
                           attrs=["fromServer"])
        return [str(m["fromServer"][0]).lower() for m in res]

    def test_kcc_cross_domain(self):
        """The KCC runs on the DCs of both domains, and each one creates a
        connection from the DC of the other domain (they share the
        Configuration and Schema NCs)"""
        for samdb, fqdn, other in ((self.child, self.child_fqdn, self.parent),
                                   (self.parent, self.parent_fqdn, self.child)):
            (drs, handle, _) = drs_utils.drsuapi_connect(
                fqdn, self.lp, self._creds(self.parent_account))
            drs.DsExecuteKCC(handle, 1, drsuapi.DsExecuteKCC1())
            self.assertIn(str(other.get_dsServiceName()).lower(),
                          self._connections_from(samdb), fqdn)

    #
    # Phase 3
    #

    def _kcc(self, fqdn):
        (drs, handle, _) = drs_utils.drsuapi_connect(
            fqdn, self.lp, self._creds(self.parent_account))
        drs.DsExecuteKCC(handle, 1, drsuapi.DsExecuteKCC1())

    def _sync_global_catalogs(self):
        """Partial replicas of each domain on the GC of the other one"""
        config_dn = self.parent.get_config_basedn()
        self._replicate(self.parent_fqdn, self.child, config_dn)
        self._replicate(self.child_fqdn, self.parent, config_dn)
        self._kcc(self.parent_fqdn)
        self._kcc(self.child_fqdn)
        self._replicate(self.parent_fqdn, self.child, self.child_dn)
        self._replicate(self.child_fqdn, self.parent, self.parent_dn)

    def test_forest_partitions_on_child(self):
        """The DC of the child domain holds the Schema, Configuration and
        ForestDnsZones partitions of the forest, which replicate in both
        directions"""
        forest_dns = "DC=ForestDnsZones,%s" % self.parent_dn
        res = self.child.search(base="", scope=ldb.SCOPE_BASE,
                                attrs=["namingContexts"])
        ncs = [str(v).lower() for v in res[0]["namingContexts"]]
        for nc in (self.parent.get_config_basedn(),
                   self.parent.get_schema_basedn(), forest_dns):
            self.assertIn(str(nc).lower(), ncs)

        res = self.child.search(base=self.child.get_dsServiceName(),
                                scope=ldb.SCOPE_BASE,
                                attrs=["msDS-hasMasterNCs"])
        held = [str(v).lower() for v in res[0]["msDS-hasMasterNCs"]]
        self.assertIn(forest_dns.lower(), held)

        res = self.parent.search(base=self.parent.get_partitions_dn(),
                                 scope=ldb.SCOPE_ONELEVEL,
                                 expression="(nCName=%s)" % forest_dns,
                                 attrs=["msDS-NC-Replica-Locations"])
        self.assertEqual(len(res), 1)
        locations = [str(v).lower()
                     for v in res[0]["msDS-NC-Replica-Locations"]]
        self.assertIn(str(self.child.get_dsServiceName()).lower(), locations)

        # the _msdcs zone of the forest is served by the child DC too
        zone = "DC=_msdcs.%s,CN=MicrosoftDNS,%s" % (
            self.parent_realm.lower(), forest_dns)
        res = self.child.search(base=zone, scope=ldb.SCOPE_BASE, attrs=[])
        self.assertEqual(len(res), 1)

        self._replicate(self.child_fqdn, self.parent, forest_dns)
        self._replicate(self.parent_fqdn, self.child, forest_dns)

    def test_child_global_catalog(self):
        """The GC of the child domain holds a partial replica of the forest
        root domain: the objects of the whole forest are found on its
        global catalog port"""
        self._sync_global_catalogs()

        res = self.child.search(base=self.child.get_dsServiceName(),
                                scope=ldb.SCOPE_BASE,
                                attrs=["hasPartialReplicaNCs"])
        partial = [str(v).lower()
                   for v in res[0].get("hasPartialReplicaNCs", [])]
        self.assertIn(str(self.parent_dn).lower(), partial)

        gc = self._connect(self.child_fqdn, self.child_account, port=3268)
        res = gc.search(base=self.parent_dn, scope=ldb.SCOPE_BASE,
                        attrs=["instanceType"])
        # nothing above the forest root NC on the child DC
        self.assertEqual(int(res[0]["instanceType"][0]),
                         dsdb.INSTANCE_TYPE_IS_NC_HEAD)

        res = gc.search(base="", scope=ldb.SCOPE_SUBTREE,
                        expression="(&(objectClass=user)"
                        "(sAMAccountName=Administrator))",
                        attrs=["objectSid"])
        sids = sorted(str(ndr_unpack(security.dom_sid, m["objectSid"][0]))
                      for m in res)
        self.assertEqual(sids, sorted(["%s-%d" % (s, security.DOMAIN_RID_ADMINISTRATOR)
                                       for s in (self.child_sid, self.parent_sid)]))

    def test_partial_replica_backlinks(self):
        """The backlinks of the links replicated before their target
        (serverReference of the child DC, in the Configuration NC) are
        added when the target arrives in the partial replica"""
        self._sync_global_catalogs()

        res = self.child.search(base=self.child.get_dsServiceName(),
                                scope=ldb.SCOPE_BASE, attrs=[])
        server_dn = res[0].dn.parent()
        res = self.child.search(base=server_dn, scope=ldb.SCOPE_BASE,
                                attrs=["serverReference"])
        computer_dn = str(res[0]["serverReference"][0])

        gc = self._connect(self.parent_fqdn, self.parent_account, port=3268)
        res = gc.search(base=computer_dn, scope=ldb.SCOPE_BASE,
                        attrs=["serverReferenceBL"])
        self.assertEqual([str(v).lower()
                          for v in res[0].get("serverReferenceBL", [])],
                         [str(server_dn).lower()])

    def _new_child_user(self):
        name = "xdom%s" % uuid.uuid4().hex[:8]
        password = "Passw0rd.%s" % uuid.uuid4().hex[:8]
        self.child.newuser(name, password, useusernameascn=True)
        res = self.child.search(base=self.child_dn, scope=ldb.SCOPE_SUBTREE,
                                expression="(sAMAccountName=%s)" % name,
                                attrs=["objectSid"])
        self.addCleanup(self.child.delete, res[0].dn)
        sid = str(ndr_unpack(security.dom_sid, res[0]["objectSid"][0]))
        return (name, password, res[0].dn, sid)

    def _new_parent_group(self, scope):
        name = "xdom%s%s" % (scope, uuid.uuid4().hex[:6])
        group_type = {"universal": dsdb.GTYPE_SECURITY_UNIVERSAL_GROUP,
                      "global": dsdb.GTYPE_SECURITY_GLOBAL_GROUP,
                      "local": dsdb.GTYPE_SECURITY_DOMAIN_LOCAL_GROUP}[scope]
        dn = "CN=%s,CN=Users,%s" % (name, self.parent_dn)
        self.parent.newgroup(name, grouptype=group_type)
        self.addCleanup(self.parent.delete, dn)
        res = self.parent.search(base=dn, scope=ldb.SCOPE_BASE,
                                 attrs=["objectSid"])
        return (dn, str(ndr_unpack(security.dom_sid,
                                   res[0]["objectSid"][0])))

    def _add_member(self, group_dn, member_dn):
        m = ldb.Message()
        m.dn = ldb.Dn(self.parent, group_dn)
        m["member"] = ldb.MessageElement(str(member_dn), ldb.FLAG_MOD_ADD,
                                         "member")
        self.parent.modify(m)

    def _cross_domain_groups(self):
        """A user of the child domain, member of a universal and of a
        domain local group of the forest root domain"""
        self._sync_global_catalogs()
        (name, password, user_dn, user_sid) = self._new_child_user()
        self._replicate(self.parent_fqdn, self.child, self.child_dn)

        (univ_dn, univ_sid) = self._new_parent_group("universal")
        (local_dn, local_sid) = self._new_parent_group("local")
        self._add_member(univ_dn, user_dn)
        self._add_member(local_dn, user_dn)
        self._replicate(self.child_fqdn, self.parent, self.parent_dn)
        return (name, password, user_dn, user_sid, univ_dn, univ_sid,
                local_dn, local_sid)

    def test_cross_domain_membership(self):
        """A user of the child domain can be a member of a universal or
        domain local group of the forest root domain, not of a global
        group; the reference carries the GUID and SID of the user"""
        (name, password, user_dn, user_sid, univ_dn, univ_sid,
         local_dn, local_sid) = self._cross_domain_groups()

        res = self.parent.search(base=univ_dn, scope=ldb.SCOPE_BASE,
                                 attrs=["member"],
                                 controls=["extended_dn:1:1"])
        member = ldb.Dn(self.parent, str(res[0]["member"][0]))
        self.assertEqual(str(member).lower(), str(user_dn).lower())
        self.assertEqual(str(ndr_unpack(security.dom_sid,
                                        member.get_extended_component("SID"))),
                         user_sid)

        (global_dn, global_sid) = self._new_parent_group("global")
        try:
            self._add_member(global_dn, user_dn)
            self.fail("a global group accepted a member of another domain")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_UNWILLING_TO_PERFORM)

        # memberOf on the copy of the user in the partial replica
        gc = self._connect(self.parent_fqdn, self.parent_account, port=3268)
        res = gc.search(base=str(user_dn), scope=ldb.SCOPE_BASE,
                        attrs=["memberOf"])
        member_of = [str(v).lower() for v in res[0].get("memberOf", [])]
        self.assertIn(str(univ_dn).lower(), member_of)

    def test_universal_groups_in_tokens(self):
        """The token of a user of the child domain has the universal group
        of the forest root domain on both DCs, and the domain local group
        of the forest root domain only on the DC of the forest root"""
        (name, password, user_dn, user_sid, univ_dn, univ_sid,
         local_dn, local_sid) = self._cross_domain_groups()
        account = (name, password, self.child_domain, self.child_realm)

        for kerberos_state in (MUST_USE_KERBEROS, DONT_USE_KERBEROS):
            sids = self._token_sids(self._connect(self.child_fqdn, account,
                                                  kerberos_state))
            self.assertIn(univ_sid, sids)
            self.assertNotIn(local_sid, sids)

            sids = self._token_sids(self._connect(self.parent_fqdn, account,
                                                  kerberos_state))
            self.assertIn(univ_sid, sids)
            self.assertIn(local_sid, sids)

    def _netlogon_trusts(self, fqdn, account):
        conn = netlogon.netlogon("ncacn_ip_tcp:%s[seal]" % fqdn, self.lp,
                                 self._creds(account))
        flags = (netlogon.NETR_TRUST_FLAG_IN_FOREST |
                 netlogon.NETR_TRUST_FLAG_OUTBOUND |
                 netlogon.NETR_TRUST_FLAG_TREEROOT |
                 netlogon.NETR_TRUST_FLAG_PRIMARY |
                 netlogon.NETR_TRUST_FLAG_NATIVE |
                 netlogon.NETR_TRUST_FLAG_INBOUND)
        trusts = conn.netr_DsrEnumerateDomainTrusts(fqdn, flags)
        return dict((str(t.netbios_name), (i, t))
                    for i, t in enumerate(trusts.array))

    def test_netlogon_domain_trusts(self):
        """DsrEnumerateDomainTrusts lists both domains of the forest, with
        the tree root, the primary domain and the parent_index of the
        child domain"""
        for fqdn, account, primary in (
                (self.child_fqdn, self.child_account, self.child_domain),
                (self.parent_fqdn, self.parent_account, self.parent_domain)):
            trusts = self._netlogon_trusts(fqdn, account)
            (root_index, root) = trusts[self.parent_domain]
            (child_index, child) = trusts[self.child_domain]

            for t in (root, child):
                self.assertTrue(t.trust_flags & netlogon.NETR_TRUST_FLAG_IN_FOREST)
                self.assertTrue(t.trust_flags & netlogon.NETR_TRUST_FLAG_NATIVE)
            self.assertTrue(root.trust_flags & netlogon.NETR_TRUST_FLAG_TREEROOT)
            self.assertFalse(child.trust_flags & netlogon.NETR_TRUST_FLAG_TREEROOT)
            self.assertEqual(child.parent_index, root_index, fqdn)
            self.assertEqual(str(root.sid), str(self.parent_sid))
            self.assertEqual(str(child.sid), str(self.child_sid))

            (primary_index, p) = trusts[primary]
            self.assertTrue(p.trust_flags & netlogon.NETR_TRUST_FLAG_PRIMARY)
            other = child if primary == self.parent_domain else root
            self.assertFalse(other.trust_flags & netlogon.NETR_TRUST_FLAG_PRIMARY)
            self.assertTrue(other.trust_flags & netlogon.NETR_TRUST_FLAG_INBOUND)
            self.assertTrue(other.trust_flags & netlogon.NETR_TRUST_FLAG_OUTBOUND)

    def _transfer_naming_master(self, samdb):
        m = ldb.Message()
        m.dn = ldb.Dn(samdb, "")
        m["becomeDomainMaster"] = ldb.MessageElement(
            "1", ldb.FLAG_MOD_REPLACE, "becomeDomainMaster")
        samdb.modify(m)

    def _naming_master(self, samdb):
        res = samdb.search(base=samdb.get_partitions_dn(),
                           scope=ldb.SCOPE_BASE, attrs=["fSMORoleOwner"])
        return str(res[0]["fSMORoleOwner"][0]).lower()

    def test_domain_naming_master(self):
        """Only the domain naming master creates or deletes crossRefs, the
        crossRef of a domain that still has DCs can't be deleted, and the
        role moves between the domains of the forest"""
        self._replicate(self.child_fqdn, self.parent,
                        self.parent.get_config_basedn())
        self.assertEqual(self._naming_master(self.child),
                         str(self.parent.get_dsServiceName()).lower())

        xref = "CN=%s,%s" % (self.child_domain, self.parent.get_partitions_dn())
        try:
            self.parent.delete(xref)
            self.fail("the crossRef of the child domain was deleted")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_UNWILLING_TO_PERFORM)

        name = "xref%s" % uuid.uuid4().hex[:6]
        rec = {"dn": "CN=%s,%s" % (name, self.parent.get_partitions_dn()),
               "objectClass": "crossRef",
               "nCName": "DC=ForestDnsZones,%s" % self.parent_dn,
               "dnsRoot": "%s.%s" % (name, self.parent_realm.lower())}

        # not the naming master (as an Enterprise Admin)
        child_as_ea = self._connect(self.child_fqdn, self.parent_account)
        try:
            child_as_ea.add(rec)
            child_as_ea.delete(rec["dn"])
            self.fail("a crossRef was created on the child DC")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_UNWILLING_TO_PERFORM)

        # the naming master refuses a dnsRoot already in use
        dup = dict(rec)
        dup["dnsRoot"] = self.child_realm.lower()
        try:
            self.parent.add(dup)
            self.parent.delete(dup["dn"])
            self.fail("a second crossRef for %s was created" % dup["dnsRoot"])
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_ENTRY_ALREADY_EXISTS)

        # the role goes to the child DC and back
        try:
            self._transfer_naming_master(self.child)
            self.assertEqual(self._naming_master(self.child),
                             str(self.child.get_dsServiceName()).lower())
        finally:
            self._transfer_naming_master(self.parent)
        self.assertEqual(self._naming_master(self.parent),
                         str(self.parent.get_dsServiceName()).lower())

    def test_infrastructure_update(self):
        """The infrastructure master of the forest root domain finds the
        references to the objects of the child domain, and updates them
        when their target is renamed or deleted"""
        (name, password, user_dn, user_sid, univ_dn, univ_sid,
         local_dn, local_sid) = self._cross_domain_groups()

        infra = InfrastructureUpdate(self.parent, self._logger())
        self.assertTrue(infra.is_infrastructure_master())
        # the parent DC is a GC: the references are already up to date
        self.assertEqual(infra.run(fix=False, ignore_gc=True), [])

        # as seen from a DC that is not a GC: the GC tells that the user
        # was renamed, then deleted
        renamed = ldb.Dn(self.parent, "CN=%s-renamed,CN=Users,%s" %
                         (name, self.child_dn))

        class FakeGC(object):
            deleted = False

            def search(self, base, scope, attrs, controls):
                msg = ldb.Message(renamed)
                msg["objectSid"] = ldb.MessageElement(
                    ndr_pack_sid(user_sid), ldb.FLAG_MOD_ADD, "objectSid")
                if self.deleted:
                    msg["isDeleted"] = ldb.MessageElement(
                        "TRUE", ldb.FLAG_MOD_ADD, "isDeleted")
                return [msg]

        infra.gc = FakeGC()
        changes = [c for c in infra.run(fix=False, ignore_gc=True)
                   if str(c[0]).lower() in (str(univ_dn).lower(),
                                            str(local_dn).lower())]
        self.assertEqual(len(changes), 2)
        for (dn, attr, old, new) in changes:
            self.assertEqual(attr.lower(), "member")
            self.assertIn(str(renamed), new)

        infra.gc.deleted = True
        changes = [c for c in infra.run(fix=False, ignore_gc=True)
                   if str(c[0]).lower() == str(univ_dn).lower()]
        self.assertEqual(len(changes), 1)
        self.assertIsNone(changes[0][3])

        # As on Windows, the role does nothing on a global catalog,
        # even with a GC that knows about the rename
        self.assertTrue(infra.is_gc())
        self.assertEqual(infra.run(fix=False), [])

    def test_infrastructure_master_on_gc(self):
        """As on Windows, the infrastructure master role does nothing on
        a global catalog: it says so, and asks to move the role when a DC
        of the domain is not a GC"""
        import logging

        class Records(logging.Handler):
            def __init__(self):
                super().__init__()
                self.records = []

            def emit(self, record):
                self.records.append(record)

        logger = logging.getLogger("subdomain-infra-gc")
        logger.setLevel(logging.DEBUG)
        handler = Records()
        logger.addHandler(handler)
        self.addCleanup(logger.removeHandler, handler)

        infra = InfrastructureUpdate(self.parent, logger)
        self.assertTrue(infra.is_gc())
        self.assertEqual(infra.run(fix=True), [])
        self.assertTrue(handler.records)
        not_gc = infra.domain_dcs_not_gc()
        warnings = [r for r in handler.records
                    if r.levelno == logging.WARNING]
        if not_gc:
            self.assertTrue(warnings)
            self.assertIn("global catalog", warnings[0].getMessage())
        else:
            self.assertFalse(warnings)

    def _logger(self):
        import logging
        return logging.getLogger("subdomain-tests")


def ndr_pack_sid(sid):
    from samba.ndr import ndr_pack
    return ndr_pack(security.dom_sid(sid))
