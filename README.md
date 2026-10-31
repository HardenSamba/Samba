Harden-Box: multi-domain forest support for Samba AD (EXPERIMENTAL)
===================================================================

> Version française : [README-fr.md](README-fr.md)

This branch of the [HardenSamba](https://github.com/HardenSamba) fork of
Samba adds the support of **multi-domain Active Directory forests**
(parent and child domains in the same forest) to the Samba AD DC. The
original Samba README follows below, after the "About Samba" heading.

> **Warning: experimental, do not use in production.** Phase 1 created
> a child domain and started its first DC. Phase 2 makes the two domains
> work together: Kerberos and NTLM across the domains, LSA name and SID
> lookups, the forest root groups in the child domain, replication in
> both directions and a global catalog that holds the child domain.
> Several things are still missing, see
> [Known limitations](#known-limitations).

- Base: Samba `master` at commit `f6fddd2232c9` (4.26.0pre1, 2026-10-30)
- Branch: `_Harden-Box-Multi-domains-supports-phase2`, on top of
  `_Harden-Box-Multi-domains-supports-phase1`
- Phase 1: 11 code commits for the multi-domain work, 1 commit that
  makes the DC advertise Active Directory Web Services (see
  [ADWS advertisement](#active-directory-web-services-adws-advertisement))
  and its documentation
- Phase 2: 15 code commits (numbered 13 to 27 in
  [Changes in detail](#changes-in-detail)) and this documentation


Context
-------

Upstream Samba only supports single-domain forests. A Samba DC can trust
other forests (external and forest trusts), but it can't be a DC of a
child domain, and a Samba forest can't get child domains.

Some work was done between 2011 and 2015 (global catalog partial
replicas, `samba-tool domain join ... SUBDOMAIN`, a `subdom_dc` selftest
environment), but it was never finished, and the SUBDOMAIN role was
removed in 2019 (commit `5583208aed0e`, "it currently does not work").
Many places in the code still said so: "We don't support WITHIN_FOREST
yet" (KDC, winbindd), "TODO: handle multiple domains in a forest"
(LSA), and so on.

- **Phase 1** brought the join of a new child domain back, fixed
  everything that broke on the way, and added a test environment.
- **Phase 2** (this branch) handles the intra-forest
  (`WITHIN_FOREST`) trusts in the KDC, in winbindd, in the NTLM
  pass-through and in LSA, gives the forest root groups their place in
  the child domain, lets the DCs of the two domains replicate, and lets
  a global catalog hold the other domain.


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
- The DCs of the parent domain must run this branch too: the KDC,
  winbindd, LSA, the replication and the KCC changes are needed on both
  sides.

What the join does:

1. Connects to a DC of the parent domain, finds the Domain Naming Master
   and the SID of the forest root domain (from its crossRef).
2. Creates the server object, then the crossRef of the new domain and its
   nTDSDSA object on the naming master with `DsAddEntry`. The nCName
   carries the GUID and the SID of the new domain.
3. Replicates the Schema and Configuration NCs.
4. Provisions the new domain NC locally (`FILL_SUBDOMAIN`), with its
   DomainDnsZones partition.
5. Adds the Enterprise Admins group of the forest root to the
   Administrators of the new domain, and Enterprise Admins and Schema
   Admins to its Denied RODC Password Replication Group (phase 2).
6. Creates the two-way `WITHIN_FOREST` trust: the parent side with LSA
   `CreateTrustedDomainEx2` on the parent DC, the child side and the
   trust account locally.
7. In the parent DNS zone, adds the delegation of the child zone (NS +
   glue A/AAAA records) and the `<ntdsguid>._msdcs.<forest>` CNAME.

Once the new DC runs, replicate the Configuration NC in both directions
(the KCC of each DC then keeps it in sync), for example from the new
DC:

```
samba-tool drs replicate <child DC> <parent DC> CN=Configuration,DC=example,DC=com \
    -UAdministrator@EXAMPLE.COM
samba-tool drs replicate <parent DC> <child DC> CN=Configuration,DC=example,DC=com \
    -UAdministrator@EXAMPLE.COM
```

On a global catalog of another domain, the KCC then adds the partial
replica of the new domain (`samba-tool drs kcc <GC>` runs it at once).


What works
----------

Tested with the `subdom_dc` selftest environment (a child domain
`sub.samba.example.com`, NetBIOS `SAMBASUBDOM`, of the forest of
`ad_dc_ntvfs`, `samba.example.com`, NetBIOS `SAMBADOMAIN`; the parent
DC is a global catalog).

Phase 1:

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
  domain.

Phase 2:

- **Kerberos** in both directions: the Administrator of each domain gets
  a ticket for the LDAP service of the DC of the other domain (cross-realm
  referral from its own KDC), and the DCs authenticate to each other;
- **NTLM** in both directions: NTLM binds of each Administrator on the DC
  of the other domain, `wbinfo -a` and `wbinfo -m` (the other domain is
  listed as "In-Forest");
- **LSA** `LookupNames` and `LookupSids` on both DCs, for both domains:
  `DOMAIN\name`, `name@dns.domain` (UPN), `DOMAIN\` and the SIDs;
- **forest root groups:** in the child domain, Enterprise Admins of the
  root is a member of `BUILTIN\Administrators` and of the Denied RODC
  Password Replication Group (with Schema Admins); the Administrator of
  the forest root gets `BUILTIN\Administrators` in its token on the child
  DC, with Kerberos and with NTLM;
- **replication:** `DsReplicaSync` of the Configuration NC in both
  directions, as the Administrator of the forest root;
- **global catalog:** the KCC of the parent DC (a GC) adds the partial
  replica of the child domain NC (`hasPartialReplicaNCs`), which
  replicates (partial attribute set, no secrets), is instantiated
  (`instanceType` 9: NC head, NC above) and is searchable on port 3268:
  a search from the root of the forest finds the objects of both
  domains.


How phase 2 works
-----------------

**Trust routing table** (`source4/dsdb/common/util_trusts.c`). The KDC,
LSA and the NTLM code find "which domain owns this name or SID" in a
routing table built from the trusted domain objects and the crossRefs.
On a child DC it could not be built (the GUID and SID of the parent
crossRef were read without the extended DN, so the KDC failed with
`EINVAL`). And the forest information of our own domain lists the top
level names of the whole forest, so every name of the forest was routed
to our own domain. The exact names and SIDs of the domains we have a
direct (parent-child) trust with now win over these top level names.

**Kerberos** (`source4/kdc/db-glue.c`, `pac-glue.c`). The inter-realm
keys of a `WITHIN_FOREST` trust come from `trustAuthIncoming` /
`trustAuthOutgoing`, as for the other uplevel trusts. A request for a
principal of another domain of the forest gets a referral to the next
domain on the way, except when the server principal exists here: the
domains of a forest share their namespace (the DRS SPNs of all the DCs
use the `_msdcs` zone of the forest root), so a local SPN stays local.
Claims are only transformed or cleared when they cross a forest
boundary, not between the domains of a forest.

**NTLM pass-through** (`source3/winbindd/winbindd_util.c`,
`source4/auth/ntlm/auth_sam.c`, `libcli/auth/smbencrypt.c`). On a DC,
winbindd now adds the domains of its forest (`NETR_TRUST_FLAG_IN_FOREST`)
and reaches them through their trust account. The SAM authentication
method hands the users of another domain of the forest over to
winbindd, which sends them to a DC of that domain over Netlogon. On the
receiving side, the pass-through domain name validation of NTLMv2
(MS-NRPC 3.5.4.5.1.1) accepts a secure channel from a domain of the
forest when the client targeted that domain (or another domain of the
forest), and refuses it when it targeted our own domain.

**LSA lookups** (`source4/rpc_server/lsa/lsa_lookup.c`,
`source3/winbindd/winbindd_msrpc.c`, `winbindd_util.c`). The names and
SIDs of the other domains of the forest go through winbindd, like those
of the trusted forests. On a DC, winbindd asks the DC of the domain that
holds them with `LsapLookupGC` (level 4,
`LSA_LOOKUP_NAMES_UPLEVEL_TRUSTS_ONLY`). winbindd routes a namespace to
the domain with that exact name before looking at the top level names of
the forest. A UPN is no longer cracked by the BUILTIN view: for another
domain it failed (we don't hold its NC) and aborted the whole lookup.

**Forest root groups** (`python/samba/join.py`). Windows references the
groups of the root domain directly (phantoms). Samba can't store a link
to an object of a NC it doesn't hold, so the join creates
`foreignSecurityPrincipal` objects for Enterprise Admins and Schema
Admins of the root and adds them to the groups of the child domain. The
token of a member of Enterprise Admins gets the child domain groups
through them, as for the members of a trusted forest.

**Replication between domains** (`source4/dsdb/common/util.c`,
`source4/dsdb/repl/`). The `serverReference` of the server object of a
DC of another domain, and the `msDS-HasDomainNCs` of its nTDSDSA object,
point to its domain NC, which we don't hold. Such links were dropped
during replication ("same NC" was assumed when the NC of the target
could not be found); they are now kept as cross-NC links. The DRS server
accepts a DC of another domain of the forest (`DsReplicaSync`,
`DsReplicaUpdateRefs`) when its nTDSDSA object hosts the domain of the
caller's SID (`msDS-HasDomainNCs`), as it can't follow its
`serverReference`. The replication service reloads its partitions when
the KCC added one.

**Global catalog** (`python/samba/kcc/`,
`source4/dsdb/samdb/ldb_modules/repl_meta_data.c`). The KCC of a GC now
adds the partial replica of every other domain NC of the forest to
`hasPartialReplicaNCs`, and loads the NCs it doesn't hold from their
crossRef. The partial attribute set replication already existed. The
placeholder NC head created with the crossRef of a new domain
(`instanceType` 11: NC head, uninstantiated, NC above) is now replaced by
the first replication of the NC: its local metadata used to win, so it
stayed uninstantiated forever.

**dbcheck** (`python/samba/dbchecker.py`). The NC head of a child domain
has no parent on its own DCs, and the Deleted Objects container of a
partial replica only has the attributes of the partial attribute set.
Neither is an error any more.


Known limitations
-----------------

- **No phantoms:** Samba can't store a reference to an object of a NC
  it doesn't hold. The forest root groups are referenced in the child
  domain through `foreignSecurityPrincipal` objects (Windows uses
  phantoms). Adding a user or group of one domain to a group of another
  domain of the forest, the universal groups of the other domains in the
  tokens (from the GC) and the Infrastructure Master duties are not done
  nor tested.
- **Backlinks on a global catalog:** a link replicated before the partial
  replica of its target (e.g. the `serverReference` of the DCs of the
  child domain, in the Configuration NC) has no backlink on the copy of
  the target in the partial replica. `samba-tool dbcheck` reports it.
- **Netlogon:** `DsrEnumerateDomainTrusts` always returns
  `parent_index = 0`.
- **DNS:** the ForestDnsZones partition is not replicated to the child
  DC.
- **SID filtering** inside the forest was not reviewed.
- Only a forest root and one child domain, with one DC each, were
  tested: no grandchild or tree root domain (referrals over more than one
  trust), no second DC or RODC in the child domain, no schema change
  replicated from the root to the child.
- Only tested with Samba DCs on both sides. A Samba child domain under a
  Windows forest root, and the reverse, are not tested.
- **Global catalog of a child domain:** a GC only gets the partial
  replica of the domains created after it joined (the uninstantiated NC
  head comes with the crossRef of the new domain). A GC of a child
  domain does not hold the forest root domain yet.
- **KCC verification:** `samba.tests.kcc` on `promoted_dc` (a third DC
  of the parent domain) fails when the forest has a child domain: seen
  from that DC, the graph of the connection objects of the site is not
  connected until the KCC of the other DCs has run. It failed in the
  same way (and also on `vampire_dc`) with the phase 1 code.
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

### Phase 1

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

### Phase 2

| # | Commit | Files | What and why |
|---|--------|-------|--------------|
| 13 | `s4:dsdb:util_trusts: read the parent crossRefs with their extended DN` | `source4/dsdb/common/util_trusts.c` | `dsdb_trust_crossref_tdo_info()` takes the GUID and SID of the forest root and parent domains from the nCName of their crossRef, but read them without `DSDB_SEARCH_SHOW_EXTENDED_DN`. The routing table could not be loaded on a child DC, so the KDC failed with `EINVAL` for every cross-realm request. |
| 14 | `s4:dsdb:util_trusts: prefer the direct trusts of the forest` | `source4/dsdb/common/util_trusts.c` | The forest information of our own domain lists the top level names of the whole forest, so `dsdb_trust_routing_by_name()`, `dsdb_trust_domain_by_name()` and `dsdb_trust_domain_by_sid()` returned our own domain for the names and SIDs of the other domains. An exact match of the name or SID of a `WITHIN_FOREST` trust now wins. |
| 15 | `s4:kdc: support WITHIN_FOREST trusts` | `source4/kdc/db-glue.c`, `source4/kdc/pac-glue.c` | The KDC refused the inter-realm keys of the intra-forest trusts and never referred a client to another domain of the forest. The keys now come from the trust like for the other uplevel trusts; a principal of another domain gets a referral, unless the server principal exists locally (shared namespace of the forest). Claims are not cleared inside the forest. |
| 16 | `s4:dsdb: a link to a NC we don't hold is not in the same NC` | `source4/dsdb/common/util.c` | `dsdb_objects_have_same_nc()` returned true when the NC of the target could not be found, so the replicated links to the domain NC of another domain (`serverReference`, `msDS-HasDomainNCs`) were dropped as "unknown but up to date". |
| 17 | `s4:dsdb: accept the DSA of a DC of another domain of the forest` | `source4/dsdb/common/util.c` | `dsdb_validate_dsa_guid()` follows the `serverReference` of the server object to check the caller of `DsReplicaSync` / `DsReplicaUpdateRefs`; for a DC of another domain it points to a NC we don't hold. Such a DSA is now accepted when the domain NC it hosts (`msDS-HasDomainNCs`) is the domain of the caller's SID, and is not ours. |
| 18 | `samba-tool domain join: add the forest root groups to the new child domain` | `python/samba/join.py` | `join_add_forest_root_memberships()`: `foreignSecurityPrincipal` objects for Enterprise Admins and Schema Admins of the forest root; Enterprise Admins in `BUILTIN\Administrators`, both in the Denied RODC Password Replication Group, as a Windows child domain has them. |
| 19 | `s3:winbindd: support the domains of our forest on a DC` | `source3/winbindd/winbindd_util.c`, `source3/winbindd/winbindd_msrpc.c` | The `WITHIN_FOREST` trusts are added with `NETR_TRUST_FLAG_IN_FOREST` instead of being ignored. A DC looks up the names and SIDs of the other domains of its forest with `LsapLookupGC` (level 4). `find_routing_from_namespace_noinit()` matches the exact domain names before the top level names of the forest information. |
| 20 | `s4:auth:ntlm: hand the users of the other domains of the forest to winbindd` | `source4/auth/ntlm/auth_sam.c` | `authsam_want_check()` claimed every user of the forest, and then failed to find the users of the other domains in the local SAM. They are now left to the winbind authentication method (NTLM pass-through). |
| 21 | `libcli:auth: validate the NTLMv2 domain names over WITHIN_FOREST trusts` | `libcli/auth/smbencrypt.c` | `NTLMv2_RESPONSE_verify_trust()` returned `NT_STATUS_NOT_SUPPORTED` for the intra-forest trusts. New `NTLMv2_RESPONSE_verify_within_forest()`: the NTLMv2 exchange must target the trusted domain or another domain of the forest, never our own domain (MS-NRPC 3.5.4.5.1.1). |
| 22 | `s4:rpc_server:lsa: look up the names and SIDs of the other domains of the forest` | `source4/rpc_server/lsa/lsa_lookup.c` | The account view only answers for our own domain; the names and SIDs of the other domains of the forest go to the winbind view (winbindd), which used to ignore every `WITHIN_FOREST` domain. The BUILTIN view no longer cracks UPNs, which failed for the UPNs of another domain and aborted the lookup. |
| 23 | `python:kcc: a global catalog holds a partial replica of every domain` | `python/samba/kcc/__init__.py`, `python/samba/kcc/kcc_utils.py` | New `refresh_gc_partial_replicas()`: on a GC, adds to `hasPartialReplicaNCs` the domain NCs of the forest that are missing and whose (uninstantiated) NC head we have, created with the crossRef of a new domain; a failure no longer aborts the KCC run. `Partition.should_be_present()` also wants the partial replicas listed in `hasPartialReplicaNCs`. The NCs we don't hold (DomainDnsZones of another domain...) are loaded from their crossRef. The intra-site graph of a NC only takes the existing connections from the DCs that hold it: in a multi-domain site, the connections from the DCs of another domain broke the KCC graph verification. |
| 24 | `s4:dsdb:repl: load the partitions added by the KCC` | `source4/dsdb/repl/drepl_service.c`, `source4/dsdb/repl/drepl_periodic.c` | The replication service only refreshed the partitions it knew, so a partial replica added by the KCC was never replicated and `DsReplicaSync` on it failed with `WERR_DS_DRA_BAD_NC`. The periodic run and `DsReplicaSync` now reload the partitions. |
| 25 | `s4:dsdb:repl_meta_data: instantiate the NC head of a new partial replica` | `source4/dsdb/samdb/ldb_modules/repl_meta_data.c` | The NC head placeholder of a partial replica (`instanceType` 11, uninstantiated) kept its value after the first replication, as its local metadata was newer. The replicated NC head now always replaces it (`instanceType` without `INSTANCE_TYPE_UNINSTANT`, `INSTANCE_TYPE_NC_ABOVE` as computed locally). |
| 26 | `python:dbcheck: child domain NC heads and partial replicas are fine` | `python/samba/dbchecker.py` | No "parent object not found" for a NC head (the NC head of a child domain has no parent on its DCs); the Deleted Objects container of a partial replica is not checked (it only has the partial attribute set). |
| 27 | `selftest: replicate subdom_dc with its parent, test the cross-domain features` | `selftest/target/Samba4.pm`, `python/samba/tests/subdomain.py` | `setup_subdom_dc()` replicates the Configuration NC in both directions. 8 new tests in `samba.tests.subdomain` (Kerberos, NTLM, forest root groups and token, LSA, replication, KCC, global catalog). |


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

`samba.tests.subdomain` (`python/samba/tests/subdomain.py`) has 14
tests: the 6 of phase 1 (state left by the join on both DCs) and 8 for
phase 2:

| Test | Checks |
|------|--------|
| `test_kerberos_cross_domain` | LDAP binds with Kerberos of both Administrators on both DCs; each is a member of the Domain Users of its own domain |
| `test_ntlm_cross_domain` | the same with NTLM |
| `test_forest_root_memberships` | Enterprise Admins of the root in `BUILTIN\Administrators` of the child, Enterprise Admins and Schema Admins in its Denied RODC Password Replication Group |
| `test_forest_root_admin_token` | on the child DC, the token of the root Administrator has Enterprise Admins and `BUILTIN\Administrators` (Kerberos and NTLM) |
| `test_lsa_lookups_cross_domain` | on both DCs, `LookupNames` of `DOMAIN\Administrator`, `Administrator@realm` and `DOMAIN\`, and `LookupSids` of Domain Admins, for both domains |
| `test_replication_cross_domain` | `DsReplicaSync` of the Configuration NC in both directions |
| `test_kcc_cross_domain` | the KCC runs on both DCs, and each one has a connection from the DC of the other domain |
| `test_global_catalog` | the KCC of the parent GC adds the partial replica of the child domain, which replicates, is instantiated and is searchable on port 3268 |

The `subdom_dc` environment now replicates the Configuration NC in both
directions when it starts, as the other DC environments do.

Results on this branch:

- `samba.tests.subdomain`: 14/14 OK.
- No regression in: `samba4.blackbox.trust_ntlm` (`fl2008r2dc` forest
  trust, `fl2003dc` external trust), `samba4.blackbox.trust_utils(fl2008r2dc)`,
  `samba3.wbinfo_simple.trust` (the 4 variants on `fl2008r2dc`),
  `samba.tests.dcerpc.lsa(ad_dc_default)`,
  `samba4.rpc.lsalookup` (ncacn_np, seal, `ad_dc_ntvfs`),
  `samba3.wbinfo_name_lookup(ad_member)`,
  `samba.tests.krb5.kdc_tgs_tests(ad_dc)`,
  `samba4.drs.replica_sync(vampire_dc)`, `samba4.drs.repl_rodc(ad_dc)`,
  `samba4.blackbox.dbcheck` (`alpha13`, `release-4-1-6-partial-object`,
  `release-4-5-0-pre1`, each with `.quick`), `samba4.blackbox.dbcheck-links`,
  `samba.tests.kcc.graph`, `.graph_utils`, `.ldif_import_export`,
  `.kcc_utils(ad_dc_default)`, and `samba.tests.kcc` on `vampire_dc` and
  `promoted_dc` (see [Known limitations](#known-limitations) for
  `promoted_dc` when the forest has a child domain).
- `samba4.blackbox.kinit_trust` (`fl2008r2dc`, `fl2003dc`): all OK
  except "check time with kerberos ccache", which fails in this build
  environment because `samba-tool` is started there with a Python
  interpreter that has no `ldb` module; it does not depend on this
  branch.
- `samba-tool dbcheck --cross-ncs` after the replications: no error on
  the child DC; on the parent DC, the missing backlink described in
  [Known limitations](#known-limitations).
- With the child domain in the forest (`samba.tests.subdomain` run
  first): `samba4.blackbox.join_ldapcmp` (5 environments) and
  `samba4.drs.repl_secdesc`, `samba4.drs.repl_move`,
  `samba4.drs.link_conflicts` on `vampire_dc`: ALL OK.
- The other phase 1 suites (provision, dsdb, sddl, security descriptors,
  upgradeprovision) were not run again, and the full Samba test suite
  was not run.


Roadmap
-------

1. **Cross-domain references:** phantoms (or an equivalent) for the
   members of a group that belong to another domain, Infrastructure
   Master, universal groups of the other domains in the tokens.
2. **Netlogon:** `parent_index` in `DsrEnumerateDomainTrusts`, and a
   review of the other calls that list the domains of the forest.
3. **DNS:** ForestDnsZones on the DCs of the child domains.
4. **Bigger forests:** grandchild and tree root domains (referrals over
   several trusts), several DCs and RODCs per domain, schema updates.
5. **Interoperability:** Samba child domain under a Windows forest root
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
