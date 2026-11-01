/*
   Unix SMB/CIFS implementation.

   DNS Server

   Copyright (C) Amitay Isaacs 2011

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

#ifndef __DNSSERVER_H__
#define __DNSSERVER_H__

#include "librpc/gen_ndr/dnsp.h"
#include "librpc/gen_ndr/dnsserver.h"
#include "param/param.h"
#include "ldb.h"
#include "dns_server/dns_harden_common.h"

/*
 * See [MS-DNSP] 2.3.2.1.1 Property Id
 *
 * DSPROPERTY_ZONE_NOREFRESH_INTERVAL:
 * The zone no refresh interval. See
 * dwNoRefreshInterval (section 2.2.5.2.4.1).
 * Default: 168 hours/7 days.
 *
 * DSPROPERTY_ZONE_REFRESH_INTERVAL:
 * The zone refresh interval. See
 * dwRefreshInterval (section 2.2.5.2.4.1).
 * Default: 168 hours/7 days.
 */
#define DNS_SERVER_DEFAULT_NO_REFRESH_INTERVAL (24 * 7)
#define DNS_SERVER_DEFAULT_REFRESH_INTERVAL (24 * 7)

struct dnsserver_serverinfo {
	uint32_t	dwVersion;
	uint8_t		fBootMethod;
	uint8_t		fAdminConfigured;
	uint8_t		fAllowUpdate;
	uint8_t		fDsAvailable;

	char *		pszServerName;
	char *		pszDsContainer;

	uint32_t	dwDsForestVersion;
	uint32_t	dwDsDomainVersion;
	uint32_t	dwDsDsaVersion;
	uint32_t	fReadOnlyDC;
	char *		pszDomainName;
	char *		pszForestName;
	char *		pszDomainDirectoryPartition;
	char *		pszForestDirectoryPartition;

	struct DNS_ADDR_ARRAY * aipServerAddrs;
	struct DNS_ADDR_ARRAY * aipListenAddrs;
	struct IP4_ARRAY * aipForwarders;

	struct IP4_ARRAY * aipLogFilter;
	char *		pwszLogFilePath;

	uint32_t 	dwLogLevel;
	uint32_t 	dwDebugLevel;
	uint32_t 	dwEventLogLevel;
	uint32_t 	dwLogFileMaxSize;

	uint32_t 	dwForwardTimeout;
	uint32_t 	dwRpcProtocol;
	uint32_t 	dwNameCheckFlag;
	uint32_t 	cAddressAnswerLimit;
	uint32_t 	dwRecursionRetry;
	uint32_t 	dwRecursionTimeout;
	uint32_t 	dwMaxCacheTtl;
	uint32_t 	dwDsPollingInterval;
	uint32_t 	dwLocalNetPriorityNetMask;

	uint32_t 	dwScavengingInterval;
	uint32_t 	dwDefaultRefreshInterval;
	uint32_t 	dwDefaultNoRefreshInterval;
	uint32_t 	dwLastScavengeTime;

	uint8_t 	fAutoReverseZones;
	uint8_t 	fAutoCacheUpdate;

	uint8_t 	fRecurseAfterForwarding;
	uint8_t 	fForwardDelegations;
	uint8_t 	fNoRecursion;
	uint8_t 	fSecureResponses;

	uint8_t 	fRoundRobin;
	uint8_t 	fLocalNetPriority;

	uint8_t 	fBindSecondaries;
	uint8_t 	fWriteAuthorityNs;

	uint8_t 	fStrictFileParsing;
	uint8_t 	fLooseWildcarding;
	uint8_t 	fDefaultAgingState;
};

struct dnsserver_zoneinfo {
	uint8_t		Version;
	uint32_t	Flags;
	uint8_t		dwZoneType;
	uint8_t		fReverse;
	uint8_t		fAllowUpdate;
	uint8_t		fPaused;
	uint8_t		fShutdown;
	uint8_t		fAutoCreated;

	uint8_t		fUseDatabase;
	char *		pszDataFile;

	struct IP4_ARRAY * aipMasters;

	uint32_t	fSecureSecondaries;
	uint32_t	fNotifyLevel;
	struct IP4_ARRAY * aipSecondaries;
	struct IP4_ARRAY * aipNotify;

	uint32_t	fUseWins;
	uint32_t	fUseNbstat;

	uint32_t	fAging;
	uint32_t	dwNoRefreshInterval;
	uint32_t	dwRefreshInterval;
	uint32_t	dwAvailForScavengeTime;
	struct IP4_ARRAY * aipScavengeServers;

	uint32_t	dwForwarderTimeout;
	uint32_t	fForwarderSlave;

	struct IP4_ARRAY * aipLocalMasters;

	char *		pwszZoneDn;

	uint32_t	dwLastSuccessfulSoaCheck;
	uint32_t	dwLastSuccessfulXfr;

	uint32_t	fQueuedForBackgroundLoad;
	uint32_t	fBackgroundLoadInProgress;
	uint8_t		fReadOnlyZone;

	uint32_t	dwLastXfrAttempt;
	uint32_t	dwLastXfrResult;

	/*
	 * Harden-Box: the masters of a conditional forwarder or a stub
	 * zone, IPv6 included, for the DNS_ADDR_ARRAY of LONGHORN clients
	 */
	struct DNS_ADDR_ARRAY *aipMastersDa;
};


struct dnsserver_partition {
	struct dnsserver_partition *prev, *next;
	struct ldb_dn *partition_dn;
	const char *pszDpFqdn;
	uint32_t dwDpFlags;
	bool is_forest;
	int zones_count;
	/* Harden-Box: CN=MicrosoftDNS,CN=System,<domain NC> */
	bool is_legacy;
	/* Harden-Box: the local zone store (not AD-integrated zones) */
	bool is_local;
};


struct dnsserver_partition_info {
	const char *pszCrDn;
	uint32_t dwState;
	uint32_t dwReplicaCount;
	struct DNS_RPC_DP_REPLICA **ReplicaArray;
};


struct dnsserver_zone {
	struct dnsserver_zone *prev, *next;
	struct dnsserver_partition *partition;
	const char *name;
	struct ldb_dn *zone_dn;
	struct dnsserver_zoneinfo *zoneinfo;
	struct dnsp_DnsProperty *tmp_props;
	int32_t num_props;
};

struct dnsserver_state {
	struct loadparm_context *lp_ctx;
	struct ldb_context *samdb;
	struct dnsserver_partition *partitions;
	struct dnsserver_zone *zones;
	int zones_count;
	struct dnsserver_serverinfo *serverinfo;
	/* Harden-Box options, local zone store (see dnsharden.c) */
	struct dns_harden_options harden;
	struct ldb_context *local_ldb;
	struct tevent_context *ev;
	struct auth_session_info *session_info;
};


struct dns_tree {
	const char *name;
	int level;
	unsigned int num_children;
	struct dns_tree **children;
	void *data;
};

/* Data structure manipulation functions from dnsdata.c */

struct IP4_ARRAY *ip4_array_copy(TALLOC_CTX *mem_ctx, struct IP4_ARRAY *ip4);
struct DNS_ADDR_ARRAY *ip4_array_to_dns_addr_array(TALLOC_CTX *mem_ctx, struct IP4_ARRAY *ip4);
struct IP4_ARRAY *dns_addr_array_to_ip4_array(TALLOC_CTX *mem_ctx,
					      struct DNS_ADDR_ARRAY *ip);
struct DNS_ADDR_ARRAY *dns_addr_array_copy(TALLOC_CTX *mem_ctx, struct DNS_ADDR_ARRAY *addr);

int dns_split_name_components(TALLOC_CTX *mem_ctx, const char *name, char ***components);
char *dns_split_node_name(TALLOC_CTX *mem_ctx, const char *node_name, const char *zone_name);

int dns_name_compare(struct ldb_message * const *m1, struct ldb_message * const *m2,
		     const char *search_name);
bool dns_record_match(struct dnsp_DnssrvRpcRecord *rec1, struct dnsp_DnssrvRpcRecord *rec2);

void dnsp_to_dns_copy(TALLOC_CTX *mem_ctx, struct dnsp_DnssrvRpcRecord *dnsp,
			struct DNS_RPC_RECORD *dns);
WERROR dns_to_dnsp_convert(TALLOC_CTX *mem_ctx, struct DNS_RPC_RECORD *dns,
			   struct dnsp_DnssrvRpcRecord **out_dnsp,
			   bool check_name);

struct dns_tree *dns_build_tree(TALLOC_CTX *mem_ctx, const char *name, struct ldb_result *res);
WERROR dns_fill_records_array(TALLOC_CTX *mem_ctx, struct dnsserver_zone *z,
			enum dns_record_type record_type,
			unsigned int select_flag, const char *zone_name,
			struct ldb_message *msg, int num_children,
			struct DNS_RPC_RECORDS_ARRAY *recs,
			char ***add_names, int *add_count);


/* Utility functions from dnsutils.c */

struct dnsserver_serverinfo *dnsserver_init_serverinfo(TALLOC_CTX *mem_ctx,
					struct loadparm_context *lp_ctx,
					struct ldb_context *samdb);
struct dnsserver_zoneinfo *dnsserver_init_zoneinfo(struct dnsserver_zone *zone,
					struct dnsserver_serverinfo *serverinfo);
struct dnsserver_zone *dnsserver_find_zone(struct dnsserver_zone *zones,
					const char *zone_name);
struct ldb_dn *dnsserver_name_to_dn(TALLOC_CTX *mem_ctx, struct dnsserver_zone *z,
					const char *name);
uint32_t dnsserver_zone_to_request_filter(const char *zone);

/* Database functions from dnsdb.c */

struct dnsserver_partition *dnsserver_db_enumerate_partitions(TALLOC_CTX *mem_ctx,
					struct dnsserver_serverinfo *serverinfo,
					struct ldb_context *samdb);
struct dnsserver_zone *dnsserver_db_enumerate_zones(TALLOC_CTX *mem_ctx,
					struct ldb_context *samdb,
					struct dnsserver_partition *p);
struct dnsserver_partition_info *dnsserver_db_partition_info(TALLOC_CTX *mem_ctx,
					struct ldb_context *samdb,
					struct dnsserver_partition *p);
WERROR dnsserver_db_add_empty_node(TALLOC_CTX *mem_ctx,
					struct ldb_context *samdb,
					struct dnsserver_zone *z,
					const char *node_name);
WERROR dnsserver_db_add_record(TALLOC_CTX *mem_ctx,
					struct ldb_context *samdb,
					struct dnsserver_zone *z,
					const char *node_name,
					struct DNS_RPC_RECORD *add_record);
WERROR dnsserver_db_update_record(TALLOC_CTX *mem_ctx,
					struct ldb_context *samdb,
					struct dnsserver_zone *z,
					const char *node_name,
					struct DNS_RPC_RECORD *add_record,
					struct DNS_RPC_RECORD *del_record);
WERROR dnsserver_db_do_reset_dword(struct ldb_context *samdb,
					struct dnsserver_zone *z,
					struct DNS_RPC_NAME_AND_PARAM *n_p);
WERROR dnsserver_db_delete_record(TALLOC_CTX *mem_ctx,
				  struct ldb_context *samdb,
				  struct dnsserver_zone *z,
				  const char *node_name,
				  struct DNS_RPC_RECORD *del_record);
WERROR dnsserver_db_create_zone(struct ldb_context *samdb,
				struct dnsserver_partition *partitions,
				struct dnsserver_zone *z,
				struct loadparm_context *lp_ctx);
WERROR dnsserver_db_delete_zone(struct ldb_context *samdb,
				struct dnsserver_zone *z);
WERROR dnsserver_db_do_create_zone(TALLOC_CTX *tmp_ctx,
				   struct ldb_context *samdb,
				   struct ldb_dn *zone_dn,
				   struct dnsserver_zone *z);
WERROR dnsserver_db_create_zone_in(struct ldb_context *samdb,
				   struct dnsserver_partition *p,
				   struct dnsserver_zone *zone,
				   struct loadparm_context *lp_ctx);

/* Harden-Box: conditional forwarders, stub zones, legacy partition */
struct dcesrv_call_state;
void dnsserver_harden_add_partitions(struct dnsserver_state *dsstate);
struct dnsserver_zone *dnsserver_harden_enumerate_zones(
	struct dnsserver_state *dsstate,
	struct dnsserver_partition *p,
	bool *failed);
void dnsserver_harden_fix_zoneinfo(struct dnsserver_state *dsstate,
				   struct dnsserver_zone *z);
bool dnsserver_harden_zone_matches(struct dnsserver_zone *z,
				   uint32_t filter);
bool dnsserver_harden_is_forward_zone(struct dnsserver_zone *z);
struct DNS_ADDR_ARRAY *dnsserver_harden_masters(TALLOC_CTX *mem_ctx,
						struct dnsserver_zoneinfo *zi);
WERROR dnsserver_harden_create_zone(struct dnsserver_state *dsstate,
				    TALLOC_CTX *mem_ctx,
				    enum DNS_RPC_TYPEID typeid,
				    union DNSSRV_RPC_UNION *r);
WERROR dnsserver_harden_operate_zone(struct dnsserver_state *dsstate,
				     TALLOC_CTX *mem_ctx,
				     struct dnsserver_zone *z,
				     const char *operation,
				     enum DNS_RPC_TYPEID typeid,
				     union DNSSRV_RPC_UNION *r,
				     bool *handled);
struct ldb_context *dnsserver_zone_ldb(struct dnsserver_state *dsstate,
				       struct dnsserver_zone *z);
void dnsserver_harden_notify_dns_server(struct dnsserver_state *dsstate);

#endif /* __DNSSERVER_H__ */
