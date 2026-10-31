Harden-Box: multi-domain forest support for Samba AD (EXPERIMENTAL)
===================================================================

> Version française : [README-fr.md](README-fr.md)

This branch of the [HardenSamba](https://github.com/HardenSamba) fork of
Samba is the start of the support of **multi-domain Active Directory
forests** (parent and child domains in the same forest) in the Samba AD
DC. The original Samba README follows below, after the
"About Samba" heading.

> **Warning: experimental, do not use in production.** Phase 1 creates a
> child domain and starts its first DC, but the DCs of the parent and of
> the child domain can not authenticate to each other yet, so they don't
> replicate. See [Known limitations](#known-limitations).

- Base: Samba `master` at commit `f6fddd2232c9` (4.26.0pre1, 2026-10-30)
- Branch: `_Harden-Box-Multi-domains-supports-phase1`
- 11 code commits for the multi-domain work, 1 commit that makes the DC
  advertise Active Directory Web Services (see
  [ADWS advertisement](#active-directory-web-services-adws-advertisement)),
  and this documentation, all on top of the base commit


Context
-------

Upstream Samba only supports single-domain forests. A Samba DC can trust
other forests (external and forest trusts), but it can't be a DC of a
child domain, and a Samba forest can't get child domains.

Some work was done between 2011 and 2015 (global catalog partial
replicas, `samba-tool domain join ... SUBDOMAIN`, a `subdom_dc` selftest
environment), but it was never finished, and the SUBDOMAIN role was
removed in 2019 (commit `5583208aed0e`, "it currently does not work").
Many places in the code still say so: "We don't support WITHIN_FOREST
yet" (KDC, winbindd), "TODO: handle multiple domains in a forest"
(LSA), and so on.

Phase 1 (this branch) brings the join of a new child domain back, fixes
everything that broke on the way, and adds a test environment, so that
the next phases (Kerberos, NTLM, global catalog, ...) can be developed
and tested.


Usage
-----

On the server that will be the first DC of the new child domain:

```
samba-tool domain join child.example.com SUBDOMAIN \
    --parent-domain=example.com \
    --server=dc1.example.com \
    --option="workgroup=CHILD" \
    -UAdministrator@EXAMPLE.COM \
    --adminpass='<password of the Administrator of the new domain>'
```

- The NetBIOS name of the new domain is taken from `workgroup` (from
  `--option` or from `smb.conf`); it is required.
- `--parent-domain` defaults to the DNS name of the new domain without
  its first label.
- The credentials must be those of an Enterprise Admin of the forest
  (the crossRef of the new domain is created on the Domain Naming Master).
- Without `--adminpass`, the Administrator password of the new domain is
  random.
- The parent DC must run this branch too (see the LSA, winbindd,
  partition and extended_dn_out changes below).

What the join does:

1. Connects to a DC of the parent domain, finds the Domain Naming Master
   and the SID of the forest root domain (from its crossRef).
2. Creates the server object, then the crossRef of the new domain and its
   nTDSDSA object on the naming master with `DsAddEntry`. The nCName
   carries the GUID and the SID of the new domain.
3. Replicates the Schema and Configuration NCs.
4. Provisions the new domain NC locally (`FILL_SUBDOMAIN`), with its
   DomainDnsZones partition.
5. Creates the two-way `WITHIN_FOREST` trust: the parent side with LSA
   `CreateTrustedDomainEx2` on the parent DC, the child side and the
   trust account locally.
6. In the parent DNS zone, adds the delegation of the child zone (NS +
   glue A/AAAA records) and the `<ntdsguid>._msdcs.<forest>` CNAME.


What works
----------

Tested with the `subdom_dc` selftest environment (a child domain
`sub.samba.example.com` of the forest of `ad_dc_ntvfs`,
`samba.example.com`):

- the join completes, and the child DC starts (LDAP, KDC, DNS, winbindd);
- the rootDSE of the child DC shows its own domain NC and the forest
  root, Configuration and Schema NCs;
- both DCs see the crossRef of the child domain, with `trustParent`
  pointing to the crossRef of the parent;
- both DCs have the two-way `WITHIN_FOREST` trust, with the right SIDs;
- the child domain has no Enterprise Admins, Schema Admins or Enterprise
  Read-only Domain Controllers groups (they only exist in the forest
  root);
- the security descriptors of the child domain use the Enterprise Admins
  and Schema Admins groups of the forest root domain;
- the DCs of the parent domain keep working once the forest has a child
  domain (winbindd used to exit, and a second DC of the parent domain
  could not start any more).


Known limitations
-----------------

These are the next phases of the work:

- **Kerberos across the forest:** the KDC still refuses `WITHIN_FOREST`
  trusts (`source4/kdc/db-glue.c`: "We don't support WITHIN_FOREST
  yet") and does not give referrals to the other domains of the forest.
  So the DCs of the two domains can't authenticate to each other, and
  the replication between them fails (`NT_STATUS_LOGON_FAILURE`).
- **NTLM pass-through:** winbindd still ignores the domains of the
  forest (`source3/winbindd/winbindd_util.c`,
  `winbindd_msrpc.c`).
- **LSA LookupNames / LookupSids** only resolve names of the local
  domain (`source4/rpc_server/lsa/lsa_lookup.c`, "TODO: handle multiple
  domains in a forest").
- **Netlogon:** `DsrEnumerateDomainTrusts` always returns
  `parent_index = 0`.
- **Cross-domain group memberships:** the Enterprise Admins of the forest
  root are not added to the Administrators and to the Denied RODC
  Password Replication groups of the child domain yet.
- **Global catalog:** the parent DC creates the (uninstantiated) partial
  replica of the new domain NC, but the replication of the partial
  attribute set between domains is not tested yet.
- **DNS:** the ForestDnsZones partition is not replicated to the child DC.
- Only tested with Samba DCs on both sides. A Samba child domain under a
  Windows forest root, and the reverse, are not tested.
- `subdom_dc` is not in `script/autobuild.py` yet.


Active Directory Web Services (ADWS) advertisement
--------------------------------------------------

The HardenSamba ADWS service
([HardenSamba/samba-adws](https://github.com/HardenSamba/samba-adws))
answers on TCP 9389, but a Windows client only uses it if the domain
controller says it has ADWS. That announcement is made by Samba itself,
so this branch carries it in the source code (it used to be shipped as
`patches/samba/0001-advertise-adws-in-the-cldap-netlogon-response.patch`
in samba-adws, branch `fix/get-forwards-visibility-controls`, commit
`33e56b9`; no patch is needed any more).

How it works:

- A client looking for an ADWS controller (the Active Directory
  Administrative Center, or the ActiveDirectory PowerShell module used
  without `-Server`) calls `DsGetDcName` with `DS_WEB_SERVICE_REQUIRED`
  (`0x00100000`, sent by the client).
- The locator only keeps the controllers whose CLDAP netlogon response
  has `DS_SERVER_WEBSERV` (`0x00002000`, also named
  `NBT_SERVER_ADS_WEB_SERVICE`) in `server_type`. Samba never set it.
- `fill_netlogon_samlogon_response()`
  (`source4/dsdb/samdb/ldb_modules/netlogon.c`) now sets it when
  `smb.conf` says so. That function builds the answer of the CLDAP ping
  (UDP 389) and of the rootDSE `netlogon` attribute (`rootdse.c`), of the
  NetBIOS mailslot (`nbt_server`) and of the Netlogon RPC server.

Enable it only on a DC where the ADWS service runs on 9389:

```ini
[global]
    adws:advertise = yes
```

It is **off by default**: a controller announcing ADWS with nothing
listening on 9389 is worse than one that stays quiet, as the client
connects to it and fails instead of looking elsewhere. It is a generic
(parametric) parameter, so no IDL, parameter table or documentation
regeneration is needed. Restart samba after changing it.

Checking it, on the DC (the `server_type` of its own netlogon reply):

| `adws:advertise` | `server_type` | `DS_SERVER_WEBSERV` (0x2000) |
|---|---|---|
| absent or `no` | `0x000013fd` | not set |
| `yes` | `0x000033fd` | set |

(values measured on this branch on a freshly provisioned domain, the same
as the ones measured with the samba-adws patch on Samba 4.17 and 4.22)

```python
from samba.credentials import Credentials
from samba.dcerpc import nbt
from samba.net import Net
from samba.param import LoadParm

lp = LoadParm(); lp.load_default()
creds = Credentials(); creds.guess(lp)
reply = Net(creds=creds, lp=lp).finddc(address="127.0.0.1",
                                       flags=nbt.NBT_SERVER_LDAP | nbt.NBT_SERVER_DS)
print("0x%08x" % reply.server_type,
      bool(reply.server_type & nbt.NBT_SERVER_ADS_WEB_SERVICE))
```

And on a joined Windows client (`/force` bypasses the locator cache):

```
nltest /dsgetdc:<REALM> /ws /force
```

which returns the controller with `WS` in its flags, instead of
`ERROR_NO_SUCH_DOMAIN` (1355) when the announcement is off.


Changes in detail
-----------------

| # | Commit | Files | What and why |
|---|--------|-------|--------------|
| 1 | `s4:dsdb:partition: let partitions created at runtime create their database` | `source4/dsdb/samdb/ldb_modules/partition_init.c` | The sam.ldb of a running DC is opened with `LDB_FLG_DONT_CREATE_DB`, so a partition created at runtime (the partial replica of a new domain on a global catalog, from `DsAddEntry`) could not create its database and the join failed. The flag is now cleared only while connecting the backend of a partition created by `partition_create()`. |
| 2 | `s4:dsdb: keep GUID and SID on a new partial replica NC head` | `source4/dsdb/common/util.c` | `dsdb_create_partial_replica_NC()` now keeps the GUID and SID given with the nCName of the new crossRef. Without the SID, `dsdb_trust_parse_crossref_info()` failed for the new domain, which broke winbindd and LSA on every DC. |
| 3 | `s4:dsdb:descriptor: resolve EA, SA, RO and EK with the forest root SID` | `source4/dsdb/common/util.c`, `libcli/security/sddl.c`, `libcli/security/sddl.h`, `source4/dsdb/samdb/ldb_modules/descriptor.c` | Enterprise Admins, Schema Admins, Enterprise Read-only DCs and Enterprise Key Admins only exist in the forest root domain, but the descriptor module resolved them with the local domain SID. New `samdb_forest_root_domain_sid()` (SID of the nCName of the forest root crossRef) and `sddl_decode_forest()`, used for the default owner/group and the schema `defaultSecurityDescriptor`. No change in the forest root domain. |
| 4 | `s4:dsdb:extended_dn_out: keep one-way links to NCs of other domains` | `source4/dsdb/samdb/ldb_modules/extended_dn_out.c` | DN values whose target is not found locally were hidden, as links to deleted objects. In a multi-domain forest many of them point to the NC of another domain (nCName of its crossRef, msDS-hasMasterNCs of its DCs), like Windows phantoms. They are now kept when the most specific NC of the forest containing the target is not one we hold. |
| 5 | `s4:dsdb:util_trusts: dsdb_trust_xref_tdo_info() is about the forest root` | `source4/dsdb/common/util_trusts.c` | It searched the crossRef of the local domain with a filter only matching the forest root, so it always failed on a child DC. It now uses the root base DN. |
| 6 | `s4:rpc_server:lsa: allow WITHIN_FOREST trusts with our parent or child` | `source4/rpc_server/lsa/dcesrv_lsa.c` | `CreateTrustedDomain*` refused every `WITHIN_FOREST` trust. It is now accepted when the domain is in our Partitions container (AD domain crossRef with the same dnsRoot and NetBIOS name, same SID if known) and is our direct parent or child (`trustParent`). |
| 7 | `s3:winbindd: don't abort on trusts ignored by add_trusted_domains_dc()` | `source3/winbindd/winbindd_util.c` | PIM, `WITHIN_FOREST` and cross-organization trusts are skipped, but a second loop then failed with "Can't find winbindd domain", which made winbindd (and samba) exit on any DC with such a trust, including the parent DCs. |
| 8 | `python:kcc: take the GUID and SID of a NC from its crossRef` | `python/samba/kcc/kcc_utils.py` | The KCC loaded every NC of the forest with a base search on its head, which fails for the NC of another domain. The GUID and SID now come from the nCName of the crossRef. |
| 9 | `python:provision: fix the provision of a child domain (FILL_SUBDOMAIN)` | `source4/setup/provision_users.ldif`, `source4/setup/provision_users_forest_root.ldif` (new), `source4/setup/provision_users_forest_root_modify.ldif` (new), `python/samba/provision/__init__.py`, `python/samba/provision/sambadns.py`, `python/samba/descriptor.py` | The forest root groups (and their memberships) moved to their own LDIF files, only used for the forest root (`FILL_FULL`). The descriptors of the domain NC objects are rewritten with the forest root SID (`fix_forest_root_sids()`). Fixed a `NameError` (`managedservice_descr`) with `FILL_SUBDOMAIN`. The crossRef of DomainDnsZones (Configuration NC) is created as system, as the Administrator of a child domain has no rights there. No change for `FILL_FULL` and `FILL_DRS`. |
| 10 | `samba-tool domain join: restore the SUBDOMAIN role (EXPERIMENTAL)` | `python/samba/join.py`, `python/samba/netcmd/domain/join.py` | `join_subdomain()`, `get_naming_master()` and the `SUBDOMAIN` role are back, with `--parent-domain` and `--adminpass`. Fixed: the trust is with the parent domain (not the forest root) on the parent DC; GUID and SID of the new domain in the nCName; nothing touched in the parent domain NC (machine account, DFSR topology, RID manager); DomainDnsZones not listed in the nTDSDSA object; LSA over named pipes; full LMDB map size for the new domain; local trust account created with the LSA control; DNS delegation and `_msdcs` CNAME; the exception is reported before the cleanup of a failed join. |
| 11 | `selftest: add the subdom_dc environment and samba.tests.subdomain` | `selftest/target/Samba4.pm`, `selftest/target/Samba.pm`, `source4/selftest/tests.py`, `python/samba/tests/subdomain.py` (new) | The `subdom_dc` environment is back (removed in 2019), with the `SUBDOM_DC_SERVER` and `PARENT_*` variables, and `samba.tests.subdomain` checks the result of the join on both DCs (6 tests). |
| 12 | `s4:dsdb:netlogon: advertise Active Directory Web Services` | `source4/dsdb/samdb/ldb_modules/netlogon.c` | Sets `DS_SERVER_WEBSERV` in the `server_type` of the netlogon response when `smb.conf` has `adws:advertise = yes` (off by default), so that Windows clients find the HardenSamba ADWS service through the DC locator. Taken from the samba-adws patch, see [ADWS advertisement](#active-directory-web-services-adws-advertisement). Independent of the multi-domain work: it touches no file changed by commits 1 to 11. |


Testing
-------

Build with the selftest enabled, then run the multi-domain tests:

```
./configure --enable-selftest --enable-debug
make -j$(nproc)
make test TESTS="samba.tests.subdomain"
```

To get a shell in the test environment:

```
SELFTEST_TESTENV=subdom_dc make testenv
```

Results on this branch:

- `samba.tests.subdomain`: 6/6 OK.
- No regression (ALL OK) in: `samba.tests.provision`, `samba.tests.dsdb`,
  `samba.tests.sddl`, `samba.tests.security_descriptors`,
  `samba4.blackbox.provision.py`, `samba4.blackbox.upgradeprovision.current`,
  `samba4.blackbox.join_ldapcmp` (5 environments),
  `samba4.blackbox.dbcheck.release-4-5-0-pre1` (+ quick, + links),
  `samba4.blackbox.trust_utils(fl2008r2dc)`, and
  `samba4.drs.repl_secdesc` / `samba4.drs.repl_move` on `vampire_dc`
  (a second DC of the parent domain, with a child domain already in the
  forest).
- ADWS advertisement: `samba.tests.getdcname` (which checks that the
  bit is off by default) OK, and the `server_type` table above measured
  with `adws:advertise` absent, `no` and `yes`.
- The full Samba test suite was not run.


Roadmap
-------

1. **Kerberos within the forest:** inter-realm keys of the `WITHIN_FOREST`
   trusts in the KDC (`samba_kdc_trust_message2entry()`), referrals to
   the other domains (routing through the parent), PAC and SID filtering
   inside the forest. This will let the DCs authenticate to each other
   and replicate.
2. **winbindd, LSA, Netlogon:** domains of the forest in winbindd
   (`NETR_TRUST_FLAG_IN_FOREST`, NTLM pass-through), multi-domain
   LookupNames/LookupSids, `parent_index` in
   `DsrEnumerateDomainTrusts`.
3. **Replication between domains:** Configuration and Schema in both
   directions, ForestDnsZones, KCC topology across domains.
4. **Global catalog:** partial replicas of the other domains (partial
   attribute set), universal groups in tokens, GC searches on port 3268.
5. **Cross-domain references:** memberships of forest root groups in the
   child domains, Infrastructure Master duties.
6. **Interoperability:** Samba child domain under a Windows forest root
   and the reverse; `subdom_dc` in autobuild.


Submitting upstream
-------------------

The commits follow the Samba conventions (one change per commit,
`component: summary` subjects). Before sending them to the Samba
project, each commit needs a `Signed-off-by:` line from its author and
the reviews described in [README.contributing](README.contributing).


About Samba
===========

Samba is the standard Windows interoperability suite of
programs for Linux and Unix.
Samba is Free Software licensed under the GNU General Public License and
the Samba project is a member of the Software Freedom Conservancy.
Since 1992, Samba has provided secure, stable and fast file and print services
for all clients using the SMB/CIFS protocol, such as all versions of DOS
and Windows, OS/2, Linux and many others.
Samba is an important component to seamlessly integrate Linux/Unix Servers and
Desktops into Active Directory environments. It can function both as a
domain controller or as a regular domain member.


For the AD DC implementation a full HOWTO is provided at:
      https://wiki.samba.org/index.php/Samba4/HOWTO

Community guidelines can be read at:
      https://wiki.samba.org/index.php/How_to_do_Samba:_Nicely

This software is freely distributable under the GNU public license, a
copy of which you should have received with this software (in a file
called COPYING).



CONTRIBUTIONS
=============

Please see https://wiki.samba.org/index.php/Contribute
for detailed set-by-step instructions on how to submit a patch
for Samba via GitLab.

Samba's GitLab mirror is at https://gitlab.com/samba-team/samba

OUR CONTRIBUTORS
================

See https://www.samba.org/samba/team/ for details of the Samba Team,
as well as details of all those currently active in Samba development.

If you like a particular feature then look through the git change-log
(on the web at https://gitweb.samba.org/?p=samba.git;a=summary) and see
who added it, then send them an email.

Remember that free software of this kind lives or dies by the response
we get. If no one tells us they like it then we'll probably move onto
something else.


MORE INFO
=========

DOCUMENTATION
-------------

There is quite a bit of documentation included with the package,
including man pages and the wiki at https://wiki.samba.org

If you would like to help with our documentation, please contribute
that improved content to the wiki, we are moving as much content there
as possible.


MAILING LIST
------------

Please do NOT send subscription/unsubscription requests to the lists!

There is a mailing list for discussion of Samba.  For details go to
<https://lists.samba.org/> or send mail to <samba-subscribe@lists.samba.org>

There is also an announcement mailing list where new versions are
announced.  To subscribe go to <https://lists.samba.org/> or send mail
to <samba-announce-subscribe@lists.samba.org>.  All announcements also
go to the samba list, so you only need to be on one.

For details of other Samba mailing lists and for access to archives, see
<https://lists.samba.org/>


MAILING LIST ETIQUETTE
----------------------

A few tips when submitting to this or any mailing list.

1. Make your subject short and descriptive. Avoid the words "help" or
   "Samba" in the subject. The readers of this list already know that
   a) you need help, and b) you are writing about samba (of course,
   you may need to distinguish between Samba PDC and other file
   sharing software). Avoid phrases such as "what is" and "how do
   i". Some good subject lines might look like "Slow response with
   Excel files" or "Migrating from Samba PDC to NT PDC".

2. If you include the original message in your reply, trim it so that
   only the relevant lines, enough to establish context, are
   included. Chances are (since this is a mailing list) we've already
   read the original message.

3. Trim irrelevant headers from the original message in your
   reply. All we need to see is a) From, b) Date, and c) Subject. We
   don't even really need the Subject, if you haven't changed
   it. Better yet is to just preface the original message with "On
   [date] [someone] wrote:".

4. Please don't reply to or argue about spam, spam filters or viruses
   on any Samba lists. We do have a spam filtering system that is
   working quite well thank you very much but occasionally unwanted
   messages slip through. Deal with it.

5. Never say "Me too." It doesn't help anyone solve the
   problem. Instead, if you ARE having the same problem, give more
   information. Have you seen something that the other writer hasn't
   mentioned, which may be helpful?

6. If you ask about a problem, then come up with the solution on your
   own or through another source, by all means post it. Someone else
   may have the same problem and is waiting for an answer, but never
   hears of it.

7. Give as much *relevant* information as possible such as Samba
   release number, OS, kernel version, etc...

8. RTFM. Google.


WEBSITE
-------

A Samba website has been setup with lots of useful info. Connect to:

https://www.samba.org/

As well as general information and documentation, this also has searchable
archives of the mailing list and links to other useful resources such as
the wiki.
