Harden-Box : prise en charge des forêts multi-domaines dans Samba AD (EXPÉRIMENTAL)
=================================================================================

> English version: [README.md](README.md)

Cette branche du fork [HardenSamba](https://github.com/HardenSamba) de
Samba amorce la prise en charge des **forêts Active Directory
multi-domaines** (domaines parent et enfants dans la même forêt) par le
contrôleur de domaine Samba AD. Le README d'origine de Samba se trouve
dans [README.md](README.md), après la partie Harden-Box.

> **Attention : expérimental, à ne pas utiliser en production.** La
> phase 1 crée un domaine enfant et démarre son premier DC, mais les DC
> du domaine parent et du domaine enfant ne savent pas encore
> s'authentifier entre eux et ne répliquent donc pas. Voir
> [Limites connues](#limites-connues).

- Base : `master` de Samba au commit `f6fddd2232c9` (4.26.0pre1, 30/10/2026)
- Branche : `_Harden-Box-Multi-domains-supports-phase1`
- 11 commits de code + cette documentation, au-dessus du commit de base


Contexte
--------

Samba upstream ne gère que les forêts à un seul domaine. Un DC Samba
peut approuver d'autres forêts (trusts externes et de forêt), mais il ne
peut pas être DC d'un domaine enfant, et une forêt Samba ne peut pas
avoir de domaine enfant.

Des travaux ont eu lieu entre 2011 et 2015 (répliques partielles du
catalogue global, `samba-tool domain join ... SUBDOMAIN`, environnement
selftest `subdom_dc`), mais ils n'ont jamais été terminés, et le rôle
SUBDOMAIN a été retiré en 2019 (commit `5583208aed0e`, « il ne
fonctionne pas actuellement »). De nombreux endroits du code le disent
encore : « We don't support WITHIN_FOREST yet » (KDC, winbindd),
« TODO: handle multiple domains in a forest » (LSA), etc.

La phase 1 (cette branche) rétablit la création d'un domaine enfant,
corrige tout ce qui bloquait en chemin et ajoute un environnement de
test, pour pouvoir développer et tester les phases suivantes (Kerberos,
NTLM, catalogue global...).


Utilisation
-----------

Sur le serveur qui sera le premier DC du nouveau domaine enfant :

```
samba-tool domain join enfant.example.com SUBDOMAIN \
    --parent-domain=example.com \
    --server=dc1.example.com \
    --option="workgroup=ENFANT" \
    -UAdministrator@EXAMPLE.COM \
    --adminpass='<mot de passe de l Administrator du nouveau domaine>'
```

- Le nom NetBIOS du nouveau domaine vient de `workgroup` (par `--option`
  ou dans `smb.conf`) ; il est obligatoire.
- Par défaut, `--parent-domain` est le nom DNS du nouveau domaine sans
  son premier libellé.
- Il faut les identifiants d'un Enterprise Admin de la forêt (le crossRef
  du nouveau domaine est créé sur le maître d'attribution des noms de
  domaine).
- Sans `--adminpass`, le mot de passe de l'Administrator du nouveau
  domaine est aléatoire.
- Le DC parent doit lui aussi tourner avec cette branche (voir les
  modifications de LSA, winbindd, partition et extended_dn_out
  ci-dessous).

Ce que fait le join :

1. Il se connecte à un DC du domaine parent, trouve le maître
   d'attribution des noms de domaine et le SID du domaine racine de la
   forêt (dans son crossRef).
2. Il crée l'objet serveur, puis le crossRef du nouveau domaine et son
   objet nTDSDSA sur le maître d'attribution des noms avec `DsAddEntry`.
   Le nCName porte le GUID et le SID du nouveau domaine.
3. Il réplique les NC Schema et Configuration.
4. Il provisionne localement le NC du nouveau domaine
   (`FILL_SUBDOMAIN`), avec sa partition DomainDnsZones.
5. Il crée le trust `WITHIN_FOREST` bidirectionnel : côté parent avec
   LSA `CreateTrustedDomainEx2` sur le DC parent, côté enfant (avec le
   compte de trust) localement.
6. Dans la zone DNS du parent, il ajoute la délégation de la zone
   enfant (NS + enregistrements glue A/AAAA) et le CNAME
   `<ntdsguid>._msdcs.<forêt>`.


Ce qui fonctionne
-----------------

Testé avec l'environnement selftest `subdom_dc` (un domaine enfant
`sub.samba.example.com` de la forêt de `ad_dc_ntvfs`,
`samba.example.com`) :

- le join aboutit et le DC enfant démarre (LDAP, KDC, DNS, winbindd) ;
- le rootDSE du DC enfant présente son propre NC de domaine ainsi que la
  racine de la forêt et les NC Configuration et Schema ;
- les deux DC voient le crossRef du domaine enfant, avec un
  `trustParent` qui pointe vers le crossRef du parent ;
- les deux DC ont le trust `WITHIN_FOREST` bidirectionnel, avec les bons
  SID ;
- le domaine enfant n'a pas de groupes Enterprise Admins, Schema Admins
  ni Enterprise Read-only Domain Controllers (ils n'existent que dans la
  racine de la forêt) ;
- les descripteurs de sécurité du domaine enfant utilisent les groupes
  Enterprise Admins et Schema Admins du domaine racine ;
- les DC du domaine parent continuent de fonctionner une fois que la
  forêt a un domaine enfant (winbindd s'arrêtait, et un second DC du
  domaine parent ne pouvait plus démarrer).


Limites connues
---------------

Ce sont les prochaines phases du travail :

- **Kerberos dans la forêt :** le KDC refuse toujours les trusts
  `WITHIN_FOREST` (`source4/kdc/db-glue.c` : « We don't support
  WITHIN_FOREST yet ») et ne donne pas de referrals vers les autres
  domaines de la forêt. Les DC des deux domaines ne peuvent donc pas
  s'authentifier entre eux, et la réplication échoue
  (`NT_STATUS_LOGON_FAILURE`).
- **Relais NTLM (pass-through) :** winbindd ignore encore les domaines de
  la forêt (`source3/winbindd/winbindd_util.c`, `winbindd_msrpc.c`).
- **LSA LookupNames / LookupSids** ne résolvent que les noms du domaine
  local (`source4/rpc_server/lsa/lsa_lookup.c`, « TODO: handle multiple
  domains in a forest »).
- **Netlogon :** `DsrEnumerateDomainTrusts` renvoie toujours
  `parent_index = 0`.
- **Appartenances entre domaines :** les Enterprise Admins de la racine
  ne sont pas encore ajoutés aux groupes Administrators et Denied RODC
  Password Replication du domaine enfant.
- **Catalogue global :** le DC parent crée la réplique partielle (non
  instanciée) du NC du nouveau domaine, mais la réplication de
  l'ensemble partiel d'attributs entre domaines n'est pas encore testée.
- **DNS :** la partition ForestDnsZones n'est pas répliquée sur le DC
  enfant.
- Testé uniquement avec des DC Samba des deux côtés. Un domaine enfant
  Samba sous une racine Windows, et l'inverse, ne sont pas testés.
- `subdom_dc` n'est pas encore dans `script/autobuild.py`.


Modifications en détail
-----------------------

| # | Commit | Fichiers | Quoi et pourquoi |
|---|--------|----------|------------------|
| 1 | `s4:dsdb:partition: let partitions created at runtime create their database` | `source4/dsdb/samdb/ldb_modules/partition_init.c` | La sam.ldb d'un DC en fonctionnement est ouverte avec `LDB_FLG_DONT_CREATE_DB` : une partition créée à chaud (la réplique partielle d'un nouveau domaine sur un catalogue global, via `DsAddEntry`) ne pouvait pas créer sa base et le join échouait. L'option n'est plus levée que pendant la connexion du backend d'une partition créée par `partition_create()`. |
| 2 | `s4:dsdb: keep GUID and SID on a new partial replica NC head` | `source4/dsdb/common/util.c` | `dsdb_create_partial_replica_NC()` conserve désormais le GUID et le SID fournis avec le nCName du nouveau crossRef. Sans le SID, `dsdb_trust_parse_crossref_info()` échouait pour le nouveau domaine, ce qui cassait winbindd et LSA sur tous les DC. |
| 3 | `s4:dsdb:descriptor: resolve EA, SA, RO and EK with the forest root SID` | `source4/dsdb/common/util.c`, `libcli/security/sddl.c`, `libcli/security/sddl.h`, `source4/dsdb/samdb/ldb_modules/descriptor.c` | Enterprise Admins, Schema Admins, Enterprise Read-only DCs et Enterprise Key Admins n'existent que dans le domaine racine, mais le module descriptor les résolvait avec le SID du domaine local. Ajout de `samdb_forest_root_domain_sid()` (SID du nCName du crossRef racine) et de `sddl_decode_forest()`, utilisés pour le propriétaire/groupe par défaut et le `defaultSecurityDescriptor` du schéma. Rien ne change dans le domaine racine. |
| 4 | `s4:dsdb:extended_dn_out: keep one-way links to NCs of other domains` | `source4/dsdb/samdb/ldb_modules/extended_dn_out.c` | Les valeurs DN dont la cible est introuvable localement étaient masquées, comme des liens vers des objets supprimés. Dans une forêt multi-domaines, beaucoup pointent vers le NC d'un autre domaine (nCName de son crossRef, msDS-hasMasterNCs de ses DC), comme les phantoms de Windows. Elles sont maintenant conservées quand le NC le plus spécifique de la forêt qui contient la cible n'est pas détenu localement. |
| 5 | `s4:dsdb:util_trusts: dsdb_trust_xref_tdo_info() is about the forest root` | `source4/dsdb/common/util_trusts.c` | La fonction cherchait le crossRef du domaine local avec un filtre qui ne correspond qu'à la racine : elle échouait toujours sur un DC enfant. Elle utilise maintenant le DN de la racine. |
| 6 | `s4:rpc_server:lsa: allow WITHIN_FOREST trusts with our parent or child` | `source4/rpc_server/lsa/dcesrv_lsa.c` | `CreateTrustedDomain*` refusait tout trust `WITHIN_FOREST`. Il est accepté si le domaine figure dans notre conteneur Partitions (crossRef de domaine AD avec le même dnsRoot et le même nom NetBIOS, même SID s'il est connu) et s'il est notre parent ou notre enfant direct (`trustParent`). |
| 7 | `s3:winbindd: don't abort on trusts ignored by add_trusted_domains_dc()` | `source3/winbindd/winbindd_util.c` | Les trusts PIM, `WITHIN_FOREST` et cross-organization sont ignorés, mais une seconde boucle échouait ensuite (« Can't find winbindd domain »), ce qui faisait quitter winbindd (et samba) sur tout DC ayant un tel trust, y compris les DC parents. |
| 8 | `python:kcc: take the GUID and SID of a NC from its crossRef` | `python/samba/kcc/kcc_utils.py` | Le KCC chargeait chaque NC de la forêt par une recherche sur sa tête, ce qui échoue pour le NC d'un autre domaine. Le GUID et le SID viennent maintenant du nCName du crossRef. |
| 9 | `python:provision: fix the provision of a child domain (FILL_SUBDOMAIN)` | `source4/setup/provision_users.ldif`, `source4/setup/provision_users_forest_root.ldif` (nouveau), `source4/setup/provision_users_forest_root_modify.ldif` (nouveau), `python/samba/provision/__init__.py`, `python/samba/provision/sambadns.py`, `python/samba/descriptor.py` | Les groupes de la racine (et leurs appartenances) sont déplacés dans leurs propres fichiers LDIF, utilisés seulement pour la racine (`FILL_FULL`). Les descripteurs des objets du NC de domaine sont réécrits avec le SID racine (`fix_forest_root_sids()`). Correction d'une `NameError` (`managedservice_descr`) en `FILL_SUBDOMAIN`. Le crossRef de DomainDnsZones (NC Configuration) est créé en system, car l'Administrator d'un domaine enfant n'y a aucun droit. Rien ne change pour `FILL_FULL` et `FILL_DRS`. |
| 10 | `samba-tool domain join: restore the SUBDOMAIN role (EXPERIMENTAL)` | `python/samba/join.py`, `python/samba/netcmd/domain/join.py` | Retour de `join_subdomain()`, `get_naming_master()` et du rôle `SUBDOMAIN`, avec `--parent-domain` et `--adminpass`. Corrigé : le trust est établi avec le domaine parent (pas la racine) sur le DC parent ; GUID et SID du nouveau domaine dans le nCName ; rien n'est modifié dans le NC du domaine parent (compte machine, topologie DFSR, RID manager) ; DomainDnsZones n'est pas listé dans l'objet nTDSDSA ; LSA par named pipes ; taille de carte LMDB complète pour le nouveau domaine ; compte de trust local créé avec le contrôle utilisé par LSA ; délégation DNS et CNAME `_msdcs` ; l'exception est affichée avant le nettoyage d'un join raté. |
| 11 | `selftest: add the subdom_dc environment and samba.tests.subdomain` | `selftest/target/Samba4.pm`, `selftest/target/Samba.pm`, `source4/selftest/tests.py`, `python/samba/tests/subdomain.py` (nouveau) | Retour de l'environnement `subdom_dc` (retiré en 2019), avec les variables `SUBDOM_DC_SERVER` et `PARENT_*`, et `samba.tests.subdomain` vérifie le résultat du join sur les deux DC (6 tests). |


Tests
-----

Compiler avec le selftest activé, puis lancer les tests multi-domaines :

```
./configure --enable-selftest --enable-debug
make -j$(nproc)
make test TESTS="samba.tests.subdomain"
```

Pour obtenir un shell dans l'environnement de test :

```
SELFTEST_TESTENV=subdom_dc make testenv
```

Résultats sur cette branche :

- `samba.tests.subdomain` : 6/6 OK.
- Aucune régression (ALL OK) dans : `samba.tests.provision`,
  `samba.tests.dsdb`, `samba.tests.sddl`, `samba.tests.security_descriptors`,
  `samba4.blackbox.provision.py`, `samba4.blackbox.upgradeprovision.current`,
  `samba4.blackbox.join_ldapcmp` (5 environnements),
  `samba4.blackbox.dbcheck.release-4-5-0-pre1` (+ quick, + links),
  `samba4.blackbox.trust_utils(fl2008r2dc)`, et
  `samba4.drs.repl_secdesc` / `samba4.drs.repl_move` sur `vampire_dc`
  (un second DC du domaine parent, avec un domaine enfant déjà présent
  dans la forêt).
- La suite de tests complète de Samba n'a pas été lancée.


Feuille de route
----------------

1. **Kerberos dans la forêt :** clés inter-realm des trusts
   `WITHIN_FOREST` dans le KDC (`samba_kdc_trust_message2entry()`),
   referrals vers les autres domaines (routage via le parent), PAC et
   filtrage de SID dans la forêt. Les DC pourront alors s'authentifier
   entre eux et répliquer.
2. **winbindd, LSA, Netlogon :** domaines de la forêt dans winbindd
   (`NETR_TRUST_FLAG_IN_FOREST`, relais NTLM), LookupNames/LookupSids
   multi-domaines, `parent_index` dans `DsrEnumerateDomainTrusts`.
3. **Réplication entre domaines :** Configuration et Schema dans les deux
   sens, ForestDnsZones, topologie KCC entre domaines.
4. **Catalogue global :** répliques partielles des autres domaines
   (ensemble partiel d'attributs), groupes universels dans les jetons,
   recherches GC sur le port 3268.
5. **Références entre domaines :** appartenance des groupes de la racine
   dans les domaines enfants, rôle de maître d'infrastructure.
6. **Interopérabilité :** domaine enfant Samba sous une racine Windows et
   l'inverse ; `subdom_dc` dans autobuild.


Soumission upstream
-------------------

Les commits suivent les conventions de Samba (une modification par
commit, sujets au format `composant: résumé`). Avant de les envoyer au
projet Samba, chaque commit doit porter une ligne `Signed-off-by:` de
son auteur et passer les relectures décrites dans
[README.contributing](README.contributing).
