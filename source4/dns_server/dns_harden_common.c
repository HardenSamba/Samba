/*
   Unix SMB/CIFS implementation.

   DNS server and DNS management: the Harden-Box options (conditional
   forwarders, stub zones, default partition of the new zones) and the
   local (not AD-integrated) zone store.

   Copyright (C) HardenSamba contributors 2026

   This program is free software; you can redistribute it and/or modify
   it under the terms of the GNU General Public License as published by
   the Free Software Foundation; either version 3 of the License, or
   (at your option) any later version.

   This program is distributed in the hope that it will be useful,
   but WITHOUT ANY WARRANTY; without even the implied warranty of
   MERCHANTABILITY or FITNESS FOR A PARTICULAR PURPOSE.  See the
   GNU General Public License for more details.

   You should have received a copy of the GNU General Public License
   along with this program.  If not, see <http://www.gnu.org/licenses/>.
*/

#include "includes.h"
#include "system/network.h"
#include "librpc/ndr/libndr.h"
#include "librpc/gen_ndr/ndr_dnsp.h"
#include "librpc/gen_ndr/dnsserver.h"
#include "param/param.h"
#include <ldb.h>
#include "dns_server/dnsserver_common.h"

#undef strcasecmp

/*
 * The options of smb.conf, all off (or unset) by default: the DNS
 * server and the DNS management RPC server then behave as upstream
 * Samba.
 *
 * harden:dns conditional forwarders = yes|no
 * harden:dns stub zones = yes|no
 * harden:dns default zone partition = domain|forest|legacy
 */
void dns_harden_get_options(struct loadparm_context *lp_ctx,
			    struct dns_harden_options *o)
{
	const char *dp = NULL;

	*o = (struct dns_harden_options) { .default_dp = 0 };
	if (lp_ctx == NULL) {
		return;
	}

	o->forwarders = lpcfg_parm_bool(lp_ctx, NULL, "harden",
					"dns conditional forwarders", false);
	o->stubs = lpcfg_parm_bool(lp_ctx, NULL, "harden",
				   "dns stub zones", false);

	dp = lpcfg_parm_string(lp_ctx, NULL, "harden",
			       "dns default zone partition");
	if (dp == NULL || dp[0] == '\0') {
		return;
	}
	if (strcasecmp(dp, "domain") == 0) {
		o->default_dp = DNS_DP_DOMAIN_DEFAULT;
	} else if (strcasecmp(dp, "forest") == 0) {
		o->default_dp = DNS_DP_FOREST_DEFAULT;
	} else if (strcasecmp(dp, "legacy") == 0 ||
		   strcasecmp(dp, "system") == 0) {
		o->default_dp = DNS_DP_LEGACY;
	} else {
		DBG_WARNING("Unknown value '%s' for 'harden:dns default "
			    "zone partition' (domain, forest or legacy), "
			    "ignored\n", dp);
	}
}

bool dns_harden_enabled(const struct dns_harden_options *o)
{
	return o->forwarders || o->stubs || o->default_dp != 0;
}

/*
 * Is a zone of this type handled (not an authoritative zone of ours)?
 */
bool dns_harden_forwarding_type(const struct dns_harden_options *o,
				uint32_t zone_type)
{
	if (zone_type == DNS_ZONE_TYPE_FORWARDER) {
		return o->forwarders;
	}
	if (zone_type == DNS_ZONE_TYPE_STUB) {
		return o->stubs;
	}
	return false;
}

/*
 * The local zone store: the conditional forwarders and stub zones that
 * are not stored in AD ("file" zones of Windows). It is a local ldb of
 * this DC, with the same objects as in AD: one object per zone,
 * DC=<zone>,CN=LocalZones, with the name and the dNSProperty values of
 * the zone.
 */
struct ldb_context *dns_local_zones_connect(TALLOC_CTX *mem_ctx,
					    struct tevent_context *ev,
					    struct loadparm_context *lp_ctx)
{
	struct ldb_context *ldb = NULL;
	char *path = NULL;
	int ret;

	path = lpcfg_state_path(mem_ctx, lp_ctx, "dns_local_zones.ldb");
	if (path == NULL) {
		return NULL;
	}

	ldb = ldb_init(mem_ctx, ev);
	if (ldb == NULL) {
		TALLOC_FREE(path);
		return NULL;
	}

	ret = ldb_connect(ldb, path, 0, NULL);
	if (ret != LDB_SUCCESS) {
		DBG_ERR("Can't open the local DNS zone store %s: %s\n",
			path, ldb_errstring(ldb));
		TALLOC_FREE(ldb);
		TALLOC_FREE(path);
		return NULL;
	}
	TALLOC_FREE(path);
	return ldb;
}

struct ldb_dn *dns_local_zone_dn(TALLOC_CTX *mem_ctx,
				 struct ldb_context *ldb,
				 const char *zone_name)
{
	struct ldb_dn *dn = NULL;
	char *name = NULL;
	struct ldb_val v;

	dn = ldb_dn_new(mem_ctx, ldb, "CN=LocalZones");
	if (dn == NULL) {
		return NULL;
	}
	if (zone_name == NULL) {
		return dn;
	}
	name = strlower_talloc(dn, zone_name);
	if (name == NULL) {
		TALLOC_FREE(dn);
		return NULL;
	}
	v = data_blob_string_const(name);
	if (!ldb_dn_add_child_val(dn, "DC", v)) {
		TALLOC_FREE(dn);
		return NULL;
	}
	return dn;
}

/*
 * IP4_ARRAY values hold the 4 bytes of the address in network order in
 * their memory, as dnsp_ip4_array does.
 */
static char *dns_harden_ip4_string(TALLOC_CTX *mem_ctx, uint32_t addr)
{
	char buf[INET_ADDRSTRLEN];
	struct in_addr in;

	memcpy(&in, &addr, sizeof(in));
	if (inet_ntop(AF_INET, &in, buf, sizeof(buf)) == NULL) {
		return NULL;
	}
	return talloc_strdup(mem_ctx, buf);
}

static bool dns_harden_add_master(TALLOC_CTX *mem_ctx,
				  struct dns_zone_forwarding *zf,
				  char *addr)
{
	const char **masters = NULL;

	if (addr == NULL) {
		return false;
	}
	masters = talloc_realloc(mem_ctx, zf->masters, const char *,
				 zf->num_masters + 2);
	if (masters == NULL) {
		return false;
	}
	masters[zf->num_masters++] = talloc_steal(masters, addr);
	masters[zf->num_masters] = NULL;
	zf->masters = masters;
	return true;
}

/*
 * The type, master servers, forwarder timeout and "do not use
 * recursion" of a zone, from its dNSProperty values (unknown or
 * invalid values are ignored, as Windows does).
 */
void dns_zone_forwarding_from_props(TALLOC_CTX *mem_ctx,
				    const struct ldb_message_element *el,
				    struct dns_zone_forwarding *zf)
{
	struct dnsp_ip4_array *ip4 = NULL;
	struct dnsp_dns_addr_array *da = NULL;
	unsigned int i;

	*zf = (struct dns_zone_forwarding) {
		.zone_type = DNS_ZONE_TYPE_PRIMARY,
		.timeout = DNS_HARDEN_FORWARDER_TIMEOUT,
	};

	for (i = 0; el != NULL && i < el->num_values; i++) {
		struct dnsp_DnsProperty *prop = NULL;
		enum ndr_err_code err;

		prop = talloc_zero(mem_ctx, struct dnsp_DnsProperty);
		if (prop == NULL) {
			return;
		}
		err = ndr_pull_struct_blob(&el->values[i], prop, prop,
				(ndr_pull_flags_fn_t)ndr_pull_dnsp_DnsProperty);
		if (!NDR_ERR_CODE_IS_SUCCESS(err)) {
			TALLOC_FREE(prop);
			continue;
		}
		switch (prop->id) {
		case DSPROPERTY_ZONE_TYPE:
			zf->zone_type = prop->data.zone_type;
			break;
		case DSPROPERTY_ZONE_MASTER_SERVERS:
			ip4 = &prop->data.master_servers;
			break;
		case DSPROPERTY_ZONE_MASTER_SERVERS_DA:
			da = &prop->data.z_master_servers;
			break;
		case DSPROPERTY_ZONE_SAMBA_FORWARDER_TIMEOUT:
			zf->timeout = prop->data.forwarder_timeout;
			break;
		case DSPROPERTY_ZONE_SAMBA_FORWARDER_SLAVE:
			zf->slave = (prop->data.forwarder_slave != 0);
			break;
		default:
			break;
		}
	}

	/* MASTER_SERVERS_DA wins ([MS-DNSP] 2.3.2.1.1) */
	if (da != NULL) {
		for (i = 0; i < da->AddrCount; i++) {
			const struct dnsp_dns_addr *a = &da->AddrArray[i];
			char *s = NULL;

			if (a->family == AF_INET) {
				s = talloc_strdup(mem_ctx, a->ipv4);
			} else if (a->family == 23 || a->family == AF_INET6) {
				/* AF_INET6 is 23 on Windows */
				s = talloc_strdup(mem_ctx, a->ipv6);
			} else {
				continue;
			}
			if (!dns_harden_add_master(mem_ctx, zf, s)) {
				return;
			}
		}
	} else if (ip4 != NULL) {
		for (i = 0; i < ip4->addrCount; i++) {
			char *s = dns_harden_ip4_string(mem_ctx,
							ip4->addrArray[i]);

			if (!dns_harden_add_master(mem_ctx, zf, s)) {
				return;
			}
		}
	}

	if (zf->timeout == 0) {
		zf->timeout = DNS_HARDEN_FORWARDER_TIMEOUT;
	}
}
