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
> a fait fonctionner les deux domaines ensemble : Kerberos et NTLM d'un
> domaine à l'autre, recherches de noms et de SID par LSA, groupes de la
> racine dans le domaine enfant, réplication dans les deux sens et
> catalogue global qui contient le domaine enfant. La phase 3 ajoute les
> membres d'un groupe venant d'un autre domaine, les groupes universels
> des autres domaines dans les jetons, le catalogue global du domaine
> enfant (qui contient désormais le domaine racine), les partitions de
> la forêt sur le DC enfant, les rôles de maître d'infrastructure et de
> maître d'attribution des noms de domaine, la vue de la forêt dans
> Netlogon et le filtrage de SID à l'intérieur de la forêt. Plusieurs
> points manquent encore, voir [Limites connues](#limites-connues).

- Base : `master` de Samba au commit `f6fddd2232c9` (4.26.0pre1, 30/10/2026)
- Branche : `_Harden-Box-Multi-domains-supports-phase3`, au-dessus de
  `_Harden-Box-Multi-domains-supports-phase2` (elle-même au-dessus de
  `_Harden-Box-Multi-domains-supports-phase1`)
- Phase 1 : 11 commits de code pour le multi-domaine, 1 commit qui fait
  annoncer Active Directory Web Services par le DC (voir
  [Annonce ADWS](#annonce-dactive-directory-web-services-adws)) et sa
  documentation
- Phase 2 : 15 commits de code (numérotés de 13 à 27 dans
  [Modifications en détail](#modifications-en-détail)) et leur
  documentation
- Phase 3 : 19 commits de code (numérotés de 28 à 46) et cette
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
- La **phase 2** gère les trusts internes à la forêt (`WITHIN_FOREST`)
  dans le KDC, dans winbindd, dans le relais NTLM et dans LSA, donne aux
  groupes de la racine leur place dans le domaine enfant, fait répliquer
  les DC des deux domaines et permet à un catalogue global de contenir
  l'autre domaine.
- La **phase 3** (cette branche) gère les références entre domaines
  (membres d'un groupe venant d'un autre domaine, backlinks, groupes
  universels dans les jetons, maître d'infrastructure), complète le
  catalogue global d'un domaine enfant et les partitions de la forêt sur
  son DC, met en place les contrôles du maître d'attribution des noms de
  domaine, et revoit Netlogon et le filtrage de SID pour les domaines de
  la forêt.


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
3. Il réplique les NC Schema et Configuration, ainsi que le NC
   ForestDnsZones (phase 3, sauf avec `--dns-backend=NONE`) ; le nouveau
   DC est ajouté aux emplacements de réplique de ForestDnsZones
   (`msDS-NC-Replica-Locations` de son crossRef).
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
lance tout de suite). Si le nouveau DC est catalogue global, son KCC
ajoute les répliques partielles des domaines qui existaient déjà, dont
le domaine racine de la forêt (phase 3).

Le maître d'infrastructure de chaque domaine met à jour, une fois par
heure depuis le KCC, les références de son NC de domaine vers les objets
des autres domaines de la forêt (phase 3). Il peut aussi être lancé à la
main, sur n'importe quel DC en écriture du domaine (`--dry-run` liste
les références sans les modifier) :

```
samba-tool domain infrastructure-update -H /var/lib/samba/private/sam.ldb \
    [--gc=ldap://<catalogue global>:3268] [--dry-run] [--force]
```

`--gc` ne sert que sur un DC qui n'est pas catalogue global et qui ne
trouve pas de GC tout seul ; `--force` le lance sur un DC qui n'est pas
le maître d'infrastructure. L'intervalle de l'exécution par le KCC se
règle avec `kcc:infrastructure update interval = <secondes>` dans
`smb.conf`.


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

Phase 3 :

- **membres venant d'un autre domaine :** un utilisateur du domaine
  enfant est ajouté à un groupe universel ou de domaine local du domaine
  racine (en LDAP : l'utilisateur est trouvé sur le catalogue global, là
  où le bouton « Parcourir » des outils Windows le cherche) ;
  un groupe global le refuse, comme sous Windows. La référence garde le
  GUID et le SID de l'utilisateur, et l'utilisateur obtient le
  `memberOf` du groupe sur les catalogues globaux ;
- **groupes universels dans les jetons :** le jeton de cet utilisateur
  contient le groupe universel du domaine racine sur les DC des deux
  domaines (Kerberos et NTLM), et le groupe de domaine local uniquement
  sur le DC du domaine racine, où il s'applique ;
- **catalogue global d'un domaine enfant :** le KCC d'un GC du domaine
  enfant ajoute la réplique partielle du domaine racine (et de chaque
  domaine qui existait avant lui), qui se réplique et peut être
  interrogée sur le port 3268 : une recherche depuis la racine de la
  forêt sur le DC enfant trouve les objets des deux domaines ;
- **backlinks :** le `serverReferenceBL` du DC enfant est présent sur le
  catalogue global du domaine parent : `samba-tool dbcheck --cross-ncs`
  ne trouve aucune erreur sur les deux DC ;
- **partitions de la forêt :** le DC enfant détient les partitions
  Schema, Configuration et ForestDnsZones, répliquées dans les deux
  sens, et sert la zone `_msdcs` de la forêt ;
- **Netlogon :** `DsrEnumerateDomainTrusts` liste tous les domaines de
  la forêt, avec les drapeaux `TREEROOT`, `PRIMARY`, `IN_FOREST`,
  `NATIVE`, `INBOUND`/`OUTBOUND` et le `parent_index` du domaine enfant
  (l'indice de son parent dans la liste renvoyée) ; `LogonGetDomainInfo`
  donne le nom de la forêt et les mêmes drapeaux ;
- **filtrage de SID dans la forêt :** les SID qu'un domaine de la forêt
  n'a pas le droit d'envoyer (SID bien connus de la machine locale,
  comptes locaux, SID d'autres autorités) sont retirés des PAC et des
  jetons NTLM venant d'un autre domaine de la forêt, comme le décrit
  [MS-PAC] 4.1.2.2 ;
- **maître d'attribution des noms de domaine :** seul le maître
  d'attribution des noms ajoute ou supprime un crossRef ; un crossRef
  doit avoir un `dnsRoot` et un nom NetBIOS uniques ; le crossRef d'un
  domaine qui a encore des DC ne peut pas être supprimé ; le rôle passe
  d'un DC à l'autre entre les deux domaines (`samba-tool fsmo transfer
  --role=naming`) ;
- **maître d'infrastructure :** sur un DC qui n'est pas catalogue
  global, les références vers les objets des autres domaines sont mises
  à jour (cible renommée ou déplacée) ou retirées (cible supprimée) par
  le maître d'infrastructure du domaine, depuis le KCC ou avec
  `samba-tool domain infrastructure-update` ;
- **KCC :** `samba.tests.kcc` passe sur `promoted_dc` et `vampire_dc`
  avec un domaine enfant dans la forêt.


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


Fonctionnement de la phase 3
----------------------------

**Membres venant d'un autre domaine**
(`source4/dsdb/samdb/ldb_modules/samldb.c`). Windows stocke la référence
vers un objet d'un domaine qu'il ne détient pas sous forme de phantom.
Samba n'a pas de phantoms, mais un catalogue global détient une réplique
partielle de chaque domaine de la forêt : un DC qui est GC trouve donc
déjà l'utilisateur d'un autre domaine, et le lien garde son GUID et son
SID (`extended_dn_store`), comme un lien vers un objet local. Il
manquait les règles d'étendue des groupes d'Active Directory : un membre
d'un autre domaine ne peut être ajouté qu'à un groupe universel ou de
domaine local, et ne peut pas être lui-même un groupe de domaine local.
`samldb` refuse désormais les autres cas (`LDB_ERR_UNWILLING_TO_PERFORM`).
Le bouton « Parcourir » des outils Windows utilise le sélecteur d'objets,
qui interroge le catalogue global : les objets des autres domaines y
sont (pas encore testé avec un client Windows).

**Groupes universels dans les jetons**
(`source4/dsdb/common/util_groups.c`). Les groupes d'un utilisateur
viennent des backlinks `memberOf` de son objet, y compris ceux des
groupes d'un autre domaine présents dans une réplique partielle. Seuls
les groupes universels d'un autre domaine s'appliquent en dehors de
celui-ci : ses groupes globaux et de domaine local sont maintenant
ignorés (et pas développés) lors de la construction du jeton. Le DC du
domaine qui détient le groupe ajoute lui-même ses groupes de domaine
local quand l'utilisateur arrive chez lui (referral Kerberos, relais
NTLM).

**Backlinks des répliques partielles**
(`source4/dsdb/samdb/ldb_modules/repl_meta_data.c`). Quand un lien est
répliqué avant sa cible (le `serverReference` d'un DC du domaine enfant
arrive avec le NC Configuration, avant la réplique partielle du domaine
enfant sur le GC du domaine parent), le backlink ne pouvait pas être
ajouté, et ne l'était jamais par la suite. Windows garde un phantom pour
cela. `repl_meta_data` enregistre maintenant ce backlink dans
l'enregistrement `@REPLMD_PENDING_BACKLINKS` (GUID de la cible, attribut
du backlink, DN étendu de la source) et l'ajoute à la fin de la
transaction de réplication qui apporte la cible ; l'enregistrement est
supprimé quand il est vide, et un lien retiré entre-temps retire son
backlink en attente.

**Catalogue global d'un domaine enfant** (`python/samba/kcc/__init__.py`,
`source4/dsdb/common/util.c`, `source4/dsdb/pydsdb.c`, `lib/ldb/ldb_mdb/`,
`source4/dsdb/samdb/ldb_modules/partition_init.c`). La tête de NC (non
instanciée) d'une réplique partielle n'était créée qu'avec le crossRef
d'un nouveau domaine : un GC d'un domaine enfant n'obtenait jamais le
domaine racine, qui existait avant lui. Le KCC crée maintenant la tête
de NC manquante à partir du crossRef (avec le GUID et le SID du NC)
avant d'ajouter la réplique à `hasPartialReplicaNCs`. Comme rien n'est
au-dessus du NC racine sur un DC enfant, `INSTANCE_TYPE_NC_ABOVE` n'est
positionné que si nous détenons le NC du dessus. La base LMDB d'une
partition créée à chaud utilisait la taille de map LMDB par défaut
(1 Mio, `MDB_MAP_FULL` à la réplication du domaine racine) : elle reçoit
maintenant les 8 Gio d'un provisionnement (espace d'adressage réservé
seulement), et `lmdb_connect()` lit la taille de map dans les options de
la connexion (celles de la partition) avant celles de l'ldb. Le KCC ne
construit l'anneau intra-site d'un NC qu'avec ses répliques complètes en
écriture : la réplique partielle d'un GC d'un autre domaine n'est pas
une source.

**Vérification du KCC** (`python/samba/kcc/__init__.py`). `samba_kcc
--verify` (`samba.tests.kcc`) échouait sur `vampire_dc` et
`promoted_dc` dès que la forêt avait un domaine enfant, selon le
moment, pour deux raisons. Une fois que le KCC d'un DC du domaine parent
(ils sont tous catalogues globaux) a ajouté la réplique partielle du
domaine enfant, le graphe du NC du domaine enfant construit sur ce DC
contient la réplique complète du DC enfant et les répliques partielles
des GC, qui ne font que recevoir des arêtes : il est connexe, mais ce
n'est pas l'anneau double qu'exigeait la vérification. L'anneau double
n'est maintenant vérifié que si notre propre réplique est complète. Et
avec quatre DC dans un site (trois DC du domaine parent et le DC
enfant), le graphe des connexions vu par un DC n'est pas connexe tant
que le KCC de tous les autres DC n'est pas passé et que leurs objets de
connexion ne sont pas répliqués : les DSA qui n'ont encore aucune
connexion (dans un sens ou dans l'autre) sont écartés de la vérification
quand le site a plus de trois DC.

**Partitions de la forêt sur un DC enfant** (`python/samba/join.py`). Le
join d'un domaine enfant réplique maintenant la partition ForestDnsZones
avec les NC Schema et Configuration (elle contient la zone `_msdcs` de
la forêt), la liste dans le `msDS-hasMasterNCs` du nouveau DC et ajoute
le nouveau DC au `msDS-NC-Replica-Locations` de son crossRef, pour que
le KCC des autres DC la réplique avec le nouveau DC.

**Netlogon** (`source4/rpc_server/netlogon/dcerpc_netlogon.c`).
`DsrEnumerateDomainTrusts` ne listait que notre propre domaine et les
objets trustedDomain, avec un `parent_index` toujours à 0. Il liste
maintenant tous les domaines de la forêt, à partir des crossRefs du
conteneur Partitions, fusionnés avec les objets trustedDomain de nos
trusts directs : `IN_FOREST` et `NATIVE` pour tous, `TREEROOT` pour un
domaine sans `trustParent`, `PRIMARY` pour notre domaine,
`INBOUND`/`OUTBOUND` pour les trusts directs, et `parent_index` égal à
l'indice du domaine parent dans la liste renvoyée ([MS-NRPC] 2.2.1.6.2).
Les entrées sont filtrées selon les drapeaux demandés.
`LogonGetDomainInfo` reçoit le nom DNS de la forêt, les mêmes drapeaux
et le même indice de parent.

**Filtrage de SID dans la forêt** (`source4/dsdb/common/util_trusts.c`,
`source4/kdc/pac-glue.c`, `source4/auth/ntlm/auth_winbind.c`). Nouvelle
fonction `dsdb_trust_filter_within_forest_sids()`, appliquée par le KDC
au PAC d'un ticket émis par un autre domaine de la forêt, et à la
réponse du relais NTLM pour un utilisateur d'un autre domaine de la
forêt, selon [MS-PAC] 4.1.2.2 : avec un trust `WITHIN_FOREST`, les SID
de la catégorie « AlwaysFilter » sont retirés (autorités Null, World,
Local, Creator, SID bien connus et BUILTIN `S-1-5-x`, SID `S-1-5-21`
partiels...) ; avec un trust en quarantaine
(`TRUST_ATTRIBUTE_QUARANTINED_DOMAIN`), seuls passent les SID du domaine
approuvé, Enterprise Domain Controllers et les SID « NeverFilter »
(`S-1-5-15`, `S-1-5-1000-*`, `S-1-16-*`, `S-1-18-*`...). Si le SID de
l'utilisateur ou de son groupe principal devait être retiré,
l'authentification est refusée. Les trusts externes et de forêt gardent
le filtrage qu'ils avaient.

**Maître d'attribution des noms de domaine**
(`source4/dsdb/samdb/ldb_modules/samldb.c`). `samldb` vérifie maintenant,
à l'ajout et à la suppression d'un crossRef (hors provisionnement, et
sauf avec le contrôle relax), que ce DC est le maître d'attribution des
noms (`fSMORoleOwner` du conteneur Partitions, sinon
`WERR_DS_INCORRECT_ROLE_OWNER`) ; qu'un nouveau crossRef de domaine a un
`dnsRoot` et un nom NetBIOS ; qu'aucun autre crossRef n'a le même
`dnsRoot` ou le même nom NetBIOS (`WERR_DS_CROSS_REF_EXISTS`) ; et qu'un
crossRef de domaine supprimé n'a plus de DC (`msDS-HasDomainNCs` d'un
objet nTDSDSA, `WERR_DS_NC_STILL_HAS_DSAS`). Le join d'un domaine enfant
passait déjà par le maître d'attribution des noms avec `DsAddEntry` ; le
transfert du rôle (`becomeDomainMaster`) existait déjà.

**Maître d'infrastructure** (`python/samba/infrastructure.py`,
`python/samba/netcmd/domain/infrastructure.py`, `python/samba/kcc/`).
Sur un GC, les références vers les objets des autres domaines sont
tenues à jour par la réplication de leur réplique partielle (Samba met
déjà à jour ou retire les liens d'un objet renommé ou supprimé). Un DC
qui n'est pas GC n'a rien pour les suivre. Le maître d'infrastructure du
domaine (`fSMORoleOwner` de `CN=Infrastructure`) cherche maintenant,
dans le catalogue global, la cible de chaque lien avant de son NC de
domaine qui pointe vers un autre domaine de la forêt (par GUID, objets
supprimés compris), et remplace la valeur quand la cible a été renommée
ou déplacée, ou la retire quand la cible a été supprimée. La
modification est une écriture d'origine, qui se réplique sur les autres
DC du domaine. Le KCC la lance sur le maître d'infrastructure une fois
par heure (quand la forêt a d'autres domaines), et `samba-tool domain
infrastructure-update` la lance à la main.

**dbcheck** (`python/samba/dbchecker.py`). Sur un GC d'un domaine
enfant, la tête du NC de son domaine est sous la réplique partielle du
domaine racine, arrivée après elle : que `INSTANCE_TYPE_NC_ABOVE` soit
positionné ou non n'est pas une erreur.


Limites connues
---------------

- **Pas de phantoms :** Samba ne sait toujours pas stocker une référence
  vers un objet qu'il ne détient pas. Un membre d'un autre domaine ne
  peut être ajouté que sur un DC qui détient sa réplique partielle,
  c'est-à-dire un catalogue global (Windows crée un phantom sur
  n'importe quel DC) ; sur un autre DC, l'objet est introuvable. Les
  groupes Enterprise Admins et Schema Admins de la racine sont toujours
  référencés dans les groupes d'un domaine enfant par des objets
  `foreignSecurityPrincipal` (phase 2), car le join crée le domaine
  enfant avant qu'il ne détienne le domaine racine.
- **Groupes universels :** les groupes universels des autres domaines ne
  sont dans les jetons que s'ils sont construits par un DC qui détient
  leur réplique partielle (un catalogue global). Un DC qui n'est pas GC
  ne les demande pas à un GC à l'ouverture de session, et la mise en
  cache de l'appartenance aux groupes universels de Windows (pour les
  sites sans GC) n'est pas implémentée.
- **Backlinks en attente :** les backlinks qui attendent leur cible sont
  gardés dans l'enregistrement local `@REPLMD_PENDING_BACKLINKS` de
  chaque DC ; `dbcheck` ne le regarde pas, et un backlink dont la cible
  n'arrive jamais y reste.
- **Maître d'infrastructure :** écrit en Python et lancé par le KCC (au
  plus une fois par heure par défaut), pas par le service de
  réplication. Sur un DC qui n'est pas GC, il faut un autre GC joignable
  en LDAP (port 3268). Seuls les liens avant du NC de domaine sont
  vérifiés ; il ne fait rien sur un GC, déjà à jour (comme sous Windows,
  le maître d'infrastructure ne devrait pas être un GC, sauf si tous les
  DC du domaine le sont).
- **Filtrage de SID :** seul le filtrage interne à la forêt a été
  ajouté. Un trust `WITHIN_FOREST` n'est mis en quarantaine que si
  `TRUST_ATTRIBUTE_QUARANTINED_DOMAIN` est positionné à la main sur son
  objet trustedDomain. L'historique de SID des utilisateurs des autres
  domaines de la forêt est conservé, comme sous Windows.
- **Netlogon :** `DsrEnumerateDomainTrusts` et `LogonGetDomainInfo` ont
  été revus ; les autres appels qui décrivent la forêt
  (`DsrGetForestTrustInformation`, `DsrGetDcName` pour un autre domaine
  de la forêt...) ne l'ont pas été.
- **Maître d'attribution des noms de domaine :** en LDAP, le `nCName`
  d'un nouveau crossRef doit déjà exister (c'est vérifié plus tôt, par
  `extended_dn_store`) : les partitions d'application ne se créent pas
  par un crossRef ajouté d'abord sur le maître d'attribution des noms.
  Les contrôles sont ignorés pendant un provisionnement et avec le
  contrôle relax.
- **Vérification du KCC :** quand un site a plus de trois DC,
  `samba_kcc --verify` écarte de la vérification du graphe les DSA qui
  n'ont encore aucune connexion, et seule la connexité du graphe d'un NC
  dont nous détenons une réplique partielle est vérifiée ; cela ne
  change que la vérification, pas la topologie construite par le KCC.
- Seuls une racine et un domaine enfant, avec un DC chacun, ont été
  testés : pas de petit-enfant ni de racine d'arborescence (referrals à
  travers plus d'un trust), pas de second DC ni de RODC dans le domaine
  enfant, pas de modification du schéma répliquée de la racine vers
  l'enfant.
- Testé uniquement avec des DC Samba des deux côtés. Un domaine enfant
  Samba sous une racine Windows, et l'inverse, ne sont pas testés, pas
  plus que les outils d'administration Windows (sélecteur d'objets,
  Utilisateurs et ordinateurs Active Directory) face à un domaine enfant
  Samba.
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


### Phase 3

| # | Commit | Fichiers | Quoi et pourquoi |
|---|--------|----------|------------------|
| 28 | `ldb:mdb: the options of the connection set the LMDB map size` | `lib/ldb/ldb_mdb/ldb_mdb.c` | `lmdb_connect()` ne lisait `lmdb_env_size` que dans les options de l'ldb, jamais dans celles passées à la connexion : le backend d'une partition ne pouvait pas avoir sa propre taille de map. Les options de la connexion passent maintenant en premier. |
| 29 | `s4:dsdb:partition: a full LMDB map for the partitions created at runtime` | `source4/dsdb/samdb/ldb_modules/partition_init.c` | Une partition créée à chaud (une réplique partielle ajoutée par le KCC) recevait la taille de map LMDB par défaut, 1 Mio, et sa réplication échouait avec `MDB_MAP_FULL`. Elle reçoit maintenant les 8 Gio d'un provisionnement (espace d'adressage réservé), sauf si ses options fixent `lmdb_env_size`. |
| 30 | `s4:dsdb: no NC_ABOVE on a partial replica with no NC above it` | `source4/dsdb/common/util.c` | `dsdb_create_partial_replica_NC()` positionnait toujours `INSTANCE_TYPE_NC_ABOVE`. La réplique partielle du domaine racine sur un GC d'un domaine enfant n'a rien au-dessus d'elle : le drapeau n'est maintenant positionné que si nous détenons le NC du dessus. |
| 31 | `pydsdb: add _dsdb_create_partial_replica_NC()` | `source4/dsdb/pydsdb.c` | Liaison Python de `dsdb_create_partial_replica_NC()`, pour le KCC. |
| 32 | `python:kcc: a global catalog of a child domain holds the forest root domain` | `python/samba/kcc/__init__.py` | `refresh_gc_partial_replicas()` crée la tête de NC manquante d'une réplique partielle à partir du crossRef du domaine (GUID et SID), au lieu d'ignorer les domaines qui existaient avant le GC (le domaine racine, vu d'un domaine enfant) ; un échec est journalisé et le NC ignoré. L'anneau intra-site d'un NC n'utilise que ses répliques complètes en écriture, pas la réplique partielle d'un GC d'un autre domaine. |
| 33 | `python:kcc: fix the graph verification in a multi-domain forest` | `python/samba/kcc/__init__.py` | Quand notre réplique d'un NC est partielle, seule la connexité du graphe en écriture est vérifiée (les répliques partielles ne font que recevoir des arêtes, ce n'est pas un anneau double) ; avec plus de trois DC dans le site, les DSA qui n'ont encore aucune connexion (les résultats de leur KCC ne nous sont pas encore parvenus) sont écartés des vérifications du graphe de `--verify`. `samba.tests.kcc` échouait sur `vampire_dc` et `promoted_dc` dès que leur KCC avait ajouté la réplique partielle du domaine enfant. |
| 34 | `s4:dsdb:repl_meta_data: add the backlinks of links replicated before their target` | `source4/dsdb/samdb/ldb_modules/repl_meta_data.c` | Un lien répliqué dont la cible n'est pas encore là (le `serverReference` d'un DC enfant sur le GC du domaine parent) n'avait jamais de backlink. Le backlink est maintenant noté dans `@REPLMD_PENDING_BACKLINKS` et ajouté quand la cible est répliquée (à la fin de cette transaction) ; `serverReferenceBL` était la seule erreur signalée par `dbcheck`. |
| 35 | `samba-tool domain join: replicate ForestDnsZones to the DC of a new child domain` | `python/samba/join.py` | La partition ForestDnsZones appartient à toute la forêt : le join d'un domaine enfant la réplique, la liste dans `msDS-hasMasterNCs` et ajoute le nouveau DC au `msDS-NC-Replica-Locations` de son crossRef. |
| 36 | `s4:dsdb:samldb: the group scope rules for the members of another domain` | `source4/dsdb/samdb/ldb_modules/samldb.c` | Un membre d'un autre domaine de la forêt (trouvé dans sa réplique partielle sur un GC) ne peut être ajouté qu'à un groupe universel ou de domaine local, et ne peut pas être lui-même un groupe de domaine local (`LDB_ERR_UNWILLING_TO_PERFORM`). |
| 37 | `s4:dsdb:util_groups: only the universal groups of the other domains` | `source4/dsdb/common/util_groups.c` | `dsdb_expand_nested_groups()` ignore les groupes globaux et de domaine local d'un autre domaine (venant de `memberOf` sur un GC) : seuls ses groupes universels s'appliquent hors de celui-ci. Sans cela, le jeton d'un utilisateur du domaine enfant recevait les groupes de domaine local du domaine racine sur le DC enfant. |
| 38 | `s4:dsdb:util_trusts: SID filtering of the intra-forest trusts` | `source4/dsdb/common/util_trusts.c`, `source4/dsdb/pydsdb.c`, `python/samba/tests/dsdb_trust_sid_filter.py` (nouveau), `selftest/tests.py` | Nouvelle fonction `dsdb_trust_filter_within_forest_sids()` ([MS-PAC] 4.1.2.2, `WithinForest` et `QuarantinedWithinForest`), sa liaison Python `_dsdb_trust_filter_within_forest_sids()` et 5 tests unitaires. |
| 39 | `s4:kdc: filter the SIDs of a PAC issued by another domain of the forest` | `source4/kdc/pac-glue.c` | Le KDC applique le filtrage de SID interne à la forêt au PAC d'un ticket émis par un autre domaine de la forêt (`KRB5KDC_ERR_POLICY` si le SID de l'utilisateur ou de son groupe principal est filtré). |
| 40 | `s4:auth:ntlm: filter the SIDs of the users of the other domains of the forest` | `source4/auth/ntlm/auth_winbind.c` | Le même filtrage pour la réponse du relais NTLM (winbindd) pour un utilisateur d'un autre domaine de la forêt. |
| 41 | `s4:rpc_server:netlogon: list the domains of the forest with their parent` | `source4/rpc_server/netlogon/dcerpc_netlogon.c` | `DsrEnumerateDomainTrusts` liste tous les domaines de la forêt (crossRefs) fusionnés avec nos trusts directs, avec les drapeaux `IN_FOREST`, `NATIVE`, `TREEROOT`, `PRIMARY`, `INBOUND`/`OUTBOUND` et `parent_index` (toujours à 0 auparavant) ; `LogonGetDomainInfo` reçoit le nom de la forêt et les mêmes drapeaux. |
| 42 | `s4:dsdb:samldb: crossRefs are added and deleted on the domain naming master` | `source4/dsdb/samdb/ldb_modules/samldb.c` | Ajout et suppression d'un crossRef uniquement sur le maître d'attribution des noms (`WERR_DS_INCORRECT_ROLE_OWNER`) ; `dnsRoot` et nom NetBIOS obligatoires pour un crossRef de domaine et uniques dans la forêt (`WERR_DS_CROSS_REF_EXISTS`) ; un crossRef de domaine ne peut pas être supprimé tant qu'un DC héberge le domaine (`WERR_DS_NC_STILL_HAS_DSAS`). |
| 43 | `python: add the infrastructure master update` | `python/samba/infrastructure.py` (nouveau), `python/samba/netcmd/domain/infrastructure.py` (nouveau), `python/samba/netcmd/domain/__init__.py`, `docs-xml/manpages/samba-tool.8.xml` | `InfrastructureUpdate` cherche dans un GC les cibles des liens de notre NC de domaine vers les autres domaines de la forêt, et les met à jour (renommage, déplacement) ou les retire (suppression). `samba-tool domain infrastructure-update` la lance (`--dry-run`, `--gc`, `--force`), documentée dans la page de manuel de samba-tool. |
| 44 | `python:kcc: run the infrastructure master update` | `python/samba/kcc/__init__.py` | Sur le maître d'infrastructure d'un domaine d'une forêt multi-domaines, le KCC lance la mise à jour au plus toutes les `kcc:infrastructure update interval` secondes (3600 par défaut), avec un GC trouvé dans la forêt si ce DC n'en est pas un. |
| 45 | `python:dbcheck: NC_ABOVE below a partial replica is fine` | `python/samba/dbchecker.py` | Sur un GC d'un domaine enfant, la tête du NC de son domaine est sous la réplique partielle du domaine racine, arrivée après elle : `INSTANCE_TYPE_NC_ABOVE` positionné ou non n'est pas une erreur. |
| 46 | `selftest: test the phase 3 multi-domain features` | `python/samba/tests/subdomain.py` | 8 nouveaux tests dans `samba.tests.subdomain`, voir [Tests](#tests). |

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

`samba.tests.subdomain` (`python/samba/tests/subdomain.py`) compte 22
tests : les 6 de la phase 1 (état laissé par le join sur les deux DC),
8 pour la phase 2 et 8 pour la phase 3.

Phase 2 :

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

Phase 3 :

| Test | Vérifie |
|------|---------|
| `test_forest_partitions_on_child` | le DC enfant détient les NC Schema, Configuration et ForestDnsZones (`namingContexts`, `msDS-hasMasterNCs`, emplacements de réplique du crossRef de ForestDnsZones), sert la zone `_msdcs` de la forêt, et ForestDnsZones se réplique dans les deux sens |
| `test_child_global_catalog` | le KCC du DC enfant (un GC) ajoute la réplique partielle du domaine racine (`instanceType` 1 : tête de NC, rien au-dessus), et une recherche sur son port 3268 trouve l'Administrator des deux domaines |
| `test_partial_replica_backlinks` | sur le GC du domaine parent, l'objet ordinateur du DC enfant a son `serverReferenceBL` |
| `test_cross_domain_membership` | un utilisateur du domaine enfant dans un groupe universel et un groupe de domaine local du domaine racine (la référence porte son SID), refusé dans un groupe global ; l'utilisateur a le `memberOf` du groupe universel sur le GC |
| `test_universal_groups_in_tokens` | le jeton de cet utilisateur, en Kerberos et en NTLM, contient le groupe universel sur les deux DC, et le groupe de domaine local uniquement sur le DC du domaine racine |
| `test_netlogon_domain_trusts` | `DsrEnumerateDomainTrusts` sur les deux DC : les deux domaines, `IN_FOREST`, `NATIVE`, `TREEROOT` (racine seulement), `PRIMARY` (propre domaine seulement), `INBOUND`/`OUTBOUND`, SID, et `parent_index` du domaine enfant pointant vers la racine |
| `test_domain_naming_master` | le crossRef du domaine enfant ne peut pas être supprimé, le DC enfant (pas maître d'attribution des noms) refuse un nouveau crossRef même à un Enterprise Admin, le maître refuse un `dnsRoot` en double, le rôle passe au DC enfant puis revient |
| `test_infrastructure_update` | sur le DC parent, le maître d'infrastructure ne trouve rien à mettre à jour (c'est un GC) ; vu à travers un catalogue global où l'utilisateur du domaine enfant a été renommé, puis supprimé, les références des deux groupes sont mises à jour, puis retirées |

`samba.tests.dsdb_trust_sid_filter` (`python/samba/tests/dsdb_trust_sid_filter.py`,
5 tests, sans environnement) vérifie le filtrage de SID des trusts
internes à la forêt : ce qui passe un trust `WITHIN_FOREST` et un trust
en quarantaine, les SID d'utilisateur et de groupe principal qui font
échouer l'authentification, et que les autres trusts ne sont pas
touchés.

L'environnement `subdom_dc` réplique le NC Configuration dans les deux
sens à son démarrage, comme les autres environnements de DC.

Résultats sur cette branche (phase 3) :

- `samba.tests.subdomain` : 22/22 OK ; `samba.tests.dsdb_trust_sid_filter` :
  5/5 OK.
- `samba-tool dbcheck --cross-ncs` après les réplications et les
  passages du KCC : aucune erreur sur les deux DC.
- Aucune régression dans : `samba.tests.provision`,
  `samba.tests.dsdb(ad_dc_default)`, `samba4.blackbox.dbcheck`
  (`alpha13`, `release-4-1-6-partial-object`, `release-4-5-0-pre1`,
  chacun avec `.quick`), `samba4.blackbox.dbcheck-links`,
  `samba.tests.kcc.graph`, `.graph_utils`, `.ldif_import_export`,
  `samba3.wbinfo_name_lookup(ad_member)`, `samba4.rpc.lsalookup`
  (ncacn_np, seal, `ad_dc_ntvfs`), `samba4.rpc.netlogon` et
  `samba4.rpc.netlogon.admin` (ncacn_ip_tcp, seal, `ad_dc_ntvfs`),
  `samba4.rpc.netlogon` (seal, `ad_dc`), `samba4.blackbox.trust_ntlm`
  (trust de forêt `fl2008r2dc`, trust externe `fl2003dc`),
  `samba4.blackbox.trust_utils(fl2008r2dc)`, `samba3.wbinfo_simple.trust`
  (les 4 variantes sur `fl2008r2dc`), `samba.tests.dcerpc.lsa(ad_dc_default)`.
- Avec le domaine enfant dans la forêt (`samba.tests.subdomain` lancé
  en premier) : `samba4.urgent_replication(ad_dc)`,
  `samba4.blackbox.join_ldapcmp` (5 environnements),
  `samba4.drs.replica_sync`, `samba4.drs.repl_move` et
  `samba4.drs.link_conflicts` sur `vampire_dc`,
  `samba4.drs.repl_rodc(ad_dc)`, `samba.tests.krb5.kdc_tgs_tests(ad_dc)`,
  `samba.tests.kcc.kcc_utils(ad_dc_default)`, et `samba.tests.kcc` sur
  `vampire_dc` et `promoted_dc`, y compris après que leur KCC a ajouté
  la réplique partielle du domaine enfant (il échouait à chaque fois dans
  ce cas avant le commit 33) : ALL OK.
- `samba4.blackbox.kinit_trust(fl2008r2dc)` : tout est OK sauf « check
  time with kerberos ccache », qui échoue dans cet environnement de
  compilation parce que `samba-tool` y est lancé avec un interpréteur
  Python sans module `ldb` ; cela ne dépend pas de cette branche.
- Les autres tests de la phase 1 (sddl, descripteurs de sécurité,
  upgradeprovision) n'ont pas été relancés, et la suite de tests
  complète de Samba n'a pas été lancée.


Feuille de route
----------------

Fait en phase 3 : membres venant d'un autre domaine (sur un GC), groupes
universels des autres domaines dans les jetons, maître
d'infrastructure, maître d'attribution des noms de domaine, catalogue
global d'un domaine enfant, ForestDnsZones sur les DC enfants,
`parent_index` et drapeaux de la forêt dans Netlogon, filtrage de SID
dans la forêt, backlink manquant et vérification du KCC.

1. **Références sans GC :** phantoms (ou équivalent) pour que n'importe
   quel DC puisse stocker un membre d'un autre domaine ; recherche des
   groupes universels auprès d'un GC à l'ouverture de session sur les DC
   qui ne sont pas GC (et sa mise en cache) ; références directes à
   Enterprise Admins et Schema Admins au lieu des objets
   `foreignSecurityPrincipal`.
2. **Netlogon :** revue des autres appels qui décrivent la forêt
   (`DsrGetForestTrustInformation`, `DsrGetDcName` pour un autre
   domaine).
3. **Forêts plus grandes :** petits-enfants et racines d'arborescence
   (referrals à travers plusieurs trusts), plusieurs DC et RODC par
   domaine, mises à jour du schéma, suppression d'un domaine enfant
   (commande `samba-tool domain` pour supprimer son crossRef sur le
   maître d'attribution des noms une fois son dernier DC parti).
4. **Interopérabilité :** domaine enfant Samba sous une racine Windows et
   l'inverse, outils d'administration Windows face à un domaine enfant
   Samba ; `subdom_dc` dans autobuild.


Soumission upstream
-------------------

Les commits suivent les conventions de Samba (une modification par
commit, sujets au format `composant: résumé`). Avant de les envoyer au
projet Samba, chaque commit doit porter une ligne `Signed-off-by:` de
son auteur et passer les relectures décrites dans
[README.contributing](README.contributing).
