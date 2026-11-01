/*
   Unix SMB/CIFS implementation.

   DNS Server management (MS-DNSP): conditional forwarders, stub zones,
   the legacy (CN=System) partition and the default partition of the new
   zones (Harden-Box options, see dns_server/dns_harden_common.c)

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
#include "rpc_server/dnsserver/dnsserver.h"
#include "dns_server/dnsserver_common.h"
#include "dsdb/samdb/samdb.h"
#include "librpc/gen_ndr/ndr_dnsp.h"
#include "librpc/gen_ndr/ndr_irpc_c.h"
#include "lib/messaging/irpc.h"
#include "lib/util/dlinklist.h"
#include "auth/session.h"
#include "libcli/security/security.h"

#undef strcasecmp

/* dwFlags of DNS_RPC_ZONE_CREATE_INFO ([MS-DNSP] 2.2.5.2.7.1) */
#define DNS_ZONE_CREATE_FOR_DCPROMO		0x00001000
#define DNS_ZONE_CREATE_FOR_DCPROMO_FOREST	0x00004000

/* The ldb holding a zone: the local zone store or AD */
struct ldb_context *dnsserver_zone_ldb(struct dnsserver_state *dsstate,
				       struct dnsserver_zone *z)
{
	if (z != NULL && z->partition != NULL && z->partition->is_local) {
		return dsstate->local_ldb;
	}
	return dsstate->samdb;
}

/*
 * The legacy partition (CN=MicrosoftDNS,CN=System of the domain NC, the
 * only one of Windows 2000) and the local zone store, after the
 * DomainDnsZones and ForestDnsZones partitions.
 */
void dnsserver_harden_add_partitions(struct dnsserver_state *dsstate)
{
	struct dnsserver_partition *p = NULL;

	if (!dns_harden_enabled(&dsstate->harden)) {
		return;
	}

	p = talloc_zero(dsstate, struct dnsserver_partition);
	if (p == NULL) {
		return;
	}
	p->partition_dn = ldb_dn_new(p, dsstate->samdb, "CN=System");
	if (p->partition_dn == NULL ||
	    !ldb_dn_add_base(p->partition_dn,
			     ldb_get_default_basedn(dsstate->samdb))) {
		TALLOC_FREE(p);
		return;
	}
	p->pszDpFqdn = "MicrosoftDNS";
	p->dwDpFlags = DNS_DP_AUTOCREATED | DNS_DP_LEGACY | DNS_DP_ENLISTED;
	p->is_legacy = true;
	DLIST_ADD_END(dsstate->partitions, p);

	if (!dsstate->harden.forwarders && !dsstate->harden.stubs) {
		return;
	}

	dsstate->local_ldb = dns_local_zones_connect(dsstate, dsstate->ev,
						     dsstate->lp_ctx);
	if (dsstate->local_ldb == NULL) {
		return;
	}
	p = talloc_zero(dsstate, struct dnsserver_partition);
	if (p == NULL) {
		return;
	}
	p->partition_dn = dns_local_zone_dn(p, dsstate->local_ldb, NULL);
	p->pszDpFqdn = NULL;
	p->dwDpFlags = 0;
	p->is_local = true;
	DLIST_ADD_END(dsstate->partitions, p);
}

static struct dnsserver_zone *dnsserver_harden_local_zones(
	struct dnsserver_state *dsstate,
	struct dnsserver_partition *p)
{
	static const char * const attrs[] = { "name", "dNSProperty", NULL };
	struct dnsserver_zone *zones = NULL;
	struct ldb_result *res = NULL;
	unsigned int i, j;
	int ret;

	ret = ldb_search(dsstate->local_ldb, dsstate, &res, p->partition_dn,
			 LDB_SCOPE_ONELEVEL, attrs, "(objectClass=dnsZone)");
	if (ret != LDB_SUCCESS) {
		return NULL;
	}

	for (i = 0; i < res->count; i++) {
		struct ldb_message_element *el = NULL;
		struct dnsserver_zone *z = NULL;
		const char *name = ldb_msg_find_attr_as_string(res->msgs[i],
							       "name", NULL);

		if (name == NULL) {
			continue;
		}
		z = talloc_zero(dsstate, struct dnsserver_zone);
		if (z == NULL) {
			break;
		}
		z->partition = p;
		z->name = talloc_strdup(z, name);
		z->zone_dn = talloc_steal(z, res->msgs[i]->dn);
		el = ldb_msg_find_element(res->msgs[i], "dNSProperty");
		if (el != NULL) {
			z->tmp_props = talloc_zero_array(z,
							 struct dnsp_DnsProperty,
							 el->num_values);
			if (z->tmp_props == NULL) {
				TALLOC_FREE(z);
				break;
			}
			for (j = 0; j < el->num_values; j++) {
				enum ndr_err_code err;

				err = ndr_pull_struct_blob(
					&el->values[j], z, &z->tmp_props[j],
					(ndr_pull_flags_fn_t)
					ndr_pull_dnsp_DnsProperty);
				if (!NDR_ERR_CODE_IS_SUCCESS(err)) {
					ZERO_STRUCT(z->tmp_props[j]);
					z->tmp_props[j].id =
						DSPROPERTY_ZONE_EMPTY;
				}
			}
			z->num_props = el->num_values;
		}
		DLIST_ADD_END(zones, z);
	}
	TALLOC_FREE(res);
	return zones;
}

/*
 * The zones of a partition: a partition added by the Harden-Box code (the
 * legacy one, the local store) may be empty or missing, that's no
 * failure.
 */
struct dnsserver_zone *dnsserver_harden_enumerate_zones(
	struct dnsserver_state *dsstate,
	struct dnsserver_partition *p,
	bool *failed)
{
	struct dnsserver_zone *zones = NULL;

	*failed = false;
	if (p->is_local) {
		return dnsserver_harden_local_zones(dsstate, p);
	}
	zones = dnsserver_db_enumerate_zones(dsstate, dsstate->samdb, p);
	if (zones == NULL && !p->is_legacy) {
		*failed = true;
	}
	return zones;
}

static struct IP4_ARRAY *dnsserver_harden_ip4_array(
	TALLOC_CTX *mem_ctx,
	const struct dns_zone_forwarding *zf)
{
	struct IP4_ARRAY *a = NULL;
	uint32_t i;

	a = talloc_zero(mem_ctx, struct IP4_ARRAY);
	if (a == NULL) {
		return NULL;
	}
	a->AddrArray = talloc_zero_array(a, uint32_t, zf->num_masters);
	if (a->AddrArray == NULL && zf->num_masters > 0) {
		TALLOC_FREE(a);
		return NULL;
	}
	for (i = 0; i < zf->num_masters; i++) {
		struct in_addr in;

		if (inet_pton(AF_INET, zf->masters[i], &in) != 1) {
			/* IPv6 masters are not shown (IP4_ARRAY) */
			continue;
		}
		memcpy(&a->AddrArray[a->AddrCount++], &in, sizeof(uint32_t));
	}
	return a;
}

/*
 * The masters as a DNS_ADDR_ARRAY ([MS-DNSP] 2.2.3.2.2 and 2.2.3.2.3):
 * a SOCKADDR_IN or SOCKADDR_IN6 (Windows AF_INET6 = 23), port 53
 */
static struct DNS_ADDR_ARRAY *dnsserver_harden_addr_array(
	TALLOC_CTX *mem_ctx,
	const struct dns_zone_forwarding *zf)
{
	struct DNS_ADDR_ARRAY *a = NULL;
	uint32_t i;
	bool ipv4 = false;
	bool ipv6 = false;

	a = talloc_zero(mem_ctx, struct DNS_ADDR_ARRAY);
	if (a == NULL) {
		return NULL;
	}
	a->AddrArray = talloc_zero_array(a, struct DNS_ADDR, zf->num_masters);
	if (a->AddrArray == NULL && zf->num_masters > 0) {
		TALLOC_FREE(a);
		return NULL;
	}
	for (i = 0; i < zf->num_masters; i++) {
		struct DNS_ADDR *d = &a->AddrArray[a->AddrCount];
		uint8_t *sa = (uint8_t *)d->MaxSa;

		if (inet_pton(AF_INET, zf->masters[i], &sa[4]) == 1) {
			sa[0] = 0x02;
			d->DnsAddrUserDword[0] = 16;
			ipv4 = true;
		} else if (inet_pton(AF_INET6, zf->masters[i], &sa[8]) == 1) {
			sa[0] = 0x17;
			d->DnsAddrUserDword[0] = 28;
			ipv6 = true;
		} else {
			continue;
		}
		sa[3] = 53;
		a->AddrCount++;
	}
	a->MaxCount = a->AddrCount;
	a->Family = (ipv4 && !ipv6) ? AF_INET : (ipv6 && !ipv4) ? 0x17 : 0;
	return a;
}

/*
 * The masters of a conditional forwarder or a stub zone for a LONGHORN
 * client (NULL: use the IP4_ARRAY as for the other zones)
 */
struct DNS_ADDR_ARRAY *dnsserver_harden_masters(TALLOC_CTX *mem_ctx,
						struct dnsserver_zoneinfo *zi)
{
	struct DNS_ADDR_ARRAY *a = NULL;

	if (zi == NULL || zi->aipMastersDa == NULL) {
		return NULL;
	}
	a = talloc_zero(mem_ctx, struct DNS_ADDR_ARRAY);
	if (a == NULL) {
		return NULL;
	}
	*a = *zi->aipMastersDa;
	a->AddrArray = talloc_memdup(a, zi->aipMastersDa->AddrArray,
				     sizeof(struct DNS_ADDR) * a->AddrCount);
	if (a->AddrArray == NULL && a->AddrCount > 0) {
		TALLOC_FREE(a);
	}
	return a;
}

bool dnsserver_harden_is_forward_zone(struct dnsserver_zone *z)
{
	return z->zoneinfo != NULL &&
	       (z->zoneinfo->dwZoneType == DNS_ZONE_TYPE_FORWARDER ||
		z->zoneinfo->dwZoneType == DNS_ZONE_TYPE_STUB);
}

/*
 * ZoneInfo of a conditional forwarder or a stub zone, and of a zone of
 * the local store ([MS-DNSP] 2.2.5.2.4.3)
 */
void dnsserver_harden_fix_zoneinfo(struct dnsserver_state *dsstate,
				   struct dnsserver_zone *z)
{
	struct dnsserver_zoneinfo *zi = z->zoneinfo;
	struct dns_zone_forwarding zf;
	struct ldb_message_element el = {};
	DATA_BLOB *values = NULL;
	int32_t i;

	if (zi == NULL || !dns_harden_enabled(&dsstate->harden)) {
		return;
	}

	if (z->partition->is_local) {
		zi->Flags &= ~DNS_RPC_ZONE_DSINTEGRATED;
		zi->fUseDatabase = 0;
		zi->pwszZoneDn = NULL;
	}

	if (!dnsserver_harden_is_forward_zone(z)) {
		return;
	}

	/* the masters, timeout and recursion, from the properties */
	values = talloc_zero_array(zi, DATA_BLOB, z->num_props);
	for (i = 0; values != NULL && i < z->num_props; i++) {
		enum ndr_err_code err;

		err = ndr_push_struct_blob(&values[el.num_values], values,
				&z->tmp_props[i],
				(ndr_push_flags_fn_t)ndr_push_dnsp_DnsProperty);
		if (NDR_ERR_CODE_IS_SUCCESS(err)) {
			el.num_values++;
		}
	}
	el.values = values;
	dns_zone_forwarding_from_props(zi, &el, &zf);
	TALLOC_FREE(values);

	zi->Flags &= ~(DNS_RPC_ZONE_UPDATE_SECURE |
		       DNS_RPC_ZONE_UPDATE_UNSECURE |
		       DNS_RPC_ZONE_AGING);
	zi->fAllowUpdate = DNS_ZONE_UPDATE_OFF;
	zi->fAging = 0;
	zi->aipMasters = dnsserver_harden_ip4_array(zi, &zf);
	zi->aipMastersDa = dnsserver_harden_addr_array(zi, &zf);
	zi->aipLocalMasters = NULL;
	if (zi->dwZoneType == DNS_ZONE_TYPE_FORWARDER) {
		zi->dwForwarderTimeout = zf.timeout;
		zi->fForwarderSlave = zf.slave ? 1 : 0;
	} else {
		zi->dwForwarderTimeout = 0;
		zi->fForwarderSlave = 0;
	}
}

/*
 * The EnumZones filter, grouped as [MS-DNSP] 2.2.5.1.4 describes the
 * flags (deduced from dnscmd): a zone matches if it matches one of the
 * flags set in each group that has some.
 */
bool dnsserver_harden_zone_matches(struct dnsserver_zone *z, uint32_t filter)
{
	const struct dnsserver_zoneinfo *zi = z->zoneinfo;
	const struct dnsserver_partition *p = z->partition;
	uint32_t type_bits = DNS_ZONE_REQUEST_PRIMARY |
			     DNS_ZONE_REQUEST_SECONDARY |
			     DNS_ZONE_REQUEST_CACHE |
			     DNS_ZONE_REQUEST_AUTO |
			     DNS_ZONE_REQUEST_FORWARDER |
			     DNS_ZONE_REQUEST_STUB;
	uint32_t lookup_bits = DNS_ZONE_REQUEST_FORWARD |
			       DNS_ZONE_REQUEST_REVERSE;
	uint32_t storage_bits = DNS_ZONE_REQUEST_DS | DNS_ZONE_REQUEST_NON_DS;
	uint32_t dp_bits = DNS_ZONE_REQUEST_DOMAIN_DP |
			   DNS_ZONE_REQUEST_FOREST_DP |
			   DNS_ZONE_REQUEST_CUSTOM_DP |
			   DNS_ZONE_REQUEST_LEGACY_DP;
	bool ds = !p->is_local;
	bool found;

	if (filter & type_bits) {
		found = false;
		if ((filter & DNS_ZONE_REQUEST_PRIMARY) &&
		    zi->dwZoneType == DNS_ZONE_TYPE_PRIMARY) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_SECONDARY) &&
		    zi->dwZoneType == DNS_ZONE_TYPE_SECONDARY) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_CACHE) &&
		    zi->dwZoneType == DNS_ZONE_TYPE_CACHE) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_AUTO) && zi->fAutoCreated) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_FORWARDER) &&
		    zi->dwZoneType == DNS_ZONE_TYPE_FORWARDER) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_STUB) &&
		    zi->dwZoneType == DNS_ZONE_TYPE_STUB) {
			found = true;
		}
		if (!found) {
			return false;
		}
	}

	if (filter & lookup_bits) {
		found = false;
		if ((filter & DNS_ZONE_REQUEST_FORWARD) && !zi->fReverse) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_REVERSE) && zi->fReverse) {
			found = true;
		}
		if (!found) {
			return false;
		}
	}

	if (filter & storage_bits) {
		found = false;
		if ((filter & DNS_ZONE_REQUEST_DS) && ds) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_NON_DS) && !ds) {
			found = true;
		}
		if (!found) {
			return false;
		}
	}

	if (filter & dp_bits) {
		found = false;
		if ((filter & DNS_ZONE_REQUEST_DOMAIN_DP) &&
		    (p->dwDpFlags & DNS_DP_DOMAIN_DEFAULT)) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_FOREST_DP) &&
		    (p->dwDpFlags & DNS_DP_FOREST_DEFAULT)) {
			found = true;
		}
		if ((filter & DNS_ZONE_REQUEST_LEGACY_DP) &&
		    (p->dwDpFlags & DNS_DP_LEGACY)) {
			found = true;
		}
		if (!found) {
			return false;
		}
	}

	return true;
}

/*
 * The local zone store and the zone ACLs: only an administrator
 * changes it (there is no ACL on the local store).
 */
static bool dnsserver_harden_is_admin(struct dnsserver_state *dsstate)
{
	const struct dom_sid *domain_sid = samdb_domain_sid(dsstate->samdb);

	if (dsstate->session_info == NULL) {
		return false;
	}
	return security_session_user_level(dsstate->session_info,
					   domain_sid) >= SECURITY_ADMINISTRATOR;
}

/* the DNS server reloads its zones (the local store has no notification) */
struct dnsserver_harden_notify_state {
	struct imessaging_context *msg_ctx;
	struct dnssrv_reload_dns_zones r;
};

static void dnsserver_harden_notify_done(struct tevent_req *req)
{
	struct dnsserver_harden_notify_state *state =
		tevent_req_callback_data(req,
					 struct dnsserver_harden_notify_state);
	NTSTATUS status;

	status = dcerpc_dnssrv_reload_dns_zones_r_recv(req, state);
	if (!NT_STATUS_IS_OK(status)) {
		DBG_NOTICE("Error notifying the DNS server: %s\n",
			   nt_errstr(status));
	}
	imessaging_cleanup(state->msg_ctx);
	talloc_free(req);
	talloc_free(state);
}

void dnsserver_harden_notify_dns_server(struct dnsserver_state *dsstate)
{
	struct dnsserver_harden_notify_state *state = NULL;
	struct dcerpc_binding_handle *handle = NULL;
	struct tevent_req *req = NULL;

	state = talloc_zero(dsstate->lp_ctx,
			    struct dnsserver_harden_notify_state);
	if (state == NULL) {
		return;
	}
	state->msg_ctx = imessaging_client_init(state, dsstate->lp_ctx,
						dsstate->ev);
	if (state->msg_ctx == NULL) {
		talloc_free(state);
		return;
	}
	handle = irpc_binding_handle_by_name(state, state->msg_ctx, "dnssrv",
					     &ndr_table_irpc);
	if (handle == NULL) {
		imessaging_cleanup(state->msg_ctx);
		talloc_free(state);
		return;
	}
	req = dcerpc_dnssrv_reload_dns_zones_r_send(state, dsstate->ev,
						    handle, &state->r);
	if (req == NULL) {
		imessaging_cleanup(state->msg_ctx);
		talloc_free(state);
		return;
	}
	tevent_req_set_callback(req, dnsserver_harden_notify_done, state);
}

static bool dnsserver_harden_add_prop(TALLOC_CTX *mem_ctx,
				      struct ldb_message_element *el,
				      struct dnsp_DnsProperty *prop)
{
	DATA_BLOB *values = NULL;
	enum ndr_err_code err;

	values = talloc_realloc(mem_ctx, el->values, DATA_BLOB,
				el->num_values + 1);
	if (values == NULL) {
		return false;
	}
	el->values = values;
	prop->version = 1;
	err = ndr_push_struct_blob(&values[el->num_values], values, prop,
				   (ndr_push_flags_fn_t)ndr_push_dnsp_DnsProperty);
	if (!NDR_ERR_CODE_IS_SUCCESS(err)) {
		return false;
	}
	el->num_values++;
	return true;
}

/*
 * The dNSProperty values of the masters of a zone: MASTER_SERVERS
 * (IPv4 only) and MASTER_SERVERS_DA ([MS-DNSP] 2.3.2.1.1, note 232)
 */
static bool dnsserver_harden_master_props(TALLOC_CTX *mem_ctx,
					  struct ldb_message_element *el,
					  const char **masters,
					  uint32_t num_masters)
{
	struct dnsp_DnsProperty prop = {};
	struct dnsp_dns_addr *da = NULL;
	uint32_t *ip4 = NULL;
	uint32_t n4 = 0;
	uint32_t i;

	ip4 = talloc_zero_array(mem_ctx, uint32_t, num_masters);
	da = talloc_zero_array(mem_ctx, struct dnsp_dns_addr, num_masters);
	if ((ip4 == NULL || da == NULL) && num_masters > 0) {
		return false;
	}
	for (i = 0; i < num_masters; i++) {
		struct in_addr in;

		if (inet_pton(AF_INET, masters[i], &in) == 1) {
			memcpy(&ip4[n4++], &in, sizeof(uint32_t));
			da[i].family = AF_INET;
			da[i].ipv4 = masters[i];
			da[i].ipv6 = "::";
		} else {
			da[i].family = 23; /* AF_INET6 on Windows */
			da[i].ipv4 = "0.0.0.0";
			da[i].ipv6 = masters[i];
		}
		da[i].port = 53;
	}

	prop.id = DSPROPERTY_ZONE_MASTER_SERVERS;
	prop.data.master_servers.addrCount = n4;
	prop.data.master_servers.addrArray = ip4;
	if (!dnsserver_harden_add_prop(mem_ctx, el, &prop)) {
		return false;
	}

	prop = (struct dnsp_DnsProperty) {
		.id = DSPROPERTY_ZONE_MASTER_SERVERS_DA,
	};
	prop.data.z_master_servers.MaxCount = num_masters;
	prop.data.z_master_servers.AddrCount = num_masters;
	prop.data.z_master_servers.AddrArray = da;
	return dnsserver_harden_add_prop(mem_ctx, el, &prop);
}

static bool dnsserver_harden_forwarder_props(TALLOC_CTX *mem_ctx,
					     struct ldb_message_element *el,
					     uint32_t timeout,
					     bool slave)
{
	struct dnsp_DnsProperty prop = {};

	/* Samba specific, only when they differ from the defaults */
	if (timeout != 0 && timeout != DNS_HARDEN_FORWARDER_TIMEOUT) {
		prop.id = DSPROPERTY_ZONE_SAMBA_FORWARDER_TIMEOUT;
		prop.data.forwarder_timeout = timeout;
		if (!dnsserver_harden_add_prop(mem_ctx, el, &prop)) {
			return false;
		}
	}
	if (slave) {
		prop = (struct dnsp_DnsProperty) {
			.id = DSPROPERTY_ZONE_SAMBA_FORWARDER_SLAVE,
		};
		prop.data.forwarder_slave = 1;
		if (!dnsserver_harden_add_prop(mem_ctx, el, &prop)) {
			return false;
		}
	}
	return true;
}

/* the parameters of a ZoneCreate, whatever the structure version */
struct dnsserver_harden_create {
	const char *name;
	uint32_t zone_type;
	uint32_t allow_update;
	uint32_t aging;
	uint32_t flags;
	bool ds_integrated;
	uint32_t dp_flags;
	const char *dp_fqdn;
	const char **masters;
	uint32_t num_masters;
	uint32_t timeout;
	bool slave;
};

static void dnsserver_harden_masters_ip4(TALLOC_CTX *mem_ctx,
					 struct dnsserver_harden_create *c,
					 const struct IP4_ARRAY *a)
{
	uint32_t i;

	if (a == NULL || a->AddrCount == 0) {
		return;
	}
	c->masters = talloc_zero_array(mem_ctx, const char *, a->AddrCount + 1);
	if (c->masters == NULL) {
		return;
	}
	for (i = 0; i < a->AddrCount; i++) {
		char buf[INET_ADDRSTRLEN];
		struct in_addr in;

		memcpy(&in, &a->AddrArray[i], sizeof(in));
		if (inet_ntop(AF_INET, &in, buf, sizeof(buf)) == NULL) {
			continue;
		}
		c->masters[c->num_masters++] = talloc_strdup(c->masters, buf);
	}
}

static void dnsserver_harden_masters_da(TALLOC_CTX *mem_ctx,
					struct dnsserver_harden_create *c,
					const struct DNS_ADDR_ARRAY *a)
{
	uint32_t i;

	if (a == NULL || a->AddrCount == 0) {
		return;
	}
	c->masters = talloc_zero_array(mem_ctx, const char *, a->AddrCount + 1);
	if (c->masters == NULL) {
		return;
	}
	for (i = 0; i < a->AddrCount; i++) {
		const uint8_t *sa = (const uint8_t *)a->AddrArray[i].MaxSa;
		char buf[INET6_ADDRSTRLEN];
		uint16_t family = sa[0] | (sa[1] << 8);

		if (family == AF_INET) {
			if (inet_ntop(AF_INET, &sa[4], buf, sizeof(buf)) == NULL) {
				continue;
			}
		} else if (family == 23 || family == AF_INET6) {
			/* sockaddr_in6: the address is at offset 8 */
			if (inet_ntop(AF_INET6, &sa[8], buf, sizeof(buf)) == NULL) {
				continue;
			}
		} else {
			continue;
		}
		c->masters[c->num_masters++] = talloc_strdup(c->masters, buf);
	}
}

static WERROR dnsserver_harden_parse_create(TALLOC_CTX *mem_ctx,
					    enum DNS_RPC_TYPEID typeid,
					    union DNSSRV_RPC_UNION *r,
					    struct dnsserver_harden_create *c)
{
	*c = (struct dnsserver_harden_create) {
		.timeout = DNS_HARDEN_FORWARDER_TIMEOUT,
	};

	if (typeid == DNSSRV_TYPEID_ZONE_CREATE_W2K) {
		c->name = r->ZoneCreateW2K->pszZoneName;
		c->zone_type = r->ZoneCreateW2K->dwZoneType;
		c->allow_update = r->ZoneCreateW2K->fAllowUpdate;
		c->aging = r->ZoneCreateW2K->fAging;
		c->flags = r->ZoneCreateW2K->dwFlags;
		c->ds_integrated = r->ZoneCreateW2K->fDsIntegrated;
		dnsserver_harden_masters_ip4(mem_ctx, c,
					     r->ZoneCreateW2K->aipMasters);
	} else if (typeid == DNSSRV_TYPEID_ZONE_CREATE_DOTNET) {
		c->name = r->ZoneCreateDotNet->pszZoneName;
		c->zone_type = r->ZoneCreateDotNet->dwZoneType;
		c->allow_update = r->ZoneCreateDotNet->fAllowUpdate;
		c->aging = r->ZoneCreateDotNet->fAging;
		c->flags = r->ZoneCreateDotNet->dwFlags;
		c->ds_integrated = r->ZoneCreateDotNet->fDsIntegrated;
		c->dp_flags = r->ZoneCreateDotNet->dwDpFlags;
		c->dp_fqdn = r->ZoneCreateDotNet->pszDpFqdn;
		c->timeout = r->ZoneCreateDotNet->dwTimeout;
		c->slave = r->ZoneCreateDotNet->fRecurseAfterForwarding;
		dnsserver_harden_masters_ip4(mem_ctx, c,
					     r->ZoneCreateDotNet->aipMasters);
	} else if (typeid == DNSSRV_TYPEID_ZONE_CREATE) {
		c->name = r->ZoneCreate->pszZoneName;
		c->zone_type = r->ZoneCreate->dwZoneType;
		c->allow_update = r->ZoneCreate->fAllowUpdate;
		c->aging = r->ZoneCreate->fAging;
		c->flags = r->ZoneCreate->dwFlags;
		c->ds_integrated = r->ZoneCreate->fDsIntegrated;
		c->dp_flags = r->ZoneCreate->dwDpFlags;
		c->dp_fqdn = r->ZoneCreate->pszDpFqdn;
		c->timeout = r->ZoneCreate->dwTimeout;
		c->slave = r->ZoneCreate->fRecurseAfterForwarding;
		dnsserver_harden_masters_da(mem_ctx, c,
					    r->ZoneCreate->aipMasters);
	} else {
		return WERR_DNS_ERROR_INVALID_PROPERTY;
	}

	if (c->name == NULL || c->name[0] == '\0') {
		return WERR_INVALID_PARAMETER;
	}
	if (c->timeout == 0) {
		c->timeout = DNS_HARDEN_FORWARDER_TIMEOUT;
	}
	return WERR_OK;
}

/*
 * The partition of a new AD-integrated zone ([MS-DNSP] 3.1.4.1
 * ZoneCreate): DCPROMO flags, then the FQDN of the partition, then the
 * built-in partition flags, then "harden:dns default zone partition"
 * (DomainDnsZones when unset, as before)
 */
static struct dnsserver_partition *dnsserver_harden_choose_partition(
	struct dnsserver_state *dsstate,
	const struct dnsserver_harden_create *c)
{
	struct dnsserver_partition *p = NULL;
	uint32_t dp = 0;

	if (c->flags & DNS_ZONE_CREATE_FOR_DCPROMO_FOREST) {
		dp = DNS_DP_FOREST_DEFAULT;
	} else if (c->flags & DNS_ZONE_CREATE_FOR_DCPROMO) {
		dp = DNS_DP_DOMAIN_DEFAULT;
	} else if (c->dp_fqdn != NULL && c->dp_fqdn[0] != '\0') {
		for (p = dsstate->partitions; p != NULL; p = p->next) {
			if (p->pszDpFqdn != NULL &&
			    strcasecmp(p->pszDpFqdn, c->dp_fqdn) == 0) {
				return p;
			}
		}
		return NULL;
	} else if (c->dp_flags & (DNS_DP_LEGACY | DNS_DP_DOMAIN_DEFAULT |
				  DNS_DP_FOREST_DEFAULT)) {
		dp = c->dp_flags & (DNS_DP_LEGACY | DNS_DP_DOMAIN_DEFAULT |
				    DNS_DP_FOREST_DEFAULT);
	} else if (dsstate->harden.default_dp != 0) {
		dp = dsstate->harden.default_dp;
	} else {
		dp = DNS_DP_DOMAIN_DEFAULT;
	}

	for (p = dsstate->partitions; p != NULL; p = p->next) {
		if (p->is_local) {
			continue;
		}
		if (p->dwDpFlags & dp) {
			return p;
		}
	}
	return NULL;
}

static WERROR dnsserver_harden_add_local_zone(struct dnsserver_state *dsstate,
					      TALLOC_CTX *mem_ctx,
					      struct dnsserver_partition *p,
					      const struct dnsserver_harden_create *c,
					      const char *name)
{
	struct ldb_message *msg = NULL;
	struct ldb_message_element *el = NULL;
	struct dnsp_DnsProperty prop = {};
	int ret;

	msg = ldb_msg_new(mem_ctx);
	if (msg == NULL) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	msg->dn = dns_local_zone_dn(msg, dsstate->local_ldb, name);
	if (msg->dn == NULL) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	ret = ldb_msg_add_string(msg, "objectClass", "dnsZone");
	if (ret == LDB_SUCCESS) {
		ret = ldb_msg_add_string(msg, "name", name);
	}
	if (ret == LDB_SUCCESS) {
		ret = ldb_msg_add_empty(msg, "dNSProperty", 0, &el);
	}
	if (ret != LDB_SUCCESS) {
		return WERR_NOT_ENOUGH_MEMORY;
	}

	prop.id = DSPROPERTY_ZONE_TYPE;
	prop.data.zone_type = c->zone_type;
	if (!dnsserver_harden_add_prop(msg, el, &prop)) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	prop = (struct dnsp_DnsProperty) { .id = DSPROPERTY_ZONE_ALLOW_UPDATE };
	prop.data.allow_update_flag = DNS_ZONE_UPDATE_OFF;
	if (!dnsserver_harden_add_prop(msg, el, &prop) ||
	    !dnsserver_harden_master_props(msg, el, c->masters,
					   c->num_masters) ||
	    (c->zone_type == DNS_ZONE_TYPE_FORWARDER &&
	     !dnsserver_harden_forwarder_props(msg, el, c->timeout,
					       c->slave))) {
		return WERR_NOT_ENOUGH_MEMORY;
	}

	ret = ldb_add(dsstate->local_ldb, msg);
	if (ret == LDB_ERR_ENTRY_ALREADY_EXISTS) {
		return WERR_DNS_ERROR_ZONE_ALREADY_EXISTS;
	}
	if (ret != LDB_SUCCESS) {
		DBG_ERR("Can't add the local zone %s: %s\n", name,
			ldb_errstring(dsstate->local_ldb));
		return WERR_INTERNAL_DB_ERROR;
	}
	return WERR_OK;
}

/*
 * A conditional forwarder or a stub zone in AD: the dnsZone object
 * (with the ACL of the zones), its type and masters, no records (the
 * records of a stub zone come from its masters).
 */
static WERROR dnsserver_harden_add_ad_zone(struct dnsserver_state *dsstate,
					   TALLOC_CTX *mem_ctx,
					   struct dnsserver_partition *p,
					   const struct dnsserver_harden_create *c,
					   const char *name)
{
	struct dnsserver_zone *z = NULL;
	struct ldb_message *msg = NULL;
	struct ldb_message_element *el = NULL;
	struct ldb_dn *dn = NULL;
	struct ldb_val name_val = data_blob_string_const(name);
	WERROR status;
	int ret;

	z = talloc_zero(mem_ctx, struct dnsserver_zone);
	if (z == NULL) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	z->name = name;
	z->partition = p;
	z->zoneinfo = talloc_zero(z, struct dnsserver_zoneinfo);
	if (z->zoneinfo == NULL) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	z->zoneinfo->dwZoneType = c->zone_type;
	z->zoneinfo->fAllowUpdate = DNS_ZONE_UPDATE_OFF;
	z->zoneinfo->fAging = 0;

	dn = ldb_dn_copy(mem_ctx, p->partition_dn);
	if (dn == NULL ||
	    !ldb_dn_add_child_fmt(dn, "CN=MicrosoftDNS") ||
	    !ldb_dn_add_child_val(dn, "DC", name_val)) {
		return WERR_NOT_ENOUGH_MEMORY;
	}

	ret = ldb_transaction_start(dsstate->samdb);
	if (ret != LDB_SUCCESS) {
		return WERR_INTERNAL_DB_ERROR;
	}

	status = dnsserver_db_do_create_zone(mem_ctx, dsstate->samdb, dn, z);
	if (!W_ERROR_IS_OK(status)) {
		ldb_transaction_cancel(dsstate->samdb);
		return status;
	}

	msg = ldb_msg_new(mem_ctx);
	if (msg == NULL) {
		ldb_transaction_cancel(dsstate->samdb);
		return WERR_NOT_ENOUGH_MEMORY;
	}
	msg->dn = dn;
	ret = ldb_msg_add_empty(msg, "dNSProperty", LDB_FLAG_MOD_ADD, &el);
	if (ret != LDB_SUCCESS ||
	    !dnsserver_harden_master_props(msg, el, c->masters,
					   c->num_masters) ||
	    (c->zone_type == DNS_ZONE_TYPE_FORWARDER &&
	     !dnsserver_harden_forwarder_props(msg, el, c->timeout,
					       c->slave))) {
		ldb_transaction_cancel(dsstate->samdb);
		return WERR_NOT_ENOUGH_MEMORY;
	}
	ret = ldb_modify(dsstate->samdb, msg);
	if (ret != LDB_SUCCESS) {
		DBG_ERR("Can't set the masters of zone %s: %s\n", name,
			ldb_errstring(dsstate->samdb));
		ldb_transaction_cancel(dsstate->samdb);
		return WERR_INTERNAL_DB_ERROR;
	}

	ret = ldb_transaction_commit(dsstate->samdb);
	if (ret != LDB_SUCCESS) {
		return WERR_INTERNAL_DB_ERROR;
	}
	return WERR_OK;
}

/* ZoneCreate with the Harden-Box options */
WERROR dnsserver_harden_create_zone(struct dnsserver_state *dsstate,
				    TALLOC_CTX *mem_ctx,
				    enum DNS_RPC_TYPEID typeid,
				    union DNSSRV_RPC_UNION *r)
{
	struct dnsserver_harden_create c;
	struct dnsserver_partition *p = NULL;
	TALLOC_CTX *tmp_ctx = talloc_new(mem_ctx);
	char *name = NULL;
	size_t len;
	WERROR status;

	if (tmp_ctx == NULL) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	status = dnsserver_harden_parse_create(tmp_ctx, typeid, r, &c);
	if (!W_ERROR_IS_OK(status)) {
		TALLOC_FREE(tmp_ctx);
		return status;
	}

	len = strlen(c.name);
	if (len > 1 && c.name[len - 1] == '.') {
		len -= 1;
	}
	name = talloc_strndup(tmp_ctx, c.name, len);
	if (name == NULL) {
		TALLOC_FREE(tmp_ctx);
		return WERR_NOT_ENOUGH_MEMORY;
	}
	if (dnsserver_find_zone(dsstate->zones, name) != NULL) {
		TALLOC_FREE(tmp_ctx);
		return WERR_DNS_ERROR_ZONE_ALREADY_EXISTS;
	}

	if (c.zone_type == DNS_ZONE_TYPE_PRIMARY) {
		struct dnsserver_zone *z = NULL;

		/* in AD, as before, but in the partition asked for */
		p = dnsserver_harden_choose_partition(dsstate, &c);
		if (p == NULL) {
			TALLOC_FREE(tmp_ctx);
			return WERR_DNS_ERROR_DP_DOES_NOT_EXIST;
		}
		z = talloc_zero(tmp_ctx, struct dnsserver_zone);
		if (z == NULL) {
			TALLOC_FREE(tmp_ctx);
			return WERR_NOT_ENOUGH_MEMORY;
		}
		z->name = name;
		z->partition = p;
		z->zoneinfo = talloc_zero(z, struct dnsserver_zoneinfo);
		if (z->zoneinfo == NULL) {
			TALLOC_FREE(tmp_ctx);
			return WERR_NOT_ENOUGH_MEMORY;
		}
		z->zoneinfo->dwZoneType = DNS_ZONE_TYPE_PRIMARY;
		z->zoneinfo->fAllowUpdate = c.allow_update;
		z->zoneinfo->fAging = c.aging;
		z->zoneinfo->Flags = c.flags;
		status = dnsserver_db_create_zone_in(dsstate->samdb, p, z,
						     dsstate->lp_ctx);
		TALLOC_FREE(tmp_ctx);
		return status;
	}

	if (!dns_harden_forwarding_type(&dsstate->harden, c.zone_type)) {
		/* not enabled, or another type */
		TALLOC_FREE(tmp_ctx);
		return WERR_CALL_NOT_IMPLEMENTED;
	}
	if (c.num_masters == 0) {
		TALLOC_FREE(tmp_ctx);
		return WERR_DNS_ERROR_INVALID_IP_ADDRESS;
	}

	if (!c.ds_integrated) {
		for (p = dsstate->partitions; p != NULL; p = p->next) {
			if (p->is_local) {
				break;
			}
		}
		if (p == NULL || dsstate->local_ldb == NULL) {
			TALLOC_FREE(tmp_ctx);
			return WERR_INTERNAL_DB_ERROR;
		}
		if (!dnsserver_harden_is_admin(dsstate)) {
			TALLOC_FREE(tmp_ctx);
			return WERR_ACCESS_DENIED;
		}
		status = dnsserver_harden_add_local_zone(dsstate, tmp_ctx, p,
							 &c, name);
		if (W_ERROR_IS_OK(status)) {
			dnsserver_harden_notify_dns_server(dsstate);
		}
		TALLOC_FREE(tmp_ctx);
		return status;
	}

	p = dnsserver_harden_choose_partition(dsstate, &c);
	if (p == NULL) {
		TALLOC_FREE(tmp_ctx);
		return WERR_DNS_ERROR_DP_DOES_NOT_EXIST;
	}
	status = dnsserver_harden_add_ad_zone(dsstate, tmp_ctx, p, &c, name);
	TALLOC_FREE(tmp_ctx);
	return status;
}

/*
 * Replace the values of the property ids of a zone object by the values
 * of el (MOD_REPLACE of dNSProperty, the other properties kept)
 */
static WERROR dnsserver_harden_replace_props(struct dnsserver_state *dsstate,
					     TALLOC_CTX *mem_ctx,
					     struct dnsserver_zone *z,
					     const uint32_t *ids,
					     size_t num_ids,
					     struct ldb_message_element *new_el)
{
	struct ldb_context *ldb = dnsserver_zone_ldb(dsstate, z);
	static const char * const attrs[] = { "dNSProperty", NULL };
	struct ldb_message_element *el = NULL;
	struct ldb_message *msg = NULL;
	struct ldb_result *res = NULL;
	unsigned int i, j;
	int ret;

	ret = ldb_search(ldb, mem_ctx, &res, z->zone_dn, LDB_SCOPE_BASE,
			 attrs, "(objectClass=dnsZone)");
	if (ret != LDB_SUCCESS || res->count != 1) {
		return WERR_DNS_ERROR_ZONE_DOES_NOT_EXIST;
	}

	msg = ldb_msg_new(mem_ctx);
	if (msg == NULL) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	msg->dn = z->zone_dn;
	ret = ldb_msg_add_empty(msg, "dNSProperty", LDB_FLAG_MOD_REPLACE,
				&el);
	if (ret != LDB_SUCCESS) {
		return WERR_NOT_ENOUGH_MEMORY;
	}

	{
		struct ldb_message_element *old = ldb_msg_find_element(
			res->msgs[0], "dNSProperty");

		for (i = 0; old != NULL && i < old->num_values; i++) {
			struct dnsp_DnsProperty prop;
			enum ndr_err_code err;
			bool replaced = false;

			err = ndr_pull_struct_blob(&old->values[i], mem_ctx,
					&prop,
					(ndr_pull_flags_fn_t)
					ndr_pull_dnsp_DnsProperty);
			if (NDR_ERR_CODE_IS_SUCCESS(err)) {
				for (j = 0; j < num_ids; j++) {
					if (prop.id == ids[j]) {
						replaced = true;
					}
				}
			}
			if (replaced) {
				continue;
			}
			ret = ldb_msg_add_value(msg, "dNSProperty",
						&old->values[i], NULL);
			if (ret != LDB_SUCCESS) {
				return WERR_NOT_ENOUGH_MEMORY;
			}
		}
	}
	el = ldb_msg_find_element(msg, "dNSProperty");
	for (i = 0; new_el != NULL && i < new_el->num_values; i++) {
		ret = ldb_msg_add_value(msg, "dNSProperty",
					&new_el->values[i], NULL);
		if (ret != LDB_SUCCESS) {
			return WERR_NOT_ENOUGH_MEMORY;
		}
	}
	el = ldb_msg_find_element(msg, "dNSProperty");
	el->flags = LDB_FLAG_MOD_REPLACE;

	ret = ldb_modify(ldb, msg);
	if (ret == LDB_ERR_INSUFFICIENT_ACCESS_RIGHTS) {
		return WERR_ACCESS_DENIED;
	}
	if (ret != LDB_SUCCESS) {
		DBG_ERR("Can't modify zone %s: %s\n", z->name,
			ldb_errstring(ldb));
		return WERR_INTERNAL_DB_ERROR;
	}
	/*
	 * The DNS server of the DC must reload its forward zones (for a zone
	 * in AD, the dns_notify module may not see the change).
	 */
	dnsserver_harden_notify_dns_server(dsstate);
	return WERR_OK;
}

static WERROR dnsserver_harden_set_masters(struct dnsserver_state *dsstate,
					   TALLOC_CTX *mem_ctx,
					   struct dnsserver_zone *z,
					   enum DNS_RPC_TYPEID typeid,
					   union DNSSRV_RPC_UNION *r)
{
	static const uint32_t ids[] = { DSPROPERTY_ZONE_MASTER_SERVERS,
					DSPROPERTY_ZONE_MASTER_SERVERS_DA };
	struct dnsserver_harden_create c = {};
	struct ldb_message_element el = {};

	if (typeid == DNSSRV_TYPEID_IPARRAY) {
		dnsserver_harden_masters_ip4(mem_ctx, &c, r->IpArray);
	} else if (typeid == DNSSRV_TYPEID_ADDRARRAY) {
		dnsserver_harden_masters_da(mem_ctx, &c, r->AddrArray);
	} else {
		return WERR_DNS_ERROR_INVALID_PROPERTY;
	}
	if (c.num_masters == 0) {
		return WERR_DNS_ERROR_INVALID_IP_ADDRESS;
	}
	if (!dnsserver_harden_master_props(mem_ctx, &el, c.masters,
					   c.num_masters)) {
		return WERR_NOT_ENOUGH_MEMORY;
	}
	return dnsserver_harden_replace_props(dsstate, mem_ctx, z, ids,
					      ARRAY_SIZE(ids), &el);
}

static WERROR dnsserver_harden_reset_dword(struct dnsserver_state *dsstate,
					   TALLOC_CTX *mem_ctx,
					   struct dnsserver_zone *z,
					   struct DNS_RPC_NAME_AND_PARAM *n_p)
{
	struct ldb_message_element el = {};
	uint32_t id;

	if (z->zoneinfo->dwZoneType != DNS_ZONE_TYPE_FORWARDER) {
		/* no property of a stub zone can be changed this way */
		return WERR_DNS_ERROR_INVALID_ZONE_TYPE;
	}

	if (strcasecmp(n_p->pszNodeName, "ForwarderTimeout") == 0) {
		id = DSPROPERTY_ZONE_SAMBA_FORWARDER_TIMEOUT;
		if (!dnsserver_harden_forwarder_props(mem_ctx, &el,
						      n_p->dwParam, false)) {
			return WERR_NOT_ENOUGH_MEMORY;
		}
	} else if (strcasecmp(n_p->pszNodeName, "ForwarderSlave") == 0) {
		id = DSPROPERTY_ZONE_SAMBA_FORWARDER_SLAVE;
		if (!dnsserver_harden_forwarder_props(mem_ctx, &el, 0,
						      n_p->dwParam != 0)) {
			return WERR_NOT_ENOUGH_MEMORY;
		}
	} else {
		return WERR_DNS_ERROR_INVALID_ZONE_TYPE;
	}

	return dnsserver_harden_replace_props(dsstate, mem_ctx, z, &id, 1,
					      &el);
}

static WERROR dnsserver_harden_delete_local_zone(
	struct dnsserver_state *dsstate,
	struct dnsserver_zone *z)
{
	int ret;

	if (!dnsserver_harden_is_admin(dsstate)) {
		return WERR_ACCESS_DENIED;
	}
	ret = ldb_delete(dsstate->local_ldb, z->zone_dn);
	if (ret != LDB_SUCCESS) {
		return WERR_DNS_ERROR_ZONE_DOES_NOT_EXIST;
	}
	dnsserver_harden_notify_dns_server(dsstate);
	return WERR_OK;
}

/*
 * The zone operations of the Harden-Box zones: *handled is false when
 * the operation is left to the normal code.
 */
WERROR dnsserver_harden_operate_zone(struct dnsserver_state *dsstate,
				     TALLOC_CTX *mem_ctx,
				     struct dnsserver_zone *z,
				     const char *operation,
				     enum DNS_RPC_TYPEID typeid,
				     union DNSSRV_RPC_UNION *r,
				     bool *handled)
{
	bool forward_zone = false;

	*handled = false;
	if (z == NULL || !dns_harden_enabled(&dsstate->harden)) {
		return WERR_OK;
	}
	forward_zone = dnsserver_harden_is_forward_zone(z);

	if (strcasecmp(operation, "ResetDwordProperty") == 0) {
		if (!forward_zone) {
			return WERR_OK;
		}
		*handled = true;
		if (typeid != DNSSRV_TYPEID_NAME_AND_PARAM) {
			return WERR_DNS_ERROR_INVALID_PROPERTY;
		}
		return dnsserver_harden_reset_dword(dsstate, mem_ctx, z,
						    r->NameAndParam);
	}

	if (strcasecmp(operation, "MasterServers") == 0) {
		if (!forward_zone) {
			return WERR_OK;
		}
		*handled = true;
		return dnsserver_harden_set_masters(dsstate, mem_ctx, z, typeid,
						    r);
	}

	if (strcasecmp(operation, "DeleteZoneFromDs") == 0 ||
	    strcasecmp(operation, "DeleteZone") == 0) {
		if (!z->partition->is_local) {
			return WERR_OK;
		}
		*handled = true;
		return dnsserver_harden_delete_local_zone(dsstate, z);
	}

	return WERR_OK;
}
