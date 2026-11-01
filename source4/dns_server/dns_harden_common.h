/*
   Unix SMB/CIFS implementation.

   DNS server and DNS management: the Harden-Box options

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

#ifndef __DNS_HARDEN_COMMON_H__
#define __DNS_HARDEN_COMMON_H__

struct loadparm_context;
struct ldb_context;
struct ldb_dn;
struct ldb_message_element;
/*
 * Harden-Box options (see dns_harden_common.c): conditional forwarders,
 * stub zones and the default partition of the new zones.
 */
struct tevent_context;

#define DNS_HARDEN_FORWARDER_TIMEOUT 5

struct dns_harden_options {
	bool forwarders;
	bool stubs;
	/* DNS_DP_DOMAIN_DEFAULT, DNS_DP_FOREST_DEFAULT, DNS_DP_LEGACY or 0 */
	uint32_t default_dp;
};

struct dns_zone_forwarding {
	uint32_t zone_type;
	uint32_t num_masters;
	const char **masters;
	uint32_t timeout;
	bool slave;
};

void dns_harden_get_options(struct loadparm_context *lp_ctx,
			    struct dns_harden_options *o);
bool dns_harden_enabled(const struct dns_harden_options *o);
bool dns_harden_forwarding_type(const struct dns_harden_options *o,
				uint32_t zone_type);
struct ldb_context *dns_local_zones_connect(TALLOC_CTX *mem_ctx,
					    struct tevent_context *ev,
					    struct loadparm_context *lp_ctx);
struct ldb_dn *dns_local_zone_dn(TALLOC_CTX *mem_ctx,
				 struct ldb_context *ldb,
				 const char *zone_name);
void dns_zone_forwarding_from_props(TALLOC_CTX *mem_ctx,
				    const struct ldb_message_element *el,
				    struct dns_zone_forwarding *zf);

#endif /* __DNS_HARDEN_COMMON_H__ */
