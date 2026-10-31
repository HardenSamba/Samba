Harden-Box : prise en charge des forêts multi-domaines dans Samba AD (EXPÉRIMENTAL)
=================================================================================

> English version: [README.md](README.md)

Cette branche du fork [HardenSamba](https://github.com/HardenSamba) de
Samba ajoute la prise en charge des **forêts Active Directory
multi-domaines** (domaines parent et enfants dans la même forêt) au
contrôleur de domaine Samba AD. Le README d'origine de Samba se trouve
dans [README.md](README.md), après la partie Harden-Box.

> **Attention : expérimental, à ne pas utiliser en production.** La
> phase 1 a créé un domaine enfant et démarré son premier DC. La phase 2
> fait fonctionner les deux domaines ensemble : Kerberos et NTLM d'un
> domaine à l'autre, recherches de noms et de SID par LSA, groupes de la
> racine dans le domaine enfant, réplication dans les deux sens et
> catalogue global qui contient le domaine enfant. Plusieurs points
> manquent encore, voir [Limites connues](#limites-connues).

- Base : `master` de Samba au commit `f6fddd2232c9` (4.26.0pre1, 30/10/2026)
- Branche : `_Harden-Box-Multi-domains-supports-phase2`, au-dessus de
  `_Harden-Box-Multi-domains-supports-phase1`
- Phase 1 : 11 commits de code pour le multi-domaine, 1 commit qui fait
  annoncer Active Directory Web Services par le DC (voir
  [Annonce ADWS](#annonce-dactive-directory-web-services-adws)) et sa
  documentation
- Phase 2 : 15 commits de code (numérotés de 13 à 27 dans
  [Modifications en détail](#modifications-en-détail)) et cette
  documentation


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
fonctionne pas actuellement »). De nombreux endroits du code le
disaient encore : « We don't support WITHIN_FOREST yet » (KDC,
winbindd), « TODO: handle multiple domains in a forest » (LSA), etc.

- La **phase 1** a rétabli la création d'un domaine enfant, corrigé tout
  ce qui bloquait en chemin et ajouté un environnement de test.
- La **phase 2** (cette branche) gère les trusts internes à la forêt
  (`WITHIN_FOREST`) dans le KDC, dans winbindd, dans le relais NTLM et
  dans LSA, donne aux groupes de la racine leur place dans le domaine
  enfant, fait répliquer les DC des deux domaines et permet à un
  catalogue global de contenir l'autre domaine.


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
- Les DC du domaine parent doivent eux aussi tourner avec cette branche :
  les modifications du KDC, de winbindd, de LSA, de la réplication et du
  KCC sont nécessaires des deux côtés.

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
5. Il ajoute le groupe Enterprise Admins de la racine aux
   Administrators du nouveau domaine, et Enterprise Admins et Schema
   Admins à son groupe Denied RODC Password Replication (phase 2).
6. Il crée le trust `WITHIN_FOREST` bidirectionnel : côté parent avec
   LSA `CreateTrustedDomainEx2` sur le DC parent, côté enfant (avec le
   compte de trust) localement.
7. Dans la zone DNS du parent, il ajoute la délégation de la zone
   enfant (NS + enregistrements glue A/AAAA) et le CNAME
   `<ntdsguid>._msdcs.<forêt>`.

Une fois le nouveau DC démarré, répliquer le NC Configuration dans les
deux sens (le KCC de chaque DC le maintient ensuite à jour), par exemple
depuis le nouveau DC :

```
samba-tool drs replicate <DC enfant> <DC parent> CN=Configuration,DC=example,DC=com \
    -UAdministrator@EXAMPLE.COM
samba-tool drs replicate <DC parent> <DC enfant> CN=Configuration,DC=example,DC=com \
    -UAdministrator@EXAMPLE.COM
```

Sur un catalogue global d'un autre domaine, le KCC ajoute ensuite la
réplique partielle du nouveau domaine (`samba-tool drs kcc <GC>` le
lance tout de suite).


Ce qui fonctionne
-----------------

Testé avec l'environnement selftest `subdom_dc` (un domaine enfant
`sub.samba.example.com`, NetBIOS `SAMBASUBDOM`, de la forêt de
`ad_dc_ntvfs`, `samba.example.com`, NetBIOS `SAMBADOMAIN` ; le DC parent
est catalogue global).

Phase 1 :

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
  forêt a un domaine enfant.

Phase 2 :

- **Kerberos** dans les deux sens : l'Administrator de chaque domaine
  obtient un ticket pour le service LDAP du DC de l'autre domaine
  (referral inter-realm de son propre KDC), et les DC s'authentifient
  entre eux ;
- **NTLM** dans les deux sens : binds NTLM de chaque Administrator sur le
  DC de l'autre domaine, `wbinfo -a` et `wbinfo -m` (l'autre domaine est
  listé « In-Forest ») ;
- **LSA** `LookupNames` et `LookupSids` sur les deux DC, pour les deux
  domaines : `DOMAINE\nom`, `nom@domaine.dns` (UPN), `DOMAINE\` et les
  SID ;
- **groupes de la racine :** dans le domaine enfant, Enterprise Admins de
  la racine est membre de `BUILTIN\Administrators` et du groupe Denied
  RODC Password Replication (avec Schema Admins) ; l'Administrator de la
  racine a `BUILTIN\Administrators` dans son jeton sur le DC enfant, en
  Kerberos comme en NTLM ;
- **réplication :** `DsReplicaSync` du NC Configuration dans les deux
  sens, en tant qu'Administrator de la racine ;
- **catalogue global :** le KCC du DC parent (un GC) ajoute la réplique
  partielle du NC du domaine enfant (`hasPartialReplicaNCs`), qui se
  réplique (ensemble partiel d'attributs, sans secrets), est instanciée
  (`instanceType` 9 : tête de NC, NC au-dessus) et peut être interrogée
  sur le port 3268 : une recherche depuis la racine de la forêt trouve
  les objets des deux domaines.


Fonctionnement de la phase 2
----------------------------

**Table de routage des trusts** (`source4/dsdb/common/util_trusts.c`).
Le KDC, LSA et le code NTLM cherchent « quel domaine détient ce nom ou
ce SID » dans une table de routage construite à partir des objets
trustedDomain et des crossRefs. Sur un DC enfant elle ne pouvait pas
être construite (le GUID et le SID du crossRef parent étaient lus sans
le DN étendu, et le KDC échouait avec `EINVAL`). De plus, les
informations de forêt de notre propre domaine listent les noms de
premier niveau de toute la forêt : tous les noms de la forêt étaient
donc routés vers notre propre domaine. Les noms et SID exacts des
domaines avec lesquels nous avons un trust direct (parent-enfant)
passent désormais avant ces noms de premier niveau.

**Kerberos** (`source4/kdc/db-glue.c`, `pac-glue.c`). Les clés
inter-realm d'un trust `WITHIN_FOREST` viennent de `trustAuthIncoming`
/ `trustAuthOutgoing`, comme pour les autres trusts uplevel. Une demande
pour un principal d'un autre domaine de la forêt reçoit un referral vers
le domaine suivant sur le chemin, sauf si le principal de service existe
ici : les domaines d'une forêt partagent leur espace de noms (les SPN
DRS de tous les DC utilisent la zone `_msdcs` de la racine), un SPN local
reste donc local. Les claims ne sont transformés ou effacés qu'au
passage d'une frontière de forêt, pas entre les domaines d'une forêt.

**Relais NTLM** (`source3/winbindd/winbindd_util.c`,
`source4/auth/ntlm/auth_sam.c`, `libcli/auth/smbencrypt.c`). Sur un DC,
winbindd ajoute désormais les domaines de sa forêt
(`NETR_TRUST_FLAG_IN_FOREST`) et les joint par leur compte de trust. La
méthode d'authentification SAM confie les utilisateurs d'un autre
domaine de la forêt à winbindd, qui les envoie à un DC de ce domaine
par Netlogon. Côté réception, la validation des noms de domaine NTLMv2
du relais (MS-NRPC 3.5.4.5.1.1) accepte un canal sécurisé venant d'un
domaine de la forêt quand le client visait ce domaine (ou un autre
domaine de la forêt), et le refuse quand il visait notre propre domaine.

**Recherches LSA** (`source4/rpc_server/lsa/lsa_lookup.c`,
`source3/winbindd/winbindd_msrpc.c`, `winbindd_util.c`). Les noms et SID
des autres domaines de la forêt passent par winbindd, comme ceux des
forêts approuvées. Sur un DC, winbindd interroge le DC du domaine qui les
détient avec `LsapLookupGC` (niveau 4,
`LSA_LOOKUP_NAMES_UPLEVEL_TRUSTS_ONLY`). winbindd route un espace de
noms vers le domaine qui porte exactement ce nom avant de regarder les
noms de premier niveau de la forêt. Un UPN n'est plus décomposé par la
vue BUILTIN : pour un autre domaine cela échouait (nous ne détenons pas
son NC) et faisait échouer toute la recherche.

**Groupes de la racine** (`python/samba/join.py`). Windows référence
directement les groupes du domaine racine (phantoms). Samba ne sait pas
stocker un lien vers un objet d'un NC qu'il ne détient pas : le join
crée donc des objets `foreignSecurityPrincipal` pour Enterprise Admins
et Schema Admins de la racine et les ajoute aux groupes du domaine
enfant. Le jeton d'un membre d'Enterprise Admins obtient les groupes du
domaine enfant à travers eux, comme pour les membres d'une forêt
approuvée.

**Réplication entre domaines** (`source4/dsdb/common/util.c`,
`source4/dsdb/repl/`). Le `serverReference` de l'objet serveur d'un DC
d'un autre domaine, et le `msDS-HasDomainNCs` de son objet nTDSDSA,
pointent vers son NC de domaine, que nous ne détenons pas. Ces liens
étaient abandonnés pendant la réplication (on supposait « même NC »
quand le NC de la cible était introuvable) ; ils sont maintenant gardés
comme liens entre NC. Le serveur DRS accepte un DC d'un autre domaine de
la forêt (`DsReplicaSync`, `DsReplicaUpdateRefs`) quand son objet
nTDSDSA héberge le domaine du SID de l'appelant (`msDS-HasDomainNCs`),
puisqu'il ne peut pas suivre son `serverReference`. Le service de
réplication recharge ses partitions quand le KCC en a ajouté une.

**Catalogue global** (`python/samba/kcc/`,
`source4/dsdb/samdb/ldb_modules/repl_meta_data.c`). Le KCC d'un GC
ajoute désormais à `hasPartialReplicaNCs` la réplique partielle de
chaque autre NC de domaine de la forêt, et charge depuis leur crossRef
les NC qu'il ne détient pas. La réplication de l'ensemble partiel
d'attributs existait déjà. La tête de NC provisoire créée avec le
crossRef d'un nouveau domaine (`instanceType` 11 : tête de NC, non
instanciée, NC au-dessus) est maintenant remplacée par la première
réplication du NC : ses métadonnées locales l'emportaient, et elle
restait non instanciée pour toujours.

**dbcheck** (`python/samba/dbchecker.py`). La tête du NC d'un domaine
enfant n'a pas de parent sur ses propres DC, et le conteneur Deleted
Objects d'une réplique partielle n'a que les attributs de l'ensemble
partiel. Ce ne sont plus des erreurs.


Limites connues
---------------

- **Pas de phantoms :** Samba ne sait pas stocker une référence vers un
  objet d'un NC qu'il ne détient pas. Les groupes de la racine sont
  référencés dans le domaine enfant par des objets
  `foreignSecurityPrincipal` (Windows utilise des phantoms). L'ajout d'un
  utilisateur ou d'un groupe d'un domaine dans un groupe d'un autre
  domaine de la forêt, les groupes universels des autres domaines dans
  les jetons (via le GC) et le rôle de maître d'infrastructure ne sont ni
  faits ni testés.
- **Backlinks sur un catalogue global :** un lien répliqué avant la
  réplique partielle de sa cible (par exemple le `serverReference` des
  DC du domaine enfant, dans le NC Configuration) n'a pas de backlink
  sur la copie de la cible dans la réplique partielle. `samba-tool
  dbcheck` le signale.
- **Netlogon :** `DsrEnumerateDomainTrusts` renvoie toujours
  `parent_index = 0`.
- **DNS :** la partition ForestDnsZones n'est pas répliquée sur le DC
  enfant.
- Le **filtrage de SID** à l'intérieur de la forêt n'a pas été revu.
- Seuls une racine et un domaine enfant, avec un DC chacun, ont été
  testés : pas de petit-enfant ni de racine d'arborescence (referrals à
  travers plus d'un trust), pas de second DC ni de RODC dans le domaine
  enfant, pas de modification du schéma répliquée de la racine vers
  l'enfant.
- Testé uniquement avec des DC Samba des deux côtés. Un domaine enfant
  Samba sous une racine Windows, et l'inverse, ne sont pas testés.
- **Catalogue global d'un domaine enfant :** un GC n'obtient que la
  réplique partielle des domaines créés après son arrivée (la tête de NC
  non instanciée arrive avec le crossRef du nouveau domaine). Un GC d'un
  domaine enfant ne contient pas encore le domaine racine.
- **Vérification du KCC :** `samba.tests.kcc` sur `promoted_dc` (un
  troisième DC du domaine parent) échoue quand la forêt a un domaine
  enfant : vu de ce DC, le graphe des objets de connexion du site n'est
  pas connexe tant que le KCC des autres DC n'est pas passé. Il échouait
  de la même façon (et aussi sur `vampire_dc`) avec le code de la
  phase 1.
- `subdom_dc` n'est pas encore dans `script/autobuild.py`.


Annonce d'Active Directory Web Services (ADWS)
----------------------------------------------

Le service ADWS de HardenSamba
([HardenSamba/samba-adws](https://github.com/HardenSamba/samba-adws))
répond sur le port TCP 9389, mais un client Windows ne l'utilise que si
le contrôleur de domaine annonce qu'il dispose d'ADWS. Cette annonce est
faite par Samba lui-même ; cette branche la porte donc directement dans
le code source. Elle était livrée sous forme de patch dans samba-adws
(`patches/samba/0001-advertise-adws-in-the-cldap-netlogon-response.patch`,
branche `fix/get-forwards-visibility-controls`, commit `33e56b9`) ; ce
patch n'est plus nécessaire.

Fonctionnement :

- Un client qui cherche un contrôleur ADWS (le Centre d'administration
  Active Directory, ou le module PowerShell ActiveDirectory utilisé sans
  `-Server`) appelle `DsGetDcName` avec `DS_WEB_SERVICE_REQUIRED`
  (`0x00100000`, envoyé par le client).
- Le localisateur ne garde que les contrôleurs dont la réponse netlogon
  CLDAP porte `DS_SERVER_WEBSERV` (`0x00002000`, aussi nommé
  `NBT_SERVER_ADS_WEB_SERVICE`) dans `server_type`. Samba ne le
  positionnait jamais.
- `fill_netlogon_samlogon_response()`
  (`source4/dsdb/samdb/ldb_modules/netlogon.c`) le positionne désormais
  quand `smb.conf` le demande. Cette fonction construit la réponse du
  ping CLDAP (UDP 389) et de l'attribut `netlogon` du rootDSE
  (`rootdse.c`), du mailslot NetBIOS (`nbt_server`) et du serveur RPC
  Netlogon.

À activer uniquement sur un DC où le service ADWS tourne sur le port 9389 :

```ini
[global]
    adws:advertise = yes
```

L'annonce est **désactivée par défaut** : un contrôleur qui annonce ADWS
sans rien qui écoute sur 9389 est pire qu'un contrôleur silencieux, car
le client s'y connecte et échoue au lieu de chercher ailleurs. C'est un
paramètre générique (paramétrique) : aucune régénération de l'IDL, de la
table des paramètres ou de la documentation n'est nécessaire.
Redémarrer samba après l'avoir modifié.

Vérification, sur le DC (le `server_type` de sa propre réponse netlogon) :

| `adws:advertise` | `server_type` | `DS_SERVER_WEBSERV` (0x2000) |
|---|---|---|
| absent ou `no` | `0x000013fd` | absent |
| `yes` | `0x000033fd` | présent |

(valeurs mesurées sur cette branche avec un domaine fraîchement
provisionné ; ce sont les mêmes que celles mesurées avec le patch
samba-adws sur Samba 4.17 et 4.22)

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

Et depuis un client Windows joint au domaine (`/force` contourne le
cache du localisateur) :

```
nltest /dsgetdc:<REALM> /ws /force
```

qui renvoie le contrôleur avec `WS` dans ses flags, au lieu de
`ERROR_NO_SUCH_DOMAIN` (1355) quand l'annonce est désactivée.


Modifications en détail
-----------------------

### Phase 1

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
| 12 | `s4:dsdb:netlogon: advertise Active Directory Web Services` | `source4/dsdb/samdb/ldb_modules/netlogon.c` | Positionne `DS_SERVER_WEBSERV` dans le `server_type` de la réponse netlogon quand `smb.conf` contient `adws:advertise = yes` (désactivé par défaut), pour que les clients Windows trouvent le service ADWS de HardenSamba via le localisateur de DC. Repris du patch de samba-adws, voir [Annonce ADWS](#annonce-dactive-directory-web-services-adws). Indépendant du travail multi-domaines : aucun fichier modifié par les commits 1 à 11 n'est touché. |

### Phase 2

| # | Commit | Fichiers | Quoi et pourquoi |
|---|--------|----------|------------------|
| 13 | `s4:dsdb:util_trusts: read the parent crossRefs with their extended DN` | `source4/dsdb/common/util_trusts.c` | `dsdb_trust_crossref_tdo_info()` prend le GUID et le SID des domaines racine et parent dans le nCName de leur crossRef, mais les lisait sans `DSDB_SEARCH_SHOW_EXTENDED_DN`. La table de routage ne pouvait pas être chargée sur un DC enfant, et le KDC échouait avec `EINVAL` pour toute demande inter-realm. |
| 14 | `s4:dsdb:util_trusts: prefer the direct trusts of the forest` | `source4/dsdb/common/util_trusts.c` | Les informations de forêt de notre propre domaine listent les noms de premier niveau de toute la forêt : `dsdb_trust_routing_by_name()`, `dsdb_trust_domain_by_name()` et `dsdb_trust_domain_by_sid()` renvoyaient notre propre domaine pour les noms et SID des autres domaines. Une correspondance exacte avec le nom ou le SID d'un trust `WITHIN_FOREST` passe maintenant en premier. |
| 15 | `s4:kdc: support WITHIN_FOREST trusts` | `source4/kdc/db-glue.c`, `source4/kdc/pac-glue.c` | Le KDC refusait les clés inter-realm des trusts internes à la forêt et ne renvoyait jamais un client vers un autre domaine de la forêt. Les clés viennent maintenant du trust comme pour les autres trusts uplevel ; un principal d'un autre domaine reçoit un referral, sauf si le principal de service existe localement (espace de noms partagé de la forêt). Les claims ne sont pas effacés à l'intérieur de la forêt. |
| 16 | `s4:dsdb: a link to a NC we don't hold is not in the same NC` | `source4/dsdb/common/util.c` | `dsdb_objects_have_same_nc()` répondait vrai quand le NC de la cible était introuvable : les liens répliqués vers le NC de domaine d'un autre domaine (`serverReference`, `msDS-HasDomainNCs`) étaient abandonnés comme « inconnus mais à jour ». |
| 17 | `s4:dsdb: accept the DSA of a DC of another domain of the forest` | `source4/dsdb/common/util.c` | `dsdb_validate_dsa_guid()` suit le `serverReference` de l'objet serveur pour vérifier l'appelant de `DsReplicaSync` / `DsReplicaUpdateRefs` ; pour un DC d'un autre domaine, il pointe vers un NC que nous ne détenons pas. Un tel DSA est maintenant accepté quand le NC de domaine qu'il héberge (`msDS-HasDomainNCs`) est le domaine du SID de l'appelant, et n'est pas le nôtre. |
| 18 | `samba-tool domain join: add the forest root groups to the new child domain` | `python/samba/join.py` | `join_add_forest_root_memberships()` : objets `foreignSecurityPrincipal` pour Enterprise Admins et Schema Admins de la racine ; Enterprise Admins dans `BUILTIN\Administrators`, les deux dans le groupe Denied RODC Password Replication, comme dans un domaine enfant Windows. |
| 19 | `s3:winbindd: support the domains of our forest on a DC` | `source3/winbindd/winbindd_util.c`, `source3/winbindd/winbindd_msrpc.c` | Les trusts `WITHIN_FOREST` sont ajoutés avec `NETR_TRUST_FLAG_IN_FOREST` au lieu d'être ignorés. Un DC recherche les noms et SID des autres domaines de sa forêt avec `LsapLookupGC` (niveau 4). `find_routing_from_namespace_noinit()` compare les noms exacts des domaines avant les noms de premier niveau des informations de forêt. |
| 20 | `s4:auth:ntlm: hand the users of the other domains of the forest to winbindd` | `source4/auth/ntlm/auth_sam.c` | `authsam_want_check()` prenait en charge tous les utilisateurs de la forêt, puis ne trouvait pas ceux des autres domaines dans la SAM locale. Ils sont maintenant laissés à la méthode d'authentification winbind (relais NTLM). |
| 21 | `libcli:auth: validate the NTLMv2 domain names over WITHIN_FOREST trusts` | `libcli/auth/smbencrypt.c` | `NTLMv2_RESPONSE_verify_trust()` renvoyait `NT_STATUS_NOT_SUPPORTED` pour les trusts internes à la forêt. Nouvelle fonction `NTLMv2_RESPONSE_verify_within_forest()` : l'échange NTLMv2 doit viser le domaine approuvé ou un autre domaine de la forêt, jamais notre propre domaine (MS-NRPC 3.5.4.5.1.1). |
| 22 | `s4:rpc_server:lsa: look up the names and SIDs of the other domains of the forest` | `source4/rpc_server/lsa/lsa_lookup.c` | La vue account ne répond que pour notre propre domaine ; les noms et SID des autres domaines de la forêt passent par la vue winbind (winbindd), qui ignorait tout domaine `WITHIN_FOREST`. La vue BUILTIN ne décompose plus les UPN, ce qui échouait pour les UPN d'un autre domaine et faisait échouer la recherche. |
| 23 | `python:kcc: a global catalog holds a partial replica of every domain` | `python/samba/kcc/__init__.py`, `python/samba/kcc/kcc_utils.py` | Nouvelle fonction `refresh_gc_partial_replicas()` : sur un GC, ajoute à `hasPartialReplicaNCs` les NC de domaine de la forêt qui y manquent et dont nous avons la tête de NC (non instanciée), créée avec le crossRef d'un nouveau domaine ; un échec n'interrompt plus l'exécution du KCC. `Partition.should_be_present()` veut aussi les répliques partielles listées dans `hasPartialReplicaNCs`. Les NC que nous ne détenons pas (DomainDnsZones d'un autre domaine...) sont chargés depuis leur crossRef. Le graphe intra-site d'un NC ne reprend que les connexions existantes venant des DC qui le détiennent : dans un site multi-domaines, les connexions venant des DC d'un autre domaine faisaient échouer la vérification du graphe du KCC. |
| 24 | `s4:dsdb:repl: load the partitions added by the KCC` | `source4/dsdb/repl/drepl_service.c`, `source4/dsdb/repl/drepl_periodic.c` | Le service de réplication ne rafraîchissait que les partitions qu'il connaissait : une réplique partielle ajoutée par le KCC n'était jamais répliquée, et `DsReplicaSync` échouait dessus avec `WERR_DS_DRA_BAD_NC`. Le passage périodique et `DsReplicaSync` rechargent maintenant les partitions. |
| 25 | `s4:dsdb:repl_meta_data: instantiate the NC head of a new partial replica` | `source4/dsdb/samdb/ldb_modules/repl_meta_data.c` | La tête de NC provisoire d'une réplique partielle (`instanceType` 11, non instanciée) gardait sa valeur après la première réplication, car ses métadonnées locales étaient plus récentes. La tête de NC répliquée la remplace maintenant toujours (`instanceType` sans `INSTANCE_TYPE_UNINSTANT`, `INSTANCE_TYPE_NC_ABOVE` calculé localement). |
| 26 | `python:dbcheck: child domain NC heads and partial replicas are fine` | `python/samba/dbchecker.py` | Plus de « parent object not found » pour une tête de NC (la tête du NC d'un domaine enfant n'a pas de parent sur ses DC) ; le conteneur Deleted Objects d'une réplique partielle n'est pas vérifié (il n'a que l'ensemble partiel d'attributs). |
| 27 | `selftest: replicate subdom_dc with its parent, test the cross-domain features` | `selftest/target/Samba4.pm`, `python/samba/tests/subdomain.py` | `setup_subdom_dc()` réplique le NC Configuration dans les deux sens. 8 nouveaux tests dans `samba.tests.subdomain` (Kerberos, NTLM, groupes et jeton de la racine, LSA, réplication, KCC, catalogue global). |


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

`samba.tests.subdomain` (`python/samba/tests/subdomain.py`) compte 14
tests : les 6 de la phase 1 (état laissé par le join sur les deux DC) et
8 pour la phase 2 :

| Test | Vérifie |
|------|---------|
| `test_kerberos_cross_domain` | binds LDAP en Kerberos des deux Administrators sur les deux DC ; chacun est membre des Domain Users de son propre domaine |
| `test_ntlm_cross_domain` | la même chose en NTLM |
| `test_forest_root_memberships` | Enterprise Admins de la racine dans `BUILTIN\Administrators` de l'enfant, Enterprise Admins et Schema Admins dans son groupe Denied RODC Password Replication |
| `test_forest_root_admin_token` | sur le DC enfant, le jeton de l'Administrator de la racine contient Enterprise Admins et `BUILTIN\Administrators` (Kerberos et NTLM) |
| `test_lsa_lookups_cross_domain` | sur les deux DC, `LookupNames` de `DOMAINE\Administrator`, `Administrator@realm` et `DOMAINE\`, et `LookupSids` de Domain Admins, pour les deux domaines |
| `test_replication_cross_domain` | `DsReplicaSync` du NC Configuration dans les deux sens |
| `test_kcc_cross_domain` | le KCC s'exécute sur les deux DC, et chacun a une connexion venant du DC de l'autre domaine |
| `test_global_catalog` | le KCC du GC parent ajoute la réplique partielle du domaine enfant, qui se réplique, est instanciée et peut être interrogée sur le port 3268 |

L'environnement `subdom_dc` réplique maintenant le NC Configuration
dans les deux sens à son démarrage, comme les autres environnements de
DC.

Résultats sur cette branche :

- `samba.tests.subdomain` : 14/14 OK.
- Aucune régression dans : `samba4.blackbox.trust_ntlm` (trust de forêt
  `fl2008r2dc`, trust externe `fl2003dc`),
  `samba4.blackbox.trust_utils(fl2008r2dc)`, `samba3.wbinfo_simple.trust`
  (les 4 variantes sur `fl2008r2dc`), `samba.tests.dcerpc.lsa(ad_dc_default)`,
  `samba4.rpc.lsalookup` (ncacn_np, seal, `ad_dc_ntvfs`),
  `samba3.wbinfo_name_lookup(ad_member)`,
  `samba.tests.krb5.kdc_tgs_tests(ad_dc)`,
  `samba4.drs.replica_sync(vampire_dc)`, `samba4.drs.repl_rodc(ad_dc)`,
  `samba4.blackbox.dbcheck` (`alpha13`, `release-4-1-6-partial-object`,
  `release-4-5-0-pre1`, chacun avec `.quick`), `samba4.blackbox.dbcheck-links`,
  `samba.tests.kcc.graph`, `.graph_utils`, `.ldif_import_export`,
  `.kcc_utils(ad_dc_default)`, et `samba.tests.kcc` sur `vampire_dc` et
  `promoted_dc` (voir [Limites connues](#limites-connues) pour
  `promoted_dc` quand la forêt a un domaine enfant).
- `samba4.blackbox.kinit_trust` (`fl2008r2dc`, `fl2003dc`) : tout est
  OK sauf « check time with kerberos ccache », qui échoue dans cet
  environnement de compilation parce que `samba-tool` y est lancé avec
  un interpréteur Python sans module `ldb` ; cela ne dépend pas de cette
  branche.
- `samba-tool dbcheck --cross-ncs` après les réplications : aucune
  erreur sur le DC enfant ; sur le DC parent, le backlink manquant décrit
  dans [Limites connues](#limites-connues).
- Avec le domaine enfant dans la forêt (`samba.tests.subdomain` lancé
  en premier) : `samba4.blackbox.join_ldapcmp` (5 environnements) et
  `samba4.drs.repl_secdesc`, `samba4.drs.repl_move`,
  `samba4.drs.link_conflicts` sur `vampire_dc` : ALL OK.
- Les autres tests de la phase 1 (provision, dsdb, sddl, descripteurs de
  sécurité, upgradeprovision) n'ont pas été relancés, et la suite de
  tests complète de Samba n'a pas été lancée.


Feuille de route
----------------

1. **Références entre domaines :** phantoms (ou équivalent) pour les
   membres d'un groupe qui appartiennent à un autre domaine, maître
   d'infrastructure, groupes universels des autres domaines dans les
   jetons.
2. **Netlogon :** `parent_index` dans `DsrEnumerateDomainTrusts`, et
   revue des autres appels qui listent les domaines de la forêt.
3. **DNS :** ForestDnsZones sur les DC des domaines enfants.
4. **Forêts plus grandes :** petits-enfants et racines d'arborescence
   (referrals à travers plusieurs trusts), plusieurs DC et RODC par
   domaine, mises à jour du schéma.
5. **Interopérabilité :** domaine enfant Samba sous une racine Windows et
   l'inverse ; `subdom_dc` dans autobuild.


Soumission upstream
-------------------

Les commits suivent les conventions de Samba (une modification par
commit, sujets au format `composant: résumé`). Avant de les envoyer au
projet Samba, chaque commit doit porter une ligne `Signed-off-by:` de
son auteur et passer les relectures décrites dans
[README.contributing](README.contributing).
