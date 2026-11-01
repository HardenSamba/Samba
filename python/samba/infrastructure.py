# Unix SMB/CIFS implementation.
#
# Infrastructure master of a domain of a multi-domain forest
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

"""Infrastructure master of a domain of a multi-domain forest.

The objects of a domain can reference objects of the other domains of
the forest: a user of a child domain in a universal group of the forest
root domain, for example. Windows keeps a phantom for the target of such
a reference, and the infrastructure master of each domain checks the
phantoms against a global catalog, so that the references follow a
rename or a move of their target, and disappear when it is deleted.

Samba has no phantoms: the reference itself (the value of the linked
attribute) carries the GUID, the SID and the DN of its target. The
infrastructure master of a domain looks the targets up by GUID in the
global catalog (the partial replicas of the other domains held by this
DC, or another GC) and updates the references of its domain NC:

- the DN or the SID of the target changed: the value is rewritten,
  which replicates to the other DCs of the domain;
- the target is deleted: the value is removed.

A target that the global catalog doesn't know (yet) is left alone.

As on Windows, the role does nothing on a DC that is a global catalog:
a GC holds the partial replica of every other domain, its references
are kept up to date by the replication, and it has no phantoms to
check. When some DCs of the domain are not GCs, the infrastructure
master must not be a GC (a warning says so); when they all are, the
role has nothing to do.
"""

import ldb
from samba import dsdb
from samba.ndr import ndr_unpack
from samba.dcerpc import misc, security
from samba.samdb import dsdb_dn_guess


class InfrastructureUpdate(object):
    """Check and fix the references of a domain NC to the objects of the
    other domains of the forest.

    :param samdb: a SamDB of a DC of the domain (local or LDAP)
    :param gc: a SamDB to look the targets up, by default samdb (a
        global catalog holds the partial replica of every other domain)
    :param logger: a logger
    """

    def __init__(self, samdb, logger, gc=None):
        self.samdb = samdb
        self.gc = gc if gc is not None else samdb
        self.logger = logger
        self.domain_dn = samdb.get_default_basedn()

    def is_gc(self):
        """Is the DC of samdb a global catalog?"""
        res = self.samdb.search(base=self.samdb.get_dsServiceName(),
                                scope=ldb.SCOPE_BASE, attrs=["options"])
        options = int(res[0].get("options", idx=0) or 0)
        return bool(options & dsdb.DS_NTDSDSA_OPT_IS_GC)

    def find_gc(self):
        """The DNS names of the other global catalogs of the forest"""
        res = self.samdb.search(base=self.samdb.get_config_basedn(),
                                scope=ldb.SCOPE_SUBTREE,
                                expression="(&(objectClass=nTDSDSA)"
                                "(options:1.2.840.113556.1.4.803:=%u))" %
                                dsdb.DS_NTDSDSA_OPT_IS_GC,
                                attrs=[])
        mine = ldb.Dn(self.samdb, self.samdb.get_dsServiceName())
        names = []
        for msg in res:
            if msg.dn == mine:
                continue
            server = self.samdb.search(base=msg.dn.parent(),
                                       scope=ldb.SCOPE_BASE,
                                       attrs=["dNSHostName"])
            if len(server) == 1 and "dNSHostName" in server[0]:
                names.append(str(server[0]["dNSHostName"][0]))
        return names

    def domain_dcs_not_gc(self):
        """The nTDSDSA objects of the DCs of our domain that are not
        global catalogs"""
        res = self.samdb.search(base=self.samdb.get_config_basedn(),
                                scope=ldb.SCOPE_SUBTREE,
                                expression="(&(objectClass=nTDSDSA)"
                                "(msDS-HasDomainNCs=%s)"
                                "(!(options:1.2.840.113556.1.4.803:=%u)))" %
                                (ldb.binary_encode(str(self.domain_dn)),
                                 dsdb.DS_NTDSDSA_OPT_IS_GC),
                                attrs=[])
        return [msg.dn for msg in res]

    def gc_disabled_reason(self):
        """Log why the role does nothing on this DC, a global catalog"""
        others = self.domain_dcs_not_gc()
        if others:
            self.logger.warning(
                "This DC is the infrastructure master and a global "
                "catalog: as on Windows, the infrastructure master role "
                "does nothing on a global catalog, and the references of "
                "the DCs that are not global catalogs (%s) to the other "
                "domains of the forest are not updated. Transfer the "
                "role to one of them (samba-tool fsmo transfer "
                "--role=infrastructure), or make them global catalogs." %
                ", ".join(str(dn.parent().get_rdn_value()) for dn in others))
        else:
            self.logger.info("This DC is a global catalog, like every DC "
                             "of the domain: the infrastructure master role "
                             "has nothing to do.")

    def is_infrastructure_master(self):
        """Is the DC of samdb the infrastructure master of its domain?"""
        res = self.samdb.search(base="CN=Infrastructure,%s" % self.domain_dn,
                                scope=ldb.SCOPE_BASE,
                                attrs=["fSMORoleOwner"])
        if len(res) != 1 or "fSMORoleOwner" not in res[0]:
            return False
        owner = ldb.Dn(self.samdb, str(res[0]["fSMORoleOwner"][0]))
        mine = ldb.Dn(self.samdb, self.samdb.get_dsServiceName())
        return owner == mine

    def forward_links(self):
        """The lDAPDisplayName of every forward link attribute"""
        res = self.samdb.search(base=self.samdb.get_schema_basedn(),
                                scope=ldb.SCOPE_ONELEVEL,
                                expression="(&(objectClass=attributeSchema)"
                                "(linkID=*))",
                                attrs=["lDAPDisplayName", "linkID"])
        names = []
        for msg in res:
            link_id = int(msg["linkID"][0])
            if link_id != 0 and link_id % 2 == 0:
                names.append(str(msg["lDAPDisplayName"][0]))
        return sorted(names)

    def other_domain_ncs(self):
        """The domain NCs of the other domains of the forest"""
        flags = dsdb.SYSTEM_FLAG_CR_NTDS_NC | dsdb.SYSTEM_FLAG_CR_NTDS_DOMAIN
        res = self.samdb.search(base=self.samdb.get_partitions_dn(),
                                scope=ldb.SCOPE_ONELEVEL,
                                expression="(&(objectClass=crossRef)"
                                "(systemFlags:1.2.840.113556.1.4.803:=%u))" %
                                flags,
                                attrs=["nCName"])
        ncs = []
        for msg in res:
            nc = ldb.Dn(self.samdb, str(msg["nCName"][0]))
            if nc != self.domain_dn:
                ncs.append(nc)
        return ncs

    def _in_other_domain(self, dn, other_ncs):
        """Is dn in the domain NC of another domain? The most specific
        domain NC containing it wins: the NC of a child domain is below
        the NC of its parent."""
        best = None
        best_is_other = False
        for (nc, is_other) in [(self.domain_dn, False)] + \
                [(nc, True) for nc in other_ncs]:
            if not dn.is_child_of(nc):
                continue
            if best is None or nc.is_child_of(best):
                best = nc
                best_is_other = is_other
        return best_is_other

    def _lookup(self, guid):
        """The target by GUID in the global catalog: (dn, sid, deleted),
        or None if it is not known there"""
        try:
            res = self.gc.search(base="<GUID=%s>" % guid,
                                 scope=ldb.SCOPE_BASE,
                                 attrs=["objectSid", "isDeleted"],
                                 controls=["show_deleted:1",
                                           "show_recycled:1"])
        except ldb.LdbError as e:
            (enum, estr) = e.args
            if enum == ldb.ERR_NO_SUCH_OBJECT:
                return None
            raise
        if len(res) != 1:
            return None
        sid = None
        if "objectSid" in res[0]:
            sid = str(ndr_unpack(security.dom_sid, res[0]["objectSid"][0]))
        deleted = str(res[0].get("isDeleted", idx=0)).upper() == "TRUE"
        return (res[0].dn, sid, deleted)

    def run(self, fix=True, ignore_gc=False):
        """Check (and fix, if fix is True) the references of our domain
        NC to the other domains of the forest.

        Nothing is done on a global catalog (see gc_disabled_reason()),
        unless ignore_gc is True (for the tests).

        :return: a list of (object dn, attribute, old value, new value)
            tuples, new value being None for a removed reference
        """
        if not ignore_gc and self.is_gc():
            self.gc_disabled_reason()
            return []

        other_ncs = self.other_domain_ncs()
        if not other_ncs:
            return []

        links = self.forward_links()
        expression = "(|%s)" % "".join("(%s=*)" % a for a in links)
        res = self.samdb.search(base=self.domain_dn,
                                scope=ldb.SCOPE_SUBTREE,
                                expression=expression,
                                attrs=links,
                                controls=["extended_dn:1:1"])

        changes = []
        for msg in res:
            for attr in msg.keys():
                if attr.lower() not in [a.lower() for a in links]:
                    continue
                for value in msg[attr]:
                    change = self._check_value(msg.dn, attr, str(value),
                                               other_ncs)
                    if change is not None:
                        changes.append(change)

        for (dn, attr, old, new) in changes:
            if new is None:
                self.logger.info("Removing the reference of %s to the "
                                 "deleted object %s" % (dn, old))
            else:
                self.logger.info("Updating the reference of %s from %s "
                                 "to %s" % (dn, old, new))
            if fix:
                self._fix(dn, attr, old, new)
        return changes

    def _check_value(self, dn, attr, value, other_ncs):
        # DN, DN-Binary ("B:...", msDS-RevealedUsers of a RODC...) or
        # DN-String
        dsdb_dn = dsdb_dn_guess(self.samdb, value)
        target = dsdb_dn.dn
        if not self._in_other_domain(target, other_ncs):
            return None

        guid = target.get_extended_component("GUID")
        if guid is None:
            return None
        guid = str(ndr_unpack(misc.GUID, guid))

        found = self._lookup(guid)
        if found is None:
            return None
        (cur_dn, cur_sid, deleted) = found

        if deleted:
            return (dn, attr, value, None)

        old_sid = target.get_extended_component("SID")
        if old_sid is not None:
            old_sid = str(ndr_unpack(security.dom_sid, old_sid))

        if cur_dn == ldb.Dn(self.samdb, str(target)) and old_sid == cur_sid:
            return None

        new = "%s<GUID=%s>" % (dsdb_dn.prefix, guid)
        if cur_sid is not None:
            new += ";<SID=%s>" % cur_sid
        new += ";%s" % cur_dn
        return (dn, attr, value, new)

    def _fix(self, dn, attr, old, new):
        m = ldb.Message()
        m.dn = dn
        m["old"] = ldb.MessageElement(old, ldb.FLAG_MOD_DELETE, attr)
        if new is not None:
            m["new"] = ldb.MessageElement(new, ldb.FLAG_MOD_ADD, attr)
        self.samdb.modify(m)
