# domain management - infrastructure master update
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

import samba.getopt as options
from samba.auth import system_session
from samba.infrastructure import InfrastructureUpdate
from samba.netcmd import Command, CommandError, Option
from samba.samdb import SamDB


class cmd_domain_infrastructure_update(Command):
    """Update the references to the objects of the other domains of the
forest (infrastructure master).

In a multi-domain forest, the objects of a domain can reference objects
of the other domains (a user of a child domain in a group of the forest
root domain, for example). This command looks the targets of these
references up in the global catalog and updates the references whose
target was renamed or moved, and removes those whose target was
deleted, like the infrastructure master of the domain does
periodically (from the KCC).

By default, this only runs on the infrastructure master of the domain
(see 'samba-tool fsmo show').
"""
    synopsis = "%prog [options]"

    takes_options = [
        Option("-H", "--URL", help="LDB URL for database or target server",
               type=str, metavar="URL", dest="H"),
        Option("--gc", help="LDB URL of the global catalog to use "
               "(default: this DC if it is a global catalog, else another "
               "global catalog of the forest)", type=str, metavar="URL"),
        Option("--dry-run", help="Only show what would be changed",
               action="store_true", default=False),
        Option("--force", help="Run even if the DC is not the "
               "infrastructure master", action="store_true", default=False),
    ]

    takes_optiongroups = {
        "sambaopts": options.SambaOptions,
        "credopts": options.CredentialsOptions,
        "versionopts": options.VersionOptions,
    }

    def run(self, H=None, gc=None, dry_run=False, force=False,
            sambaopts=None, credopts=None, versionopts=None):
        lp = sambaopts.get_loadparm()
        creds = credopts.get_credentials(lp)
        samdb = SamDB(url=H, session_info=system_session(),
                      credentials=creds, lp=lp)
        gc_samdb = None
        if gc is not None:
            gc_samdb = SamDB(url=gc, session_info=system_session(),
                             credentials=creds, lp=lp)

        infra = InfrastructureUpdate(samdb, self.get_logger(), gc=gc_samdb)
        if not force and not infra.is_infrastructure_master():
            raise CommandError("This DC is not the infrastructure master "
                               "of its domain (use --force to run anyway)")

        if gc_samdb is None and not infra.is_gc():
            # we need the partial replicas of the other domains
            for name in infra.find_gc():
                try:
                    gc_samdb = SamDB(url="ldap://%s:3268" % name,
                                     session_info=system_session(),
                                     credentials=creds, lp=lp)
                    break
                except Exception as e:
                    self.get_logger().warning("Global catalog %s: %s" %
                                              (name, e))
            if gc_samdb is None:
                raise CommandError("This DC is not a global catalog, and "
                                   "no other global catalog answered "
                                   "(use --gc)")
            infra.gc = gc_samdb

        changes = infra.run(fix=not dry_run)
        for (dn, attr, old, new) in changes:
            if new is None:
                self.outf.write("%s: %s: remove %s\n" % (dn, attr, old))
            else:
                self.outf.write("%s: %s: %s -> %s\n" % (dn, attr, old, new))
        self.outf.write("%d reference(s) %s\n" %
                        (len(changes),
                         "to update" if dry_run else "updated"))
