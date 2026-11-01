# Unix SMB/CIFS implementation.
# Tests of "harden:sort one-level results"
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

"""The one-level LDAP searches return the children of a container
sorted by name, as a Windows DC does (Active Directory Users and Computers
shows them in that order). Run against subdom_dc, which has
"harden:sort one-level results = yes"."""

import random
import uuid

import ldb
from samba.auth import system_session
from samba.credentials import Credentials
from samba.samdb import SamDB
from samba.tests import TestCase, env_get_var_value, env_loadparm


class OneLevelOrderTests(TestCase):

    def setUp(self):
        super().setUp()
        self.lp = env_loadparm()
        creds = Credentials()
        creds.guess(self.lp)
        creds.set_username(env_get_var_value("USERNAME"))
        creds.set_password(env_get_var_value("PASSWORD"))
        creds.set_domain(env_get_var_value("DOMAIN"))
        creds.set_realm(env_get_var_value("REALM"))
        self.samdb = SamDB(url="ldap://%s" % env_get_var_value("SERVER_IP"),
                           session_info=system_session(),
                           credentials=creds, lp=self.lp)

        self.ou = "OU=order%s,%s" % (uuid.uuid4().hex[:8],
                                     self.samdb.get_default_basedn())
        self.samdb.create_ou(self.ou)
        self.addCleanup(self.samdb.delete, self.ou, ["tree_delete:1"])

        names = ["%s%02d" % (random.choice("aBcDeFgH"), i)
                 for i in range(30)]
        random.shuffle(names)
        for name in names:
            if name[0] in "aceg":
                self.samdb.create_ou("OU=%s,%s" % (name, self.ou))
            else:
                self.samdb.add({"dn": "CN=%s,%s" % (name, self.ou),
                                "objectClass": "contact"})
        self.expected = sorted(names, key=lambda n: (n.lower(), n))

    def _names(self, res):
        return [str(m.dn.get_rdn_value()) for m in res]

    def test_onelevel(self):
        res = self.samdb.search(base=self.ou, scope=ldb.SCOPE_ONELEVEL,
                                attrs=["name"])
        self.assertEqual(self._names(res), self.expected)

    def test_onelevel_paged(self):
        """The pages of a paged search follow the same order"""
        names = []
        cookie = ""
        for page in range(10):
            res = self.samdb.search(base=self.ou, scope=ldb.SCOPE_ONELEVEL,
                                    expression="(objectClass=*)",
                                    attrs=["name"],
                                    controls=["paged_results:1:7" + cookie])
            self.assertLessEqual(len(res), 7)
            names += self._names(res)
            ctrls = [str(c) for c in res.controls
                     if str(c).startswith("paged_results")]
            self.assertEqual(len(ctrls), 1)
            spl = ctrls[0].rsplit(':', 3)
            if len(spl) != 3 or not spl[-1]:
                break
            cookie = ":" + spl[-1]
        self.assertEqual(names, self.expected)

    def test_server_sort_wins(self):
        """An explicit sort control still decides"""
        res = self.samdb.search(base=self.ou, scope=ldb.SCOPE_ONELEVEL,
                                attrs=["name"],
                                controls=["server_sort:1:1:name"])
        self.assertEqual(self._names(res), list(reversed(self.expected)))
