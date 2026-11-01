/*
   Unix SMB/CIFS implementation.

   DNS server: conditional forwarders and stub zones (Harden-Box)

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

/*
 * With "harden:dns conditional forwarders = yes" (or "harden:dns stub
 * zones = yes"), the zones of type DNS_ZONE_TYPE_FORWARDER (or
 * DNS_ZONE_TYPE_STUB) are not authoritative zones of this server: the
 * queries for the names of such a zone are forwarded to its master
 * servers, the most specific zone wins.
 *
 * The zones come from AD (any DNS partition) and from the local zone
 * store (dns_local_zones.ldb, the zones that are not AD-integrated).
 *
 * A stub zone is resolved through its master servers too (Windows
 * loads the NS records of the zone from them and asks those servers):
 * the master servers of a stub zone have to be authoritative servers of
 * the zone.
 */

#include "includes.h"
#include "samba/service_task.h"
#include "dns_server/dns_server.h"
#include "param/param.h"
#include "librpc/ndr/libndr.h"
#include "librpc/gen_ndr/ndr_dnsp.h"
#include <ldb.h>
#include "dsdb/samdb/samdb.h"
#include "lib/util/dlinklist.h"

static struct dns_forward_zone *dns_harden_new_forward_zone(
	TALLOC_CTX *mem_ctx,
	const char *name,
	const struct ldb_message_element *props,
	bool local)
{
	struct dns_forward_zone *fz = NULL;

	fz = talloc_zero(mem_ctx, struct dns_forward_zone);
	if (fz == NULL) {
		return NULL;
	}
	fz->name = talloc_strdup(fz, name);
	if (fz->name == NULL) {
		TALLOC_FREE(fz);
		return NULL;
	}
	dns_zone_forwarding_from_props(fz, props, &fz->zf);
	fz->local = local;
	return fz;
}

/*
 * Move the conditional forwarders and stub zones of *zones to *fwd, and
 * add those of the local zone store.
 */
NTSTATUS dns_harden_split_zones(struct dns_server *dns,
				TALLOC_CTX *mem_ctx,
				struct dns_server_zone **zones,
				struct dns_forward_zone **fwd)
{
	static const char * const attrs[] = { "name", "dNSProperty", NULL };
	struct dns_server_zone *z = NULL;
	struct dns_server_zone *next = NULL;
	struct ldb_context *local = NULL;
	struct ldb_result *res = NULL;
	struct ldb_dn *base = NULL;
	TALLOC_CTX *frame = talloc_stackframe();
	unsigned int i;
	int ret;

	*fwd = NULL;

	for (z = *zones; z != NULL; z = next) {
		struct ldb_message_element *el = NULL;
		struct dns_zone_forwarding zf;
		struct dns_forward_zone *fz = NULL;

		next = z->next;

		ret = ldb_search(dns->samdb, frame, &res, z->dn,
				 LDB_SCOPE_BASE, attrs, "(objectClass=dnsZone)");
		if (ret != LDB_SUCCESS || res->count != 1) {
			continue;
		}
		el = ldb_msg_find_element(res->msgs[0], "dNSProperty");
		dns_zone_forwarding_from_props(frame, el, &zf);
		if (!dns_harden_forwarding_type(&dns->harden, zf.zone_type)) {
			continue;
		}

		fz = dns_harden_new_forward_zone(mem_ctx, z->name, el, false);
		if (fz == NULL) {
			TALLOC_FREE(frame);
			return NT_STATUS_NO_MEMORY;
		}
		DBG_INFO("Zone %s is a %s zone, forwarded to %u servers\n",
			 z->name,
			 zf.zone_type == DNS_ZONE_TYPE_STUB ?
			 "stub" : "conditional forwarder",
			 fz->zf.num_masters);
		DLIST_ADD_END(*fwd, fz);
		DLIST_REMOVE(*zones, z);
		talloc_free(z);
	}

	local = dns_local_zones_connect(frame, dns->task->event_ctx,
					dns->task->lp_ctx);
	if (local == NULL) {
		TALLOC_FREE(frame);
		return NT_STATUS_OK;
	}
	base = dns_local_zone_dn(frame, local, NULL);
	if (base == NULL) {
		TALLOC_FREE(frame);
		return NT_STATUS_NO_MEMORY;
	}
	ret = ldb_search(local, frame, &res, base, LDB_SCOPE_ONELEVEL, attrs,
			 "(name=*)");
	if (ret != LDB_SUCCESS) {
		/* no local zones yet */
		TALLOC_FREE(frame);
		return NT_STATUS_OK;
	}
	for (i = 0; i < res->count; i++) {
		struct ldb_message_element *el = ldb_msg_find_element(
			res->msgs[i], "dNSProperty");
		const char *name = ldb_msg_find_attr_as_string(res->msgs[i],
							       "name", NULL);
		struct dns_zone_forwarding zf;
		struct dns_forward_zone *fz = NULL;

		if (name == NULL) {
			continue;
		}
		dns_zone_forwarding_from_props(frame, el, &zf);
		if (!dns_harden_forwarding_type(&dns->harden, zf.zone_type)) {
			continue;
		}
		fz = dns_harden_new_forward_zone(mem_ctx, name, el, true);
		if (fz == NULL) {
			TALLOC_FREE(frame);
			return NT_STATUS_NO_MEMORY;
		}
		DBG_INFO("Local zone %s forwarded to %u servers\n",
			 name, fz->zf.num_masters);
		DLIST_ADD_END(*fwd, fz);
	}

	TALLOC_FREE(frame);
	return NT_STATUS_OK;
}

/*
 * The conditional forwarder or stub zone name belongs to, if it is more
 * specific than the authoritative zone of name (if any).
 */
const struct dns_forward_zone *dns_harden_forward_zone(
	struct dns_server *dns, const char *name)
{
	const struct dns_forward_zone *fz = NULL;
	const struct dns_forward_zone *best = NULL;
	const char *auth_zone = NULL;
	size_t host_part_len = 0;

	if (dns->forward_zones == NULL || name == NULL) {
		return NULL;
	}

	for (fz = dns->forward_zones; fz != NULL; fz = fz->next) {
		if (!dns_name_match(fz->name, name, &host_part_len)) {
			continue;
		}
		if (best == NULL || strlen(fz->name) > strlen(best->name)) {
			best = fz;
		}
	}
	if (best == NULL) {
		return NULL;
	}

	auth_zone = dns_get_authoritative_zone(dns, name);
	if (auth_zone != NULL && strlen(auth_zone) > strlen(best->name)) {
		/* a zone of ours below the forwarder zone */
		return NULL;
	}

	return best;
}
