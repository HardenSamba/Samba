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
> Netlogon et le filtrage de SID à l'intérieur de la forêt. La phase 4
> fait de chaque fonctionnalité une option de `smb.conf`, désactivée par
> défaut (sans aucune d'elles, le DC se comporte comme un DC de la
> branche master de Samba), désactive le rôle de maître d'infrastructure
> sur un catalogue global comme Windows, et ajoute au serveur DNS interne
> les redirecteurs conditionnels et les zones de stub, le choix de la
> partition d'une nouvelle zone DNS, la corbeille AD telle que Windows la
> gère et l'ordre alphabétique des objets dans Utilisateurs et
> ordinateurs Active Directory. Plusieurs points manquent encore, voir
> [Limites connues](#limites-connues).

- Base : `master` de Samba au commit `f6fddd2232c9` (4.26.0pre1, 30/10/2026)
- Branche : `_Harden-Box-Multi-domains-supports-phase4`, au-dessus de
  `_Harden-Box-Multi-domains-supports-phase3` (elle-même au-dessus de
  `_Harden-Box-Multi-domains-supports-phase2` et
  `_Harden-Box-Multi-domains-supports-phase1`)
- Phase 1 : 11 commits de code pour le multi-domaine, 1 commit qui fait
  annoncer Active Directory Web Services par le DC (voir
  [Annonce ADWS](#annonce-dactive-directory-web-services-adws)) et sa
  documentation
- Phase 2 : 15 commits de code (numérotés de 13 à 27 dans
  [Modifications en détail](#modifications-en-détail)) et leur
  documentation
- Phase 3 : 19 commits de code (numérotés de 28 à 46) et leur
  documentation
- Phase 4 : 14 commits de code (numérotés de 47 à 60) et cette
  documentation ; la restauration des objets supprimés depuis le Centre
  d'administration Active Directory demande aussi la branche
  `fix/restore-deleted-objects` de samba-adws (voir
  [Corbeille AD](#corbeille-ad))


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
- La **phase 3** gère les références entre domaines (membres d'un
  groupe venant d'un autre domaine, backlinks, groupes universels dans
  les jetons, maître d'infrastructure), complète le catalogue global
  d'un domaine enfant et les partitions de la forêt sur son DC, met en
  place les contrôles du maître d'attribution des noms de domaine, et
  revoit Netlogon et le filtrage de SID pour les domaines de la forêt.
- La **phase 4** (cette branche) fait de chaque fonctionnalité une option
  de `smb.conf` (voir [Configuration](#configuration)), désactive le rôle
  de maître d'infrastructure sur un catalogue global, et ajoute des
  fonctionnalités qui ne sont pas propres aux forêts multi-domaines :
  redirecteurs conditionnels et zones de stub dans le serveur DNS
  interne, gérés depuis la console DNS d'une machine Windows, partition
  d'une nouvelle zone DNS, corbeille AD, et ordre des objets dans
  Utilisateurs et ordinateurs Active Directory.


Configuration
-------------

Chaque fonctionnalité des branches Harden-Box s'active par une option
paramétrique de la section `[global]` de `smb.conf`, **désactivée par
défaut**. Sans aucune d'elles, le DC se comporte comme un DC de la
branche master de Samba : chaque commit de la phase 4 dit ce que son
option change, et la partie [Tests](#tests) liste les suites de
non-régression lancées avec les options désactivées.

| Option | Défaut | Active |
|---|---|---|
| `harden:multi-domain forest` | `no` | la prise en charge des forêts multi-domaines des phases 1 à 3 : domaines enfants, trusts `WITHIN_FOREST` dans le KDC, winbindd, NTLM, LSA et Netlogon, répliques partielles des autres domaines sur les catalogues globaux, membres venant d'un autre domaine, mise à jour du maître d'infrastructure... À mettre sur **tous les DC de la forêt**, y compris ceux du domaine racine. |
| `harden:recycle bin` | `no` | la corbeille AD telle que Windows la gère, une fois activée dans la forêt, voir [Corbeille AD](#corbeille-ad) |
| `harden:sort one-level results` | `no` | le tri par nom des recherches LDAP à un niveau, voir [Ordre des objets dans ADUC](#ordre-des-objets-dans-aduc) |
| `harden:dns conditional forwarders` | `no` | les redirecteurs conditionnels du serveur DNS interne, voir [DNS](#dns--redirecteurs-conditionnels-zones-de-stub-et-partitions-des-zones) |
| `harden:dns stub zones` | `no` | les zones de stub du serveur DNS interne |
| `harden:dns default zone partition` | non défini | la partition d'une nouvelle zone DNS quand le client n'en indique pas : `domain` (DomainDnsZones, comme sur la branche master), `forest` (ForestDnsZones) ou `legacy` (`CN=MicrosoftDNS,CN=System` de la partition de domaine) ; toute valeur fait aussi gérer la partition `legacy` par le serveur RPC DNS |
| `adws:advertise` | `no` | l'indicateur ADWS de la réponse netlogon (phase 1), voir [Annonce ADWS](#annonce-dactive-directory-web-services-adws) |
| `kcc:infrastructure update interval` | `3600` | secondes entre deux mises à jour du maître d'infrastructure par le KCC (phase 3) |

Par exemple, sur un DC d'une forêt multi-domaines qui les utilise
toutes :

```ini
[global]
    harden:multi-domain forest = yes
    harden:recycle bin = yes
    harden:sort one-level results = yes
    harden:dns conditional forwarders = yes
    harden:dns stub zones = yes
    harden:dns default zone partition = forest
```

Redémarrer samba après une modification. Ce sont des options génériques
(paramétriques) : `testparm` les affiche telles qu'elles sont écrites, et
elles ne demandent aucune modification des tables de paramètres. Les
options DNS concernent le serveur DNS interne
(`--dns-backend=SAMBA_INTERNAL`), pas BIND9 DLZ.


Utilisation
-----------

Sur le serveur qui sera le premier DC du nouveau domaine enfant :

```
samba-tool domain join enfant.example.com SUBDOMAIN \
    --parent-domain=example.com \
    --server=dc1.example.com \
    --option="workgroup=ENFANT" \
    --option="harden:multi-domain forest=yes" \
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
- Les DC du domaine parent doivent eux aussi tourner avec cette branche,
  avec `harden:multi-domain forest = yes` dans leur `smb.conf` (les
  redémarrer après l'ajout) : les modifications du KDC, de winbindd, de
  LSA, de la réplication et du KCC sont nécessaires des deux côtés.
- Le join refuse de se lancer sans `harden:multi-domain forest = yes`
  (phase 4). Donnée par `--option` (sans espace autour du `=`), l'option
  est écrite dans le `smb.conf` du nouveau DC.

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

`--gc` ne sert que quand le DC ne trouve pas de catalogue global tout
seul ; `--force` le lance sur un DC qui n'est pas le maître
d'infrastructure. L'intervalle de l'exécution par le KCC se règle avec
`kcc:infrastructure update interval = <secondes>` dans `smb.conf`.

Comme sous Windows, le rôle de maître d'infrastructure ne fait rien sur
un DC catalogue global (phase 4) : le KCC et `samba-tool domain
infrastructure-update` indiquent pourquoi dans le journal et s'arrêtent.
Quand certains DC du domaine ne sont pas catalogue global, le message
les nomme et demande de transférer le rôle à l'un d'eux
(`samba-tool fsmo transfer --role=infrastructure` sur ce DC) ; quand tous
les DC du domaine sont catalogue global, le rôle ne sert pas.


DNS : redirecteurs conditionnels, zones de stub et partitions des zones
-----------------------------------------------------------------------

Avec `harden:dns conditional forwarders = yes` et
`harden:dns stub zones = yes` (serveur DNS interne), la console DNS d'une
machine Windows (`dnsmgmt.msc`, connectée au DC) crée, modifie et
supprime :

- des **redirecteurs conditionnels** (*Redirecteurs conditionnels >
  Nouveau redirecteur conditionnel...*) : le domaine DNS, les adresses IP
  de ses serveurs maîtres, le délai d'attente des requêtes redirigées, et
  *Stocker ce redirecteur conditionnel dans Active Directory et le
  répliquer comme suit* ;
- des **zones de stub** (*Nouvelle zone... > Zone de stub*) : la zone,
  ses serveurs maîtres, et si et où elle est stockée dans Active
  Directory.

Stockés dans Active Directory, ils sont répliqués sur tous les DC qui
ont leur partition, comme les zones principales :

| Choix de la console | Partition |
|---|---|
| Vers tous les serveurs DNS exécutés sur des contrôleurs de domaine dans cette forêt | ForestDnsZones |
| Vers tous les serveurs DNS exécutés sur des contrôleurs de domaine dans ce domaine | DomainDnsZones du domaine |
| Vers tous les contrôleurs de domaine dans ce domaine (pour la compatibilité Windows 2000) | `CN=MicrosoftDNS,CN=System` de la partition de domaine (la partition « legacy ») |

Non stockés dans Active Directory, ils n'existent que sur le DC qui les
a créés (dans `dns_local_zones.ldb`, dans le répertoire d'état ; sous
Windows, un fichier dans `%windir%\System32\dns`) ; seul un
administrateur les crée, les modifie ou les supprime.

Le serveur DNS envoie une requête pour un nom d'un redirecteur
conditionnel à ses serveurs maîtres, puis, sauf si le redirecteur
interdit la récursivité, aux redirecteurs globaux (`dns forwarder`) ;
une requête pour un nom d'une zone de stub ne va qu'à ses serveurs
maîtres. Une zone plus précise du DC l'emporte.

La même chose avec `samba-tool` :

```
samba-tool dns zonecreate dc1 example.org --type=forwarder \
    --master=192.0.2.53 --master=2001:db8::53 --forwarder-timeout=3 \
    --dns-directory-partition=forest -UAdministrator
samba-tool dns zonecreate dc1 partenaire.test --type=stub \
    --master=192.0.2.10 --dns-directory-partition=legacy -UAdministrator
samba-tool dns zonecreate dc1 labo.test --type=forwarder \
    --master=192.0.2.99 --not-ds-integrated -UAdministrator
samba-tool dns zonelist dc1 --forwarder -UAdministrator
samba-tool dns zonedelete dc1 example.org -UAdministrator
```

**Partition d'une nouvelle zone.** Avec
`harden:dns default zone partition = domain|forest|legacy`, une zone
créée sans partition (`samba-tool dns zonecreate` sans
`--dns-directory-partition`, `dnscmd /ZoneAdd ... /DsPrimary` sans
`/DP`) va dans cette partition, zones principales comprises. Sans
l'option, elle va dans DomainDnsZones, comme sur la branche master.

Mettre les options DNS sur **tous les DC qui ont la partition** de ces
zones (tous les DC de la forêt pour ForestDnsZones) : un DC sans elles
charge un redirecteur conditionnel ou une zone de stub comme une zone
principale vide, et répond que ses noms n'existent pas.


Corbeille AD
------------

La corbeille est une fonctionnalité optionnelle de la forêt. Samba en
avait déjà l'essentiel, mais un DC ne la voyait activée que si son
propre objet nTDSDSA l'indiquait, la restauration d'un objet supprimé
échouait une fois la corbeille activée (`noSuchAttribute`), et un
administrateur ne pouvait pas l'activer. Avec `harden:recycle bin = yes`,
sur tous les DC de la forêt, un DC se comporte comme un DC Windows
([MS-ADTS] 3.1.1.5.5, 3.1.1.5.3.7 et 3.1.1.9.1).

**Activation** (niveau fonctionnel de forêt 2008 R2 ou plus, par un
Enterprise Admin ; elle ne peut pas être désactivée). Depuis une machine
Windows, avec le service ADWS de HardenSamba (branche
`fix/restore-deleted-objects` de samba-adws) : l'action *Activer la
Corbeille...* du Centre d'administration Active Directory, ou

```
Enable-ADOptionalFeature 'Recycle Bin Feature' -Scope ForestOrConfigurationSet -Target example.com
```

Ou sur un DC, en root :

```
ldbmodify -H /var/lib/samba/private/sam.ldb <<'END'
dn:
changetype: modify
add: enableOptionalFeature
enableOptionalFeature: CN=Partitions,CN=Configuration,DC=example,DC=com:766ddcd8-acd0-445e-f3b9-a7f9b6744f2a
END
```

Une fois activée :

| État | `isDeleted` | `isRecycled` | Attributs et liens | Restauration |
|---|---|---|---|---|
| objet supprimé (pendant `msDS-DeletedObjectLifetime`, sinon la durée de vie des tombstones) | `TRUE` | absent | conservés ; les liens sont désactivés (masqués sans le contrôle `LDAP_SERVER_SHOW_DEACTIVATED_LINK`) | oui, avec ses attributs et ses liens |
| objet recyclé (effacé après la durée de vie des tombstones) | `TRUE` | `TRUE` | supprimés, comme pour un tombstone | non (`unwillingToPerform`) |
| tombstone créé avant l'activation de la corbeille | `TRUE` | `TRUE` (positionné à l'activation) | supprimés | non |

Supprimer un objet supprimé (vider la corbeille) le recycle. Une
restauration est une seule modification de l'objet supprimé, comme sous
Windows : suppression de `isDeleted` et remplacement de
`distinguishedName`, avec le contrôle `show_deleted` :

```
ldbmodify -H ldap://dc1 -UAdministrator --controls=show_deleted:1 <<'END'
dn: <GUID=5e3ea2a3-0b5c-4c4f-a3a6-7b0e0a29a6b2>
changetype: modify
delete: isDeleted
-
replace: distinguishedName
distinguishedName: CN=Jeanne Dupont,OU=Personnel,DC=example,DC=com
END
```

Le nouveau parent doit exister (sinon `noSuchObject`,
`ERROR_DS_NO_PARENT_OBJECT` : restaurer d'abord le parent supprimé).
`Restore-ADObject` et les actions *Restaurer* / *Restaurer dans...* du
Centre d'administration Active Directory fonctionnent avec le service
ADWS de HardenSamba, branche `fix/restore-deleted-objects`, qui les
traduit en cette modification (le Centre d'administration affiche les
objets supprimés depuis la branche
`fix/get-forwards-visibility-controls`).


Ordre des objets dans ADUC
--------------------------

Utilisateurs et ordinateurs Active Directory affiche les objets d'un
conteneur dans l'ordre où le DC les renvoie. Un DC Windows répond à une
recherche à un niveau par son index sur le parent et le nom : les objets
reviennent par ordre alphabétique ; Samba les renvoyait dans l'ordre de
son propre index (par GUID). Avec `harden:sort one-level results = yes`,
les recherches à un niveau qui viennent de LDAP et ne demandent pas
elles-mêmes d'ordre (tri côté serveur, VLV) sont triées par nom (sans
tenir compte de la casse), pages d'une recherche paginée comprises.
L'ordre de Windows est déduit de son comportement, pas d'une
spécification.


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


Phase 4 :

- **options :** sans aucune option `harden:`, le DC se comporte comme sur
  la branche master (les suites de non-régression listées dans
  [Tests](#tests) passent sur les environnements selftest qui ne les
  activent pas) ; `samba-tool domain join ... SUBDOMAIN` refuse de se
  lancer sans `harden:multi-domain forest` ;
- **maître d'infrastructure sur un GC :** le rôle ne fait rien sur un
  catalogue global, et indique quels DC du domaine pourraient le tenir ;
- **DNS** (DC enfant, serveur DNS interne) : un redirecteur conditionnel
  créé dans DomainDnsZones avec un maître sur le DC parent résout les
  noms de la zone du parent par lui, est listé et décrit comme un
  redirecteur (maîtres avec IPv6, délai, récursivité), refuse les
  enregistrements, est modifié (maîtres, délai, récursivité) et
  supprimé ; de même pour un redirecteur conditionnel stocké sur le DC
  seulement et pour une zone de stub dans ForestDnsZones ; une zone
  créée sans partition va dans la partition de
  `harden:dns default zone partition`, et une zone peut être créée dans
  la partition legacy ; le DC parent, sans les options, refuse les
  redirecteurs conditionnels et les zones de stub ;
- **corbeille AD :** activée dans la forêt par l'Administrator (un
  Enterprise Admin), anciens tombstones recyclés, objets supprimés qui
  gardent leurs attributs, restauration avec les attributs et les liens,
  restauration sans aucun contrôle par un client qui envoie la simple
  modification, suppression / restauration / nouvelle suppression,
  recyclage d'un objet supprimé, restauration refusée pour un objet
  recyclé et pour un parent absent, liens désactivés masqués sauf
  demande (sur un domaine provisionné par le test) ; restauration par le
  code du service ADWS de HardenSamba (samba-adws
  `tests/dc/test_undelete_request.py`, les trois formes de la requête de
  restauration) ;
- **ordre dans ADUC :** les enfants d'une OU reviennent triés par nom
  d'une recherche LDAP à un niveau, aussi page par page, et un tri
  explicite décide toujours.


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



Fonctionnement de la phase 4
----------------------------

**Options** (`source4/dsdb/common/util.c`, `python/samba/harden.py`).
`dsdb_lp_multi_domain_forest()` et `dsdb_multi_domain_forest()` (via
l'opaque `loadparm` de la ldb) lisent `harden:multi-domain forest` ;
chaque modification des phases 1 à 3 qui change le comportement d'un DC
d'une forêt à un seul domaine en dépend (dsdb, réplication, KDC, NTLM,
LSA, Netlogon, winbindd avec `lp_parm_bool()`, KCC et samba-tool avec
`samba.harden`). Le code des phases 1 à 3 qui ne sert qu'à un domaine
enfant (le join SUBDOMAIN, la mise à jour du maître d'infrastructure)
refuse de se lancer sans elle. Les autres options sont lues là où elles
servent : `dsdb_harden_recycle_bin()`, `dns_harden_get_options()`,
l'initialisation du module `onelevel_order`.

**Maître d'infrastructure** (`python/samba/infrastructure.py`). Un GC a
une réplique partielle de chaque domaine : ses références vers les
objets des autres domaines sont tenues à jour par leur réplication. Sous
Windows, le maître d'infrastructure d'un domaine ne doit pas être un GC,
sauf si tous les DC du domaine en sont (il comparerait ses références à
ses propres répliques partielles, toujours à jour, et ne trouverait rien
à mettre à jour pour les autres DC). `run()` s'arrête désormais tout de
suite sur un GC, et `gc_disabled_reason()` liste les objets nTDSDSA du
domaine (`msDS-HasDomainNCs`) qui ne sont pas GC (`options` sans
`NTDSDSA_OPT_IS_GC`). Les liens de syntaxe DN-Binary et DN-String sont
lus avec `dsdb_dn_guess()`.

**Serveur DNS** (`source4/dns_server/dns_harden.c`, `dns_query.c`,
`dns_harden_common.c`). Au (re)chargement des zones, celles dont le
`DSPROPERTY_ZONE_TYPE` est redirecteur (4) ou stub (3) passent dans une
liste de zones de redirection avec leurs maîtres
(`DSPROPERTY_ZONE_MASTER_SERVERS_DA`, sinon
`DSPROPERTY_ZONE_MASTER_SERVERS`), leur délai et leur réglage de
récursivité (propriétés privées `0xFF01` et `0xFF02`), et les zones de
`dns_local_zones.ldb` y sont ajoutées. Avant la recherche dans les zones
faisant autorité, une requête est comparée aux zones de redirection
(suffixe le plus long, sauf si une zone faisant autorité plus précise
existe) et redirigée avec le code des redirecteurs globaux, aux maîtres
d'abord, chacun avec le délai de la zone. Le module `dns_notify` fait
recharger ses zones au serveur DNS quand une zone d'une partition DNS
change ; sur un DC enfant, il surveille désormais la partition
DomainDnsZones de son propre domaine.

**Serveur RPC DNS** (`source4/rpc_server/dnsserver/dnsharden.c`). Les
partitions du serveur RPC reçoivent la partition legacy
(`CN=MicrosoftDNS,CN=System,<domaine>`, `DNS_DP_LEGACY`) et le stockage
local (non intégré à AD). `ZoneCreate` avec un type de zone redirecteur
ou stub (ou une zone principale avec les options) est traité ici : la
partition vient des indicateurs `DCPROMO`, de `pszDpFqdn` ou de
`dwDpFlags`, sinon de `harden:dns default zone partition` ; l'objet de
zone et ses valeurs `dNSProperty` (type, maîtres en tableaux IP4 et
DNS_ADDR, délai, récursivité) sont écrits dans une seule transaction.
`ZoneInfo` prend les maîtres, le délai et la récursivité dans les
propriétés ; `EnumZones` applique le filtre de la requête par groupes
(type de zone, intégration à AD, partition) ; `MasterServers`,
`ResetDwordProperty` et `DeleteZone(FromDs)` sont traités pour ces
zones, et le serveur DNS est prévenu de recharger ses zones (IRPC
`dnssrv_reload_dns_zones`).

**Corbeille** (`source4/dsdb/samdb/ldb_modules/`). `rootdse.c` vérifie
le niveau fonctionnel de la forêt, la portée et le droit
Manage-Optional-Features (`7c0e2a7c-a419-48e4-a995-10180aad54dd`) de
l'appelant, écrit `msDS-EnabledFeature` en tant que système et recycle
les tombstones existants. `util.c` voit la fonctionnalité activée quand
le conteneur Partitions l'indique. `tombstone_reanimate.c` restaure un
objet supprimé sans retirer un `isRecycled` qu'il n'a pas et sans
écraser ses attributs, et refuse les objets recyclés et les parents
absents. `objectclass.c` transforme la suppression d'un objet supprimé
en recyclage. `garbage_collect_tombstones.c` recycle les objets
supprimés après la durée de vie des objets supprimés et efface les
objets recyclés après celle des tombstones. `extended_dn_out.c` masque
les liens désactivés.

**Ordre à un niveau** (`source4/dsdb/samdb/ldb_modules/onelevel_order.c`).
Le module est dans la pile après `server_sort`, sous `paged_results`.
Pour une recherche à un niveau non fiable (LDAP) sans contrôle de tri ni
VLV, il garde les entrées, les trie par valeur du RDN
(`strcasecmp_m()`, puis `strcmp()`) et les envoie avant la réponse de
fin ; les referrals et les contrôles passent sans changement.


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
- **Options (phase 4) :** elles sont lues au démarrage de samba (le
  redémarrer après une modification). `harden:multi-domain forest` doit
  être mise sur tous les DC d'une forêt multi-domaines : un DC sans elle
  se comporte comme un DC de la branche master et refuse les trusts
  internes à la forêt. De même pour les options DNS sur les DC qui ont
  les partitions des redirecteurs conditionnels et des zones de stub, et
  pour `harden:recycle bin` une fois la corbeille activée (un DC sans
  elle ne sait pas restaurer un objet supprimé).
- **Zones de stub :** elles sont résolues en redirigeant les requêtes
  vers leurs serveurs maîtres. Les enregistrements SOA, NS et glue ne
  sont pas transférés et rafraîchis comme sous Windows, et la console
  n'y montre aucun enregistrement.
- **Console DNS :** le changement de type d'une zone (`ZoneTypeReset`)
  ou de son étendue de réplication (`ZoneChangeDirectoryPartition`)
  n'est pas implémenté (supprimer la zone et la recréer), et
  `EnumDirectoryPartitions` ne liste toujours que DomainDnsZones et
  ForestDnsZones. Les serveurs maîtres IPv6 ne sont montrés qu'aux
  clients qui utilisent les tableaux `DNS_ADDR` (version LONGHORN, la
  console des versions actuelles de Windows). Les indicateurs envoyés
  par la console (partition, filtres d'`EnumZones`) ont été repris de
  [MS-DNSP] et de `dnscmd`, pas de captures de la console, qui n'a pas
  encore été testée.
- **Stockage DNS :** le délai et le réglage « ne pas utiliser la
  récursivité » d'un redirecteur conditionnel stocké dans AD utilisent
  deux identifiants `dNSProperty` privés (`0xFF01`, `0xFF02`) ; leur
  traitement par un DC Windows n'a pas été testé. Les zones stockées sur
  un seul DC demandent un administrateur du domaine. BIND9 DLZ ne
  connaît pas les redirecteurs conditionnels ni les zones de stub.
- **Corbeille :** les liens désactivés sont masqués dans les résultats
  des recherches LDAP, mais un filtre de recherche sur un attribut lié
  les trouve encore, et la réplication des liens désactivés n'a pas été
  comparée à Windows. La restauration depuis le Centre d'administration
  Active Directory demande le service ADWS de HardenSamba (branche
  `fix/restore-deleted-objects`) ; la forme de la requête de restauration
  envoyée par la console est une déduction (le service accepte les trois
  formes possibles).
- **Ordre des recherches à un niveau :** seules les recherches LDAP à un
  niveau sans contrôle de tri ni VLV sont triées, par valeur du RDN, avec
  la comparaison sans casse de Samba (`strcasecmp_m()`) : l'ordre des
  noms accentués peut différer de celui de Windows. Les entrées de la
  recherche sont gardées en mémoire jusqu'au tri.


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


### Phase 4

| # | Commit | Fichiers | Quoi et pourquoi |
|---|--------|----------|------------------|
| 47 | `s4:dsdb: make the multi-domain forest support an option` | `source4/dsdb/common/util.c`, `util_groups.c`, `source4/dsdb/repl/drepl_periodic.c`, `drepl_service.c`, `source4/dsdb/samdb/ldb_modules/descriptor.c`, `extended_dn_out.c`, `partition_init.c`, `repl_meta_data.c`, `samldb.c`, `selftest/target/Samba4.pm` | Nouvelles `dsdb_lp_multi_domain_forest()` et `dsdb_multi_domain_forest()` (`harden:multi-domain forest`, désactivée par défaut). Sans l'option, le code dsdb des phases 1 à 3 se comporte comme sur la branche master (DSA d'un autre domaine, tête de NC d'une réplique partielle, liens vers des NC non détenus, SID de la racine dans les descripteurs, carte LMDB des partitions créées à chaud, backlinks en attente, contrôles d'étendue de groupe et du maître des noms, groupes des jetons, rechargement des partitions). Les DC de la forêt de `ad_dc_ntvfs` reçoivent l'option en selftest. |
| 48 | `s4: WITHIN_FOREST trusts only with "harden:multi-domain forest"` | `source4/kdc/db-glue.c`, `pac-glue.c`, `source4/auth/ntlm/auth_sam.c`, `auth_winbind.c`, `source4/rpc_server/lsa/dcesrv_lsa.c`, `lsa_lookup.c`, `source4/rpc_server/netlogon/dcerpc_netlogon.c` | Sans l'option : pas de clés inter-royaumes ni de referrals pour un trust `WITHIN_FOREST` dans le KDC, pas de filtrage de SID interne à la forêt, pas de relais NTLM vers les domaines de la forêt, `CreateTrustedDomain*` refuse `WITHIN_FOREST`, la vue BUILTIN traite les UPN, et Netlogon décrit le domaine comme sur master. |
| 49 | `s3:winbindd: the domains of the forest only with "harden:multi-domain forest"` | `source3/winbindd/winbindd_util.c`, `winbindd_msrpc.c` | Sans l'option, les trusts `WITHIN_FOREST` sont ignorés, les espaces de noms routés comme sur master et `LsapLookupGC` n'est pas utilisé. |
| 50 | `python: join and replicate a child domain only with "harden:multi-domain forest"` | `python/samba/harden.py` (nouveau), `python/samba/kcc/__init__.py`, `kcc_utils.py`, `python/samba/netcmd/domain/join.py`, `python/samba/tests/kcc/__init__.py` | `samba.harden` (noms des options, `lp_bool()`, `multi_domain_forest()`). Le join SUBDOMAIN demande l'option ; sans elle, le KCC fonctionne comme sur master. |
| 51 | `python:infrastructure: the role does nothing on a global catalog` | `python/samba/infrastructure.py`, `python/samba/netcmd/domain/infrastructure.py`, `python/samba/kcc/__init__.py`, `docs-xml/manpages/samba-tool.8.xml`, `python/samba/tests/subdomain.py` | Comme sous Windows, la mise à jour du maître d'infrastructure ne fait rien sur un GC et indique quels DC du domaine pourraient tenir le rôle ; `samba-tool domain infrastructure-update` demande l'option multi-domaines ; les liens DN-Binary et DN-String (`msDS-RevealedUsers` d'un RODC) ne la font plus échouer. Nouveau `test_infrastructure_master_on_gc`. |
| 52 | `s4:dsdb: add the onelevel_order module ("harden:sort one-level results")` | `source4/dsdb/samdb/ldb_modules/onelevel_order.c` (nouveau), `wscript_build_server`, `samba_dsdb.c` | Trie par nom les entrées des recherches LDAP à un niveau sans contrôle de tri ni VLV, pour qu'Utilisateurs et ordinateurs Active Directory affiche les objets par ordre alphabétique. |
| 53 | `s4:dsdb: enable the AD Recycle Bin for the forest ("harden:recycle bin")` | `source4/dsdb/common/util.c`, `source4/dsdb/samdb/ldb_modules/util.c`, `rootdse.c` | La corbeille est activée quand le conteneur Partitions l'indique ; `enableOptionalFeature` vérifie le niveau fonctionnel de la forêt, la portée et le droit Manage-Optional-Features de l'appelant, écrit `msDS-EnabledFeature` en tant que système et recycle les tombstones existants. Nouvelles `dsdb_harden_recycle_bin()`, `dsdb_deleted_object_lifetime()`, `dsdb_check_optional_feature_forest()`. |
| 54 | `s4:dsdb: delete, restore and recycle objects as Windows does` | `source4/dsdb/samdb/ldb_modules/objectclass.c`, `tombstone_reanimate.c`, `repl_meta_data.c`, `show_deleted.c`, `extended_dn_out.c`, `source4/dsdb/kcc/garbage_collect_tombstones.c`, `python/samba/tests/recycle_bin.py` (nouveau), `selftest/tests.py` | Restauration sans `noSuchAttribute` et avec les attributs, refusée pour les objets recyclés et les parents absents ; la suppression d'un objet supprimé le recycle ; recyclage après la durée de vie des objets supprimés ; liens désactivés masqués sauf demande ; `msDS-LastKnownRDN` remplacé. 9 tests. |
| 55 | `librpc:dnsp: private dnsProperty ids for the conditional forwarders` | `librpc/idl/dnsp.idl`, `source4/dns_server/dnsserver_common.c` | `DSPROPERTY_ZONE_SAMBA_FORWARDER_TIMEOUT` (`0xFF01`) et `DSPROPERTY_ZONE_SAMBA_FORWARDER_SLAVE` (`0xFF02`), que [MS-DNSP] ne définit pas. |
| 56 | `s4:dns_server: resolve the conditional forwarders and stub zones` | `source4/dns_server/dns_harden.c` (nouveau), `dns_harden_common.c` (nouveau), `dns_harden_common.h` (nouveau), `dns_server.c`, `dns_server.h`, `dns_query.c`, `dnsserver_common.h`, `wscript_build` | `harden:dns conditional forwarders`, `harden:dns stub zones` : les zones redirecteur et stub (AD et stockage local) sont redirigées vers leurs maîtres, puis vers les redirecteurs globaux pour un redirecteur conditionnel qui autorise la récursivité. |
| 57 | `s4:rpc_server:dnsserver: conditional forwarders, stub zones and the legacy partition` | `source4/rpc_server/dnsserver/dnsharden.c` (nouveau), `dcerpc_dnsserver.c`, `dnsdb.c`, `dnsserver.h`, `source4/rpc_server/wscript_build` | Création, description, liste, modification et suppression des redirecteurs conditionnels et des zones de stub depuis la console DNS, dans la partition de forêt, de domaine ou legacy, ou sur le DC seulement ; `harden:dns default zone partition`. |
| 58 | `s4:dsdb:dns_notify: watch the DomainDnsZones of the domain of a child DC` | `source4/dsdb/samdb/ldb_modules/dns_notify.c` | Le serveur DNS d'un DC enfant ne rechargeait pas ses zones quand une zone de son propre DomainDnsZones changeait (celui de la racine était surveillé). |
| 59 | `samba-tool dns: conditional forwarders, stub zones and the zone partitions` | `python/samba/netcmd/dns.py` | `zonecreate --type --master --forwarder-timeout --no-recursion --not-ds-integrated`, `--dns-directory-partition=legacy` et sa valeur par défaut dans `smb.conf` ; `zonelist --forwarder --stub`. |
| 60 | `selftest: test the phase 4 DNS and one-level options on subdom_dc` | `selftest/target/Samba4.pm`, `python/samba/tests/dns_harden.py` (nouveau), `python/samba/tests/onelevel_order.py` (nouveau), `source4/selftest/tests.py` | Les options DNS et de tri sur `subdom_dc`, `samba.tests.dns_harden` (6 tests) et `samba.tests.onelevel_order` (3 tests). |

samba-adws, branche `fix/restore-deleted-objects` (au-dessus de
`fix/get-forwards-visibility-controls`), commit `e4d2182`
(`Restore deleted objects in one undelete modify; enable optional features through the rootDSE`) :
un `Put` qui retire `isDeleted` devient la modification unique de
restauration (`classes/directory/undelete_request.py`), et
`ChangeOptionalFeature` passe par l'opération `enableOptionalFeature`
du rootDSE au lieu d'écrire `msDS-EnabledFeature`, réservé au système ;
tests et documentation mis à jour.

Tests
-----

Compiler avec le selftest activé, puis lancer les tests multi-domaines :

```
./configure --enable-selftest --enable-debug
make -j$(nproc)
make test TESTS="samba.tests.subdomain samba.tests.dns_harden \
    samba.tests.onelevel_order samba.tests.recycle_bin"
```

Pour obtenir un shell dans l'environnement de test :

```
SELFTEST_TESTENV=subdom_dc make testenv
```

`samba.tests.subdomain` (`python/samba/tests/subdomain.py`) compte 23
tests : les 6 de la phase 1 (état laissé par le join sur les deux DC),
8 pour la phase 2, 8 pour la phase 3 et 1 pour la phase 4.

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


Phase 4 :

| Test | Vérifie |
|------|---------|
| `samba.tests.subdomain` : `test_infrastructure_master_on_gc` | sur le DC parent (un GC), la mise à jour du maître d'infrastructure ne fait rien et l'indique dans le journal, en nommant les DC qui ne sont pas GC s'il y en a |
| `samba.tests.dns_harden` (`subdom_dc`) : `test_default_partition` | une zone créée sans partition va dans ForestDnsZones (`harden:dns default zone partition = forest`) |
| `test_legacy_partition` | une zone créée avec `DNS_DP_LEGACY` est dans `CN=MicrosoftDNS,CN=System` de la partition de domaine, et `EnumZones` la liste avec le filtre legacy, pas avec celui de la forêt |
| `test_conditional_forwarder` | un redirecteur conditionnel dans DomainDnsZones, avec le DC parent comme maître : dans la liste des redirecteurs et pas dans celle des zones principales, `ZoneInfo` (type, délai 7, récursivité, maîtres), un nom de la zone du parent résolu par le DC enfant, `ResetDwordProperty ForwarderSlave`, `MasterServers` avec un maître IPv6, enregistrements refusés, suppression |
| `test_local_conditional_forwarder` | un redirecteur conditionnel hors AD : aucun objet dans l'annuaire, listé avec le filtre `NON_DS`, `fUseDatabase` à 0, résolu, supprimé avec `DeleteZone` |
| `test_stub_zone` | une zone de stub dans ForestDnsZones : dans la liste des zones de stub, de type stub, résolue par son maître |
| `test_disabled_on_parent` | le DC parent, sans les options, refuse un redirecteur conditionnel (`WERR_CALL_NOT_IMPLEMENTED`), comme la branche master |
| `samba.tests.onelevel_order` (`subdom_dc`) | les enfants d'une OU (OU et contacts aux noms en casse mélangée, créés dans un ordre aléatoire) reviennent triés par nom, aussi à travers les pages d'une recherche paginée (pages de 7), et un `server_sort` explicite (inverse) décide toujours |
| `samba.tests.recycle_bin` (sans environnement) | sur un domaine provisionné par le test avec `harden:recycle bin` : activation par l'Administrator, ancien tombstone recyclé, utilisateur supprimé qui garde ses attributs et est restauré avec eux (avec et sans contrôle côté client), suppression / restauration / nouvelle suppression, objet supprimé recyclé par une suppression et plus restaurable, parent absent refusé (`2089`), liens désactivés |

Résultats sur cette branche (phase 4) :

- `samba.tests.subdomain` : 23/23 OK ; `samba.tests.dns_harden` : 6/6 OK ;
  `samba.tests.onelevel_order` : 3/3 OK ; `samba.tests.recycle_bin` :
  9/9 OK ; `samba.tests.kcc` sur `vampire_dc` et `promoted_dc`,
  `samba.tests.kcc.graph`, `.graph_utils`, `.ldif_import_export`,
  `.kcc_utils` : ALL OK.
- DNS, avec les options sur `subdom_dc` seulement : `samba.tests.dns`
  sur `fl2003dc`, `rodc` et `vampire_dc`, `samba.tests.dns_aging`,
  `samba.tests.dns_forwarder` (`fl2003dc`), `samba.tests.dns_tkey`
  (`fl2008r2dc`), `samba.tests.dns_wildcard`, `.dns_invalid`,
  `.dns_packet` (`ad_dc`), `samba.tests.dcerpc.dnsserver`
  (`ad_dc_ntvfs`, `ad_dc`), `samba.tests.samba_tool.dnscmd`
  (`chgdcpass`) : ALL OK.
- Options désactivées (le domaine de `ad_dc` et les environnements
  `fl*` n'en activent aucune ; `ad_dc_ntvfs` n'active que
  `harden:multi-domain forest`) : `samba4.tombstone_reanimation.python`,
  `samba4.ldap.vlv.python` (`ad_dc_slowtests`),
  `samba4.ldap.dirsync.python`, `samba4.ldap.linked_attributes.python`
  (`ad_dc`), `samba4.ldap.sort.python`, `samba4.deletetest.python`,
  `samba.tests.dsdb`, `samba.tests.dcerpc.lsa` (`ad_dc_default`),
  `samba4.blackbox.trust_ntlm` (`fl2008r2dc`, `fl2003dc`, `fl2000dc`,
  `ad_member`, `nt4_member`), `samba.tests.samba_tool.help`,
  `samba.tests.source`, `samba.tests.source_chars` : ALL OK (419 tests).
- samba-adws : `tests/dc/test_undelete_request.py` 5/5 OK (sur un domaine
  provisionné avec cette branche, options désactivées), la suite de
  niveau 0 295/295 OK, `tools/norms`, ruff, flake8 et `mypy --strict`
  sans remarque sur les fichiers modifiés.
- Non testé : la console DNS de Windows, le Centre d'administration
  Active Directory et Utilisateurs et ordinateurs Active Directory face à
  ces fonctionnalités (pas de client Windows dans cet environnement), et
  la suite de tests complète de Samba.


Feuille de route
----------------

Fait en phase 3 : membres venant d'un autre domaine (sur un GC), groupes
universels des autres domaines dans les jetons, maître
d'infrastructure, maître d'attribution des noms de domaine, catalogue
global d'un domaine enfant, ForestDnsZones sur les DC enfants,
`parent_index` et drapeaux de la forêt dans Netlogon, filtrage de SID
dans la forêt, backlink manquant et vérification du KCC.

Fait en phase 4 : chaque fonctionnalité derrière une option de
`smb.conf`, rôle de maître d'infrastructure désactivé sur un GC,
redirecteurs conditionnels et zones de stub (dans AD, avec les trois
étendues de réplication, ou locaux), partition par défaut d'une nouvelle
zone DNS, corbeille AD et restauration depuis le Centre d'administration
Active Directory (avec samba-adws), ordre des objets dans Utilisateurs
et ordinateurs Active Directory.

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
   Samba ; `subdom_dc` dans autobuild ; captures de la console DNS, du
   Centre d'administration Active Directory et d'Utilisateurs et
   ordinateurs Active Directory face aux fonctionnalités de la phase 4.
5. **DNS :** transferts de zone pour les zones de stub, changement du
   type et de l'étendue de réplication d'une zone, partition legacy dans
   `EnumDirectoryPartitions`, BIND9 DLZ.


Soumission upstream
-------------------

Les commits suivent les conventions de Samba (une modification par
commit, sujets au format `composant: résumé`). Avant de les envoyer au
projet Samba, chaque commit doit porter une ligne `Signed-off-by:` de
son auteur et passer les relectures décrites dans
[README.contributing](README.contributing).
