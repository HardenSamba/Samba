/*
   ldb database library

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
 * Name: ldb
 *
 * Component: ldb onelevel_order module
 *
 * Description: return the results of the one-level LDAP searches in
 * the order of their RDN, as a Windows DC does.
 *
 * A Windows DC answers a one-level search through its index on the
 * parent and the RDN of the objects: the children of a container come
 * back sorted by name (case-insensitive). Tools such as Active
 * Directory Users and Computers show the objects of a container in the
 * order the DC returns them. Samba returns them in the order of its
 * one-level index (by GUID), which looks random.
 *
 * With "harden:sort one-level results = yes" in smb.conf, the one-level
 * searches that come from LDAP and don't ask for an order themselves
 * (server side sort or VLV controls) get their entries sorted by the
 * value of their RDN. The module sits below dsdb_paged_results, so a
 * paged search is sorted as a whole. It does nothing otherwise.
 */

#include "includes.h"
#include "ldb_module.h"
#include "dsdb/samdb/samdb.h"
#include "param/param.h"

struct onelevel_order_private {
	bool enabled;
};

struct onelevel_order_context {
	struct ldb_module *module;
	struct ldb_request *req;
	struct ldb_message **msgs;
	size_t num_msgs;
};

static int onelevel_order_compare(struct ldb_message **m1,
				  struct ldb_message **m2)
{
	const struct ldb_val *v1 = ldb_dn_get_rdn_val((*m1)->dn);
	const struct ldb_val *v2 = ldb_dn_get_rdn_val((*m2)->dn);
	char *s1 = NULL;
	char *s2 = NULL;
	int cmp;

	if (v1 == NULL || v2 == NULL) {
		return NUMERIC_CMP(v1 != NULL, v2 != NULL);
	}

	s1 = talloc_strndup(*m1, (const char *)v1->data, v1->length);
	s2 = talloc_strndup(*m2, (const char *)v2->data, v2->length);
	if (s1 == NULL || s2 == NULL) {
		TALLOC_FREE(s1);
		TALLOC_FREE(s2);
		return 0;
	}
	cmp = strcasecmp_m(s1, s2);
	if (cmp == 0) {
		cmp = strcmp(s1, s2);
	}
	TALLOC_FREE(s1);
	TALLOC_FREE(s2);
	return cmp;
}

static int onelevel_order_done(struct onelevel_order_context *ac,
			       struct ldb_reply *ares)
{
	size_t i;
	int ret;

	TYPESAFE_QSORT(ac->msgs, ac->num_msgs, onelevel_order_compare);

	for (i = 0; i < ac->num_msgs; i++) {
		ret = ldb_module_send_entry(ac->req, ac->msgs[i], NULL);
		if (ret != LDB_SUCCESS) {
			/*
			 * the request was aborted, ldb_module_send_entry()
			 * already told the caller
			 */
			return ret;
		}
	}

	return ldb_module_done(ac->req, ares->controls,
			       ares->response, ares->error);
}

static int onelevel_order_callback(struct ldb_request *req,
				   struct ldb_reply *ares)
{
	struct onelevel_order_context *ac =
		talloc_get_type_abort(req->context,
				      struct onelevel_order_context);
	struct ldb_message **msgs = NULL;

	if (ares == NULL) {
		return ldb_module_done(ac->req, NULL, NULL,
				       LDB_ERR_OPERATIONS_ERROR);
	}
	if (ares->error != LDB_SUCCESS) {
		return ldb_module_done(ac->req, ares->controls,
				       ares->response, ares->error);
	}

	switch (ares->type) {
	case LDB_REPLY_ENTRY:
		msgs = talloc_realloc(ac, ac->msgs, struct ldb_message *,
				      ac->num_msgs + 1);
		if (msgs == NULL) {
			return ldb_module_done(ac->req, NULL, NULL,
					       ldb_module_oom(ac->module));
		}
		msgs[ac->num_msgs++] = talloc_steal(msgs, ares->message);
		ac->msgs = msgs;
		talloc_free(ares);
		return LDB_SUCCESS;

	case LDB_REPLY_REFERRAL:
		return ldb_module_send_referral(ac->req, ares->referral);

	case LDB_REPLY_DONE:
		return onelevel_order_done(ac, ares);
	}

	talloc_free(ares);
	return LDB_SUCCESS;
}

static int onelevel_order_search(struct ldb_module *module,
				 struct ldb_request *req)
{
	struct onelevel_order_private *priv =
		talloc_get_type(ldb_module_get_private(module),
				struct onelevel_order_private);
	struct ldb_context *ldb = ldb_module_get_ctx(module);
	struct onelevel_order_context *ac = NULL;
	struct ldb_request *down_req = NULL;
	int ret;

	if (priv == NULL || !priv->enabled ||
	    req->op.search.scope != LDB_SCOPE_ONELEVEL ||
	    !ldb_req_is_untrusted(req) ||
	    ldb_request_get_control(req, LDB_CONTROL_SERVER_SORT_OID) != NULL ||
	    ldb_request_get_control(req, LDB_CONTROL_VLV_REQ_OID) != NULL) {
		return ldb_next_request(module, req);
	}

	ac = talloc_zero(req, struct onelevel_order_context);
	if (ac == NULL) {
		return ldb_oom(ldb);
	}
	ac->module = module;
	ac->req = req;

	ret = ldb_build_search_req_ex(&down_req, ldb, ac,
				      req->op.search.base,
				      req->op.search.scope,
				      req->op.search.tree,
				      req->op.search.attrs,
				      req->controls,
				      ac,
				      onelevel_order_callback,
				      req);
	LDB_REQ_SET_LOCATION(down_req);
	if (ret != LDB_SUCCESS) {
		return ret;
	}

	return ldb_next_request(module, down_req);
}

static int onelevel_order_init(struct ldb_module *module)
{
	struct ldb_context *ldb = ldb_module_get_ctx(module);
	struct loadparm_context *lp_ctx = talloc_get_type(
		ldb_get_opaque(ldb, "loadparm"), struct loadparm_context);
	struct onelevel_order_private *priv = NULL;

	priv = talloc_zero(module, struct onelevel_order_private);
	if (priv == NULL) {
		return ldb_oom(ldb);
	}
	if (lp_ctx != NULL) {
		priv->enabled = lpcfg_parm_bool(lp_ctx, NULL, "harden",
						"sort one-level results",
						false);
	}
	ldb_module_set_private(module, priv);

	return ldb_next_init(module);
}

static const struct ldb_module_ops ldb_onelevel_order_module_ops = {
	.name		   = "onelevel_order",
	.search            = onelevel_order_search,
	.init_context	   = onelevel_order_init
};

int ldb_onelevel_order_module_init(const char *version)
{
	LDB_MODULE_CHECK_VERSION(version);
	return ldb_register_module(&ldb_onelevel_order_module_ops);
}
