# Unix SMB/CIFS implementation.
# Tests of the conditional forwarders, stub zones and DNS partitions of
# the Harden-Box options
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

"""Run against subdom_dc, which has "harden:dns conditional forwarders",
"harden:dns stub zones" and "harden:dns default zone partition = forest";
its parent DC (ad_dc_ntvfs) has none of them and is the master server of
the conditional forwarders and stub zones created on the child DC."""

import time
import uuid

import ldb
from samba import werror, WERRORError
from samba.auth import system_session
from samba.credentials import Credentials
from samba.dcerpc import dns, dnsp, dnsserver
from samba.dnsserver import record_from_string
from samba.netcmd.dns import dns_addr_array, dns_addr_array_string
from samba.samdb import SamDB
from samba.tests import env_get_var_value, env_loadparm
from samba.tests.dns_base import DNSTest

CLIENT = dnsserver.DNS_CLIENT_VERSION_LONGHORN


class DnsHardenTests(DNSTest):

    def setUp(self):
        super().setUp()
        self.lp = env_loadparm()
        self.timeout = 10

        self.child = env_get_var_value("SUBDOM_DC_SERVER")
        self.child_ip = env_get_var_value("SERVER_IP")
        self.parent = env_get_var_value("PARENT_SERVER")
        self.parent_ip = env_get_var_value("PARENT_SERVER_IP")

        self.child_creds = self._creds(
            env_get_var_value("USERNAME"),
            env_get_var_value("PASSWORD"),
            env_get_var_value("DOMAIN"),
            env_get_var_value("REALM"))
        self.parent_creds = self._creds(
            env_get_var_value("PARENT_USERNAME"),
            env_get_var_value("PARENT_PASSWORD"),
            env_get_var_value("PARENT_DOMAIN"),
            env_get_var_value("PARENT_REALM"))

        self.child_rpc = dnsserver.dnsserver(
            "ncacn_ip_tcp:%s[sign]" % self.child_ip, self.lp,
            self.child_creds)
        self.parent_rpc = dnsserver.dnsserver(
            "ncacn_ip_tcp:%s[sign]" % self.parent_ip, self.lp,
            self.parent_creds)
        self.child_ldb = SamDB(url="ldap://%s" % self.child_ip,
                               session_info=system_session(),
                               credentials=self.child_creds, lp=self.lp)

    def _creds(self, user, password, domain, realm):
        creds = Credentials()
        creds.guess(self.lp)
        creds.set_username(user)
        creds.set_password(password)
        creds.set_domain(domain)
        creds.set_realm(realm)
        return creds

    def _name(self):
        return "hb%s.example" % uuid.uuid4().hex[:8]

    def _create(self, rpc, server, name, zone_type=dnsp.DNS_ZONE_TYPE_PRIMARY,
                dp_flags=0, masters=None, ds=True, timeout=0, slave=False):
        info = dnsserver.DNS_RPC_ZONE_CREATE_INFO_LONGHORN()
        info.pszZoneName = name
        info.dwZoneType = zone_type
        info.fAging = 0
        info.fDsIntegrated = 1 if ds else 0
        info.fLoadExisting = 0
        info.dwDpFlags = dp_flags
        if masters is not None:
            info.aipMasters = dns_addr_array(masters)
        info.dwTimeout = timeout
        info.fRecurseAfterForwarding = 1 if slave else 0
        rpc.DnssrvOperation2(CLIENT, 0, server, None, 0, 'ZoneCreate',
                             dnsserver.DNSSRV_TYPEID_ZONE_CREATE, info)

    def _delete(self, rpc, server, name, operation='DeleteZoneFromDs'):
        try:
            rpc.DnssrvOperation2(CLIENT, 0, server, name, 0, operation,
                                 dnsserver.DNSSRV_TYPEID_NULL, None)
        except WERRORError:
            pass

    def _zone_info(self, rpc, server, name):
        typeid, info = rpc.DnssrvQuery2(CLIENT, 0, server, name, 'ZoneInfo')
        return info

    def _list(self, rpc, server, request_filter):
        typeid, res = rpc.DnssrvComplexOperation2(
            CLIENT, 0, server, None, 'EnumZones',
            dnsserver.DNSSRV_TYPEID_DWORD, request_filter)
        return [z.pszZoneName.rstrip('.') for z in res.ZoneArray or []]

    def _add_a(self, rpc, server, zone, host, address):
        buf = dnsserver.DNS_RPC_RECORD_BUF()
        buf.rec = record_from_string('A', address, sep=' ')
        rpc.DnssrvUpdateRecord2(CLIENT, 0, server, zone, host, buf, None)

    def _query_a(self, name, server_ip):
        p = self.make_name_packet(dns.DNS_OPCODE_QUERY)
        p.operation |= dns.DNS_FLAG_RECURSION_DESIRED
        q = self.make_name_question(name, dns.DNS_QTYPE_A, dns.DNS_QCLASS_IN)
        self.finish_name_packet(p, [q])
        (response, _) = self.dns_transaction_udp(p, host=server_ip)
        return response

    def _parent_zone_with_host(self):
        """A zone of the parent DC with host.<zone> = 10.53.57.250"""
        name = self._name()
        self._create(self.parent_rpc, self.parent, name,
                     dp_flags=dnsserver.DNS_DP_DOMAIN_DEFAULT)
        self.addCleanup(self._delete, self.parent_rpc, self.parent, name)
        self._add_a(self.parent_rpc, self.parent, name, "host",
                    "10.53.57.250")
        return name

    def _wait_answer(self, name, server_ip, expected):
        # the DNS server reloads its zones asynchronously
        for i in range(20):
            response = self._query_a(name, server_ip)
            if (response.operation & dns.DNS_RCODE) == dns.DNS_RCODE_OK and \
               response.ancount > 0:
                self.assertEqual(response.answers[0].rdata, expected)
                return response
            time.sleep(0.5)
        self.fail("no answer for %s from %s (rcode %d)" %
                  (name, server_ip, response.operation & dns.DNS_RCODE))

    def _zone_dn(self, name):
        res = self.child_ldb.search(base="", scope=ldb.SCOPE_SUBTREE,
                                    expression="(&(objectClass=dnsZone)"
                                    "(name=%s))" % name,
                                    attrs=[],
                                    controls=["search_options:1:2"])
        self.assertEqual(len(res), 1, name)
        return str(res[0].dn)

    def test_default_partition(self):
        """A zone created without a partition goes to the partition of
        "harden:dns default zone partition" (forest)"""
        name = self._name()
        self._create(self.child_rpc, self.child, name)
        self.addCleanup(self._delete, self.child_rpc, self.child, name)
        self.assertIn(",DC=ForestDnsZones,", self._zone_dn(name))

    def test_legacy_partition(self):
        """A zone in CN=MicrosoftDNS,CN=System of the domain NC"""
        name = self._name()
        self._create(self.child_rpc, self.child, name,
                     dp_flags=dnsserver.DNS_DP_LEGACY)
        self.addCleanup(self._delete, self.child_rpc, self.child, name)
        self.assertIn(",CN=MicrosoftDNS,CN=System,", self._zone_dn(name))
        self.assertIn(name, self._list(
            self.child_rpc, self.child,
            dnsserver.DNS_ZONE_REQUEST_PRIMARY |
            dnsserver.DNS_ZONE_REQUEST_LEGACY_DP))
        self.assertNotIn(name, self._list(
            self.child_rpc, self.child,
            dnsserver.DNS_ZONE_REQUEST_PRIMARY |
            dnsserver.DNS_ZONE_REQUEST_FOREST_DP))

    def test_conditional_forwarder(self):
        """A conditional forwarder in AD: listed, described, resolved
        through its master, modified, deleted"""
        name = self._parent_zone_with_host()
        self._create(self.child_rpc, self.child, name,
                     zone_type=dnsp.DNS_ZONE_TYPE_FORWARDER,
                     dp_flags=dnsserver.DNS_DP_DOMAIN_DEFAULT,
                     masters=[self.parent_ip], timeout=7)
        self.addCleanup(self._delete, self.child_rpc, self.child, name)

        self.assertIn(",DC=DomainDnsZones,", self._zone_dn(name))
        self.assertIn(name, self._list(self.child_rpc, self.child,
                                       dnsserver.DNS_ZONE_REQUEST_FORWARDER))
        self.assertNotIn(name, self._list(self.child_rpc, self.child,
                                          dnsserver.DNS_ZONE_REQUEST_PRIMARY))

        info = self._zone_info(self.child_rpc, self.child, name)
        self.assertEqual(info.dwZoneType, dnsp.DNS_ZONE_TYPE_FORWARDER)
        self.assertEqual(info.dwForwarderTimeout, 7)
        self.assertEqual(info.fForwarderSlave, 0)
        self.assertEqual(info.aipMasters.AddrCount, 1)

        self._wait_answer("host.%s" % name, self.child_ip, "10.53.57.250")

        # the timeout and the "do not recurse" setting
        n_p = dnsserver.DNS_RPC_NAME_AND_PARAM()
        n_p.pszNodeName = "ForwarderSlave"
        n_p.dwParam = 1
        self.child_rpc.DnssrvOperation2(CLIENT, 0, self.child, name, 0,
                                        'ResetDwordProperty',
                                        dnsserver.DNSSRV_TYPEID_NAME_AND_PARAM,
                                        n_p)
        info = self._zone_info(self.child_rpc, self.child, name)
        self.assertEqual(info.fForwarderSlave, 1)

        # the master servers
        self.child_rpc.DnssrvOperation2(CLIENT, 0, self.child, name, 0,
                                        'MasterServers',
                                        dnsserver.DNSSRV_TYPEID_ADDRARRAY,
                                        dns_addr_array([self.parent_ip,
                                                        "10.53.57.251",
                                                        "fd00::5357:5f15"]))
        info = self._zone_info(self.child_rpc, self.child, name)
        self.assertEqual(dns_addr_array_string(info.aipMasters),
                         [self.parent_ip, "10.53.57.251", "fd00::5357:5f15"])

        # no records in a conditional forwarder
        buf = dnsserver.DNS_RPC_RECORD_BUF()
        buf.rec = record_from_string('A', "10.0.0.1", sep=' ')
        try:
            self.child_rpc.DnssrvUpdateRecord2(CLIENT, 0, self.child, name,
                                               "x", buf, None)
            self.fail("a record was added to a conditional forwarder")
        except WERRORError as e:
            self.assertEqual(e.args[0],
                             werror.WERR_DNS_ERROR_INVALID_ZONE_TYPE)

        self._delete(self.child_rpc, self.child, name)
        self.assertNotIn(name, self._list(self.child_rpc, self.child,
                                          dnsserver.DNS_ZONE_REQUEST_FORWARDER))

    def test_local_conditional_forwarder(self):
        """A conditional forwarder stored on the DC only (not in AD)"""
        name = self._parent_zone_with_host()
        self._create(self.child_rpc, self.child, name,
                     zone_type=dnsp.DNS_ZONE_TYPE_FORWARDER,
                     masters=[self.parent_ip], ds=False)
        self.addCleanup(self._delete, self.child_rpc, self.child, name,
                        'DeleteZone')

        res = self.child_ldb.search(base="", scope=ldb.SCOPE_SUBTREE,
                                    expression="(&(objectClass=dnsZone)"
                                    "(name=%s))" % name,
                                    attrs=[],
                                    controls=["search_options:1:2"])
        self.assertEqual(len(res), 0)
        self.assertIn(name, self._list(self.child_rpc, self.child,
                                       dnsserver.DNS_ZONE_REQUEST_FORWARDER |
                                       dnsserver.DNS_ZONE_REQUEST_NON_DS))
        info = self._zone_info(self.child_rpc, self.child, name)
        self.assertEqual(info.fUseDatabase, 0)

        self._wait_answer("host.%s" % name, self.child_ip, "10.53.57.250")

        self._delete(self.child_rpc, self.child, name, 'DeleteZone')
        self.assertNotIn(name, self._list(self.child_rpc, self.child,
                                          dnsserver.DNS_ZONE_REQUEST_FORWARDER))

    def test_stub_zone(self):
        """A stub zone in the forest partition, resolved through its
        master"""
        name = self._parent_zone_with_host()
        self._create(self.child_rpc, self.child, name,
                     zone_type=dnsp.DNS_ZONE_TYPE_STUB,
                     dp_flags=dnsserver.DNS_DP_FOREST_DEFAULT,
                     masters=[self.parent_ip])
        self.addCleanup(self._delete, self.child_rpc, self.child, name)

        self.assertIn(",DC=ForestDnsZones,", self._zone_dn(name))
        self.assertIn(name, self._list(self.child_rpc, self.child,
                                       dnsserver.DNS_ZONE_REQUEST_STUB))
        info = self._zone_info(self.child_rpc, self.child, name)
        self.assertEqual(info.dwZoneType, dnsp.DNS_ZONE_TYPE_STUB)

        self._wait_answer("host.%s" % name, self.child_ip, "10.53.57.250")

    def test_disabled_on_parent(self):
        """Without the options (the parent DC), conditional forwarders
        are refused, as by upstream Samba"""
        name = self._name()
        try:
            self._create(self.parent_rpc, self.parent, name,
                         zone_type=dnsp.DNS_ZONE_TYPE_FORWARDER,
                         dp_flags=dnsserver.DNS_DP_DOMAIN_DEFAULT,
                         masters=[self.child_ip])
            self._delete(self.parent_rpc, self.parent, name)
            self.fail("the parent DC created a conditional forwarder")
        except WERRORError as e:
            self.assertEqual(e.args[0], werror.WERR_CALL_NOT_IMPLEMENTED)
