# Unix SMB/CIFS implementation.
# Tests of the AD Recycle Bin handling ("harden:recycle bin = yes")
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

"""The AD Recycle Bin with "harden:recycle bin = yes" ([MS-ADTS]
3.1.1.5.5, 3.1.1.5.3.7, 3.1.1.9.1), on a domain provisioned for the test:
enabling it as an Enterprise Admin, deleted objects that keep their
attributes, restore, recycle, and the deactivated links."""

import logging
import shutil
import tempfile
import uuid

import ldb
from samba import dsdb
from samba.auth import system_session, admin_session
from samba.provision import provision
from samba.samdb import SamDB
from samba.tests import TestCase

RECYCLE_BIN_GUID = "766ddcd8-acd0-445e-f3b9-a7f9b6744f2a"


class RecycleBinTests(TestCase):

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.tempdir = tempfile.mkdtemp(prefix="recycle_bin")
        result = provision(logging.getLogger("selftest"),
                           system_session(), targetdir=cls.tempdir,
                           realm="rb.samba.example.com", domain="RBDOM",
                           hostname="rbdc", use_ntvfs=True,
                           serverrole="active directory domain controller",
                           dns_backend="NONE",
                           dom_for_fun_level=dsdb.DS_DOMAIN_FUNCTION_2008_R2)
        cls.lp = result.lp
        cls.lp.set("harden:recycle bin", "yes")
        url = cls.lp.samdb_url()
        cls.system = SamDB(url=url, session_info=system_session(cls.lp),
                           lp=cls.lp)
        domain_sid = cls.system.get_domain_sid()
        cls.samdb = SamDB(url=url, lp=cls.lp,
                          session_info=admin_session(cls.lp, domain_sid))
        cls.base_dn = cls.samdb.get_default_basedn()

        # a tombstone made before the Recycle Bin is enabled, without
        # isRecycled (as an older DC leaves them)
        cls.old_dn = cls._add_user(cls.samdb, "oldtomb")
        cls.samdb.delete(cls.old_dn)
        cls.old_guid = cls._guid_of_deleted(cls.system, "oldtomb")
        m = ldb.Message()
        m.dn = ldb.Dn(cls.system, "<GUID=%s>" % cls.old_guid)
        m["isRecycled"] = ldb.MessageElement([], ldb.FLAG_MOD_DELETE,
                                             "isRecycled")
        try:
            cls.system.modify(m, controls=["show_recycled:1",
                                           "relax:0"])
            cls.old_unrecycled = True
        except ldb.LdbError:
            cls.old_unrecycled = False

        # enabled by the Administrator (an Enterprise Admin), not only
        # by the system account
        m = ldb.Message()
        m.dn = ldb.Dn(cls.samdb, "")
        m["enableOptionalFeature"] = ldb.MessageElement(
            "%s:%s" % (cls.samdb.get_partitions_dn(), RECYCLE_BIN_GUID),
            ldb.FLAG_MOD_ADD, "enableOptionalFeature")
        cls.samdb.modify(m)

    @classmethod
    def tearDownClass(cls):
        shutil.rmtree(cls.tempdir, ignore_errors=True)
        super().tearDownClass()

    @staticmethod
    def _add_user(samdb, name, parent=None):
        dn = "CN=%s,%s" % (name, parent or ("CN=Users,%s" %
                                             samdb.get_default_basedn()))
        samdb.add({"dn": dn, "objectClass": "user",
                   "sAMAccountName": name,
                   "description": "kept by the Recycle Bin"})
        return dn

    @staticmethod
    def _guid_of_deleted(samdb, name):
        res = samdb.search(base=samdb.get_default_basedn(),
                           scope=ldb.SCOPE_SUBTREE,
                           expression="(&(isDeleted=TRUE)(name=%s*))" % name,
                           attrs=["objectGUID"],
                           controls=["show_deleted:1", "show_recycled:1"])
        assert len(res) == 1, res
        return samdb.schema_format_value("objectGUID",
                                         res[0]["objectGUID"][0]).decode()

    def _deleted(self, guid, attrs=None):
        res = self.system.search(base="<GUID=%s>" % guid,
                                 scope=ldb.SCOPE_BASE, attrs=attrs or ["*"],
                                 controls=["show_deleted:1",
                                           "show_recycled:1"])
        self.assertEqual(len(res), 1)
        return res[0]

    def _restore(self, guid, new_dn, controls=None):
        m = ldb.Message()
        m.dn = ldb.Dn(self.samdb, "<GUID=%s>" % guid)
        m["isDeleted"] = ldb.MessageElement([], ldb.FLAG_MOD_DELETE,
                                            "isDeleted")
        m["distinguishedName"] = ldb.MessageElement(
            [str(new_dn)], ldb.FLAG_MOD_REPLACE, "distinguishedName")
        self.samdb.modify(m, controls=controls or ["show_deleted:1"])

    def _new_group(self, member):
        gname = "rbg%s" % uuid.uuid4().hex[:6]
        self.samdb.newgroup(gname)
        self.samdb.add_remove_group_members(gname, [member],
                                            add_members_operation=True)
        return "CN=%s,CN=Users,%s" % (gname, self.base_dn)

    def _new_user(self, parent=None):
        name = "rb%s" % uuid.uuid4().hex[:8]
        return (name, self._add_user(self.samdb, name, parent))

    def test_enabled_in_forest(self):
        res = self.samdb.search(base=self.samdb.get_partitions_dn(),
                                scope=ldb.SCOPE_BASE,
                                attrs=["msDS-EnabledFeature"])
        values = [str(v) for v in res[0]["msDS-EnabledFeature"]]
        self.assertTrue(any("Recycle Bin Feature" in v for v in values))

    def test_old_tombstone_recycled(self):
        """Enabling the Recycle Bin turns the tombstones into recycled
        objects"""
        if not self.old_unrecycled:
            self.skipTest("could not make a tombstone without isRecycled")
        msg = self._deleted(self.old_guid, ["isRecycled"])
        self.assertEqual(str(msg["isRecycled"][0]), "TRUE")

    def test_delete_keeps_attributes(self):
        (name, dn) = self._new_user()
        self.samdb.delete(dn)
        guid = self._guid_of_deleted(self.system, name)
        msg = self._deleted(guid)
        self.assertEqual(str(msg["isDeleted"][0]), "TRUE")
        self.assertNotIn("isRecycled", msg)
        self.assertEqual(str(msg["description"][0]), "kept by the Recycle Bin")
        self.assertEqual(str(msg["msDS-LastKnownRDN"][0]), name)
        self.assertEqual(str(msg["lastKnownParent"][0]).lower(),
                         ("CN=Users,%s" % self.base_dn).lower())

    def test_restore_user(self):
        """The restore keeps the attributes and the group memberships"""
        (name, dn) = self._new_user()
        group = self._new_group(name)
        res = self.samdb.search(base=dn, scope=ldb.SCOPE_BASE,
                                attrs=["primaryGroupID", "accountExpires"])
        before = res[0]
        self.samdb.delete(dn)
        guid = self._guid_of_deleted(self.system, name)

        self._restore(guid, dn)

        res = self.samdb.search(base=dn, scope=ldb.SCOPE_BASE,
                                attrs=["description", "memberOf",
                                       "primaryGroupID", "accountExpires",
                                       "sAMAccountType", "objectCategory",
                                       "isDeleted", "isRecycled"])
        self.assertEqual(len(res), 1)
        msg = res[0]
        self.assertNotIn("isDeleted", msg)
        self.assertNotIn("isRecycled", msg)
        self.assertEqual(str(msg["description"][0]), "kept by the Recycle Bin")
        self.assertIn("sAMAccountType", msg)
        self.assertIn("objectCategory", msg)
        self.assertEqual(str(msg["primaryGroupID"][0]),
                         str(before["primaryGroupID"][0]))
        self.assertEqual(str(msg["accountExpires"][0]),
                         str(before["accountExpires"][0]))
        self.assertIn(group.lower(),
                      [str(v).lower() for v in msg.get("memberOf", [])])

    def test_restore_without_control(self):
        """The restore works without the Show Deleted control too, on
        the DN of the deleted object (as our ADWS service sends it)"""
        (name, dn) = self._new_user()
        self.samdb.delete(dn)
        guid = self._guid_of_deleted(self.system, name)
        deleted = self._deleted(guid, ["distinguishedName"])

        m = ldb.Message()
        m.dn = deleted.dn
        m["isDeleted"] = ldb.MessageElement([], ldb.FLAG_MOD_DELETE,
                                            "isDeleted")
        m["distinguishedName"] = ldb.MessageElement(
            [str(dn)], ldb.FLAG_MOD_REPLACE, "distinguishedName")
        self.samdb.modify(m)

        res = self.samdb.search(base=dn, scope=ldb.SCOPE_BASE,
                                attrs=["description"])
        self.assertEqual(str(res[0]["description"][0]),
                         "kept by the Recycle Bin")

    def test_delete_restore_delete(self):
        (name, dn) = self._new_user()
        self.samdb.delete(dn)
        guid = self._guid_of_deleted(self.system, name)
        self._restore(guid, dn)
        self.samdb.delete(dn)
        msg = self._deleted(guid, ["isDeleted", "msDS-LastKnownRDN"])
        self.assertEqual(str(msg["isDeleted"][0]), "TRUE")
        self.assertEqual([str(v) for v in msg["msDS-LastKnownRDN"]], [name])

    def test_recycle_deleted_object(self):
        """Deleting a deleted object recycles it, and a recycled object
        can't be restored"""
        (name, dn) = self._new_user()
        self.samdb.delete(dn)
        guid = self._guid_of_deleted(self.system, name)
        deleted = self._deleted(guid, ["distinguishedName"])

        self.samdb.delete(deleted.dn, controls=["show_deleted:1"])
        msg = self._deleted(guid)
        self.assertEqual(str(msg["isRecycled"][0]), "TRUE")
        self.assertNotIn("description", msg)

        # a recycled object is not visible with the Show Deleted control
        try:
            self._restore(guid, dn)
            self.fail("a recycled object was restored")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_NO_SUCH_OBJECT)
        # and can't be restored when it is (Show Recycled)
        try:
            self._restore(guid, dn, controls=["show_deleted:1",
                                              "show_recycled:1"])
            self.fail("a recycled object was restored")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_UNWILLING_TO_PERFORM)

        try:
            self.samdb.delete(msg.dn, controls=["show_deleted:1",
                                                "show_recycled:1"])
            self.fail("a recycled object was deleted")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_UNWILLING_TO_PERFORM)

    def test_restore_missing_parent(self):
        ou = "OU=rbou%s,%s" % (uuid.uuid4().hex[:6], self.base_dn)
        self.samdb.create_ou(ou)
        (name, dn) = self._new_user(parent=ou)
        self.samdb.delete(dn)
        self.samdb.delete(ou)
        guid = self._guid_of_deleted(self.system, name)
        try:
            self._restore(guid, dn)
            self.fail("restored below a deleted OU")
        except ldb.LdbError as e:
            self.assertEqual(e.args[0], ldb.ERR_NO_SUCH_OBJECT)
            self.assertIn("2089", e.args[1].upper())

    def test_deactivated_links(self):
        """The links to and from a deleted object are hidden"""
        (name, dn) = self._new_user()
        group = self._new_group(name)
        self.samdb.delete(dn)

        res = self.samdb.search(base=group, scope=ldb.SCOPE_BASE,
                                attrs=["member"])
        self.assertNotIn("member", res[0])

        res = self.samdb.search(base=group, scope=ldb.SCOPE_BASE,
                                attrs=["member"],
                                controls=["show_deactivated_link:1"])
        self.assertEqual(len(res[0]["member"]), 1)

        guid = self._guid_of_deleted(self.system, name)
        res = self.samdb.search(base="<GUID=%s>" % guid,
                                scope=ldb.SCOPE_BASE, attrs=["memberOf"],
                                controls=["show_deleted:1"])
        self.assertNotIn("memberOf", res[0])
