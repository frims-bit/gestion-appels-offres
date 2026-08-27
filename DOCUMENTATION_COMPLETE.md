# Documentation complete du projet

## 1. Vue d'ensemble

Ce projet est une application Django de gestion et d'evaluation des appels d'offres.
Elle couvre le cycle suivant :

1. creation ou import d'un appel d'offres ;
2. extraction du texte du cahier des charges ;
3. generation d'une grille d'evaluation assistee par IA ;
4. validation humaine de la grille ;
5. depot et traitement des dossiers des soumissionnaires ;
6. extraction OCR/IA des donnees candidates ;
7. evaluation par cascade, scoring et classement ;
8. correction humaine des decisions et des prix ;
9. generation d'un PV synthetique ;
10. generation d'un rapport d'evaluation detaille ;
11. validation et signature du PV.

L'application distingue clairement deux documents :

- PV : document administratif de synthese, court et lisible.
- Rapport d'evaluation : document technique detaille contenant les criteres, donnees extraites, justifications et anomalies.

## 2. Architecture Django

Le projet est organise autour des applications suivantes :

- `utilisateurs` : utilisateur personnalise et roles metier.
- `appels_offres` : appels d'offres, cahiers des charges, criteres et grilles.
- `soumissionnaires` : dossiers candidats, donnees extraites, scores et classement.
- `proces_verbaux` : PV, rapports, consultation, telechargement et validation.
- `historique` : journalisation des actions importantes.
- `pipeline` : extraction OCR/IA, scoring, cascade et generation documentaire.

Le point d'entree URL principal est `config/urls.py`.

Les routes principales sont :

- `/` : accueil et redirection selon le role.
- `/upload-ao/` : import d'un appel d'offres.
- `/validation-grille/<ao_id>/` : validation de la grille.
- `/soumissionnaires/upload-dossier/` : depot de dossiers candidats.
- `/soumissionnaires/traitement-dossiers/<ao_id>/` : extraction et traitement des dossiers.
- `/soumissionnaires/classement/<ao_id>/` : classement et detail des soumissionnaires.
- `/proces-verbaux/generer-pv/<ao_id>/` : generation du PV.
- `/proces-verbaux/rapports/generer/<ao_id>/` : generation du rapport d'evaluation.

## 3. Roles et permissions

Le modele `Utilisateur` etend `AbstractUser`.

Roles disponibles :

- `secretaire` : creation/import des appels d'offres et depot de dossiers.
- `evaluateur` : validation de grille, traitement, corrections et classement.
- `president` : consultation, validation finale et signature du PV.

Les vues sensibles verifient explicitement le role connecte avec `request.user.role`.
Les corrections humaines dans les details d'un soumissionnaire sont reservees aux roles `evaluateur` et `president`.

## 4. Modeles principaux

### AppelOffre

Represente un appel d'offres.

Champs principaux :

- `reference`
- `titre`
- `date_publication`
- `statut`
- `document_source`
- `texte_extrait`
- `created_at`

La propriete `grille_evaluee` indique si la grille existe et si tous ses criteres sont valides.

### CritereGrille

Represente un groupe ou un sous-critere d'evaluation.

Champs principaux :

- `appel_offre`
- `libelle`
- `categorie` : administratif, technique ou financier.
- `type_groupe` : eliminatoire ou notable.
- `note_max`
- `source` : IA ou manuel.
- `valide`
- `parent`

Un critere sans parent est un groupe.
Un critere avec parent est un sous-critere evaluable.

### Soumissionnaire

Represente une entreprise candidate.

Champs principaux :

- `appel_offre`
- `nom_entreprise`
- `fichier_dossier`
- `texte_extrait`
- `statut_conformite`
- `motif_rejet`
- `prix_lu_publiquement`
- `prix_corrige`
- `prix_financier_brut`
- `prix_financier_devise`
- `prix_financier_source`
- `prix_financier_statut`
- `prix_financier_validation_humaine`
- `rang`
- `statut_final`
- informations entreprise : adresse, telephone, email, beneficiaires effectifs, nationalite.

### DonneeExtraite

Associe un soumissionnaire a un critere.

Champs principaux :

- `soumissionnaire`
- `critere`
- `valeur_extraite`
- `justification_ia`

La contrainte `unique_together = ("soumissionnaire", "critere")` evite les doublons pour un meme critere.

### Score

Stocke une note attribuee a un soumissionnaire pour un critere.

Champs principaux :

- `soumissionnaire`
- `critere`
- `note`
- `justification`
- `valide_par`
- `date_validation`

### ProcesVerbal

Associe un PV a un appel d'offres.

Champs principaux :

- `appel_offre`
- `statut`
- `fichier`
- `valide_par`
- `date_generation`
- `date_validation`

### HistoriqueAction

Journalise les actions importantes.

Champs principaux :

- `utilisateur`
- `utilisateur_nom`
- `utilisateur_role`
- `appel_offre`
- `action`
- `details`
- `date_action`

Le nom et le role sont conserves pour garder une trace lisible meme si l'utilisateur est modifie ou supprime.

## 5. Flux fonctionnel principal

### 5.1 Appel d'offres

Le secretaire importe ou cree un appel d'offres.
Le cahier des charges est sauvegarde et son texte est extrait.
Le pipeline peut ensuite generer une grille a partir du texte.

### 5.2 Grille d'evaluation

Le module `pipeline/extraction/grille.py` prepare le texte utile, appelle le modele IA et cree les groupes/sous-criteres.
La grille reste modifiable et validable par l'evaluateur.

La logique ajoute aussi des exigences textuelles complementaires lorsque l'IA ne retourne pas assez de criteres par rapport au contenu detecte.

### 5.3 Depot des dossiers

Les dossiers des soumissionnaires sont enregistres avec leur fichier et leurs metadonnees.
Le depot est rattache a un appel d'offres.

### 5.4 Extraction OCR et IA

Le module `pipeline/extraction/lecture.py` extrait le texte :

- lecture native des PDF avec `pdfplumber` ;
- OCR via docTR si aucun texte natif exploitable n'est trouve ;
- extraction DOCX via `python-docx`.

Le module `pipeline/extraction/candidats.py` exploite ensuite le texte du dossier :

- selection de passages pertinents par critere ;
- appel IA pour determiner `Present`, `Absent` ou `Incertain` ;
- extraction structuree du prix financier ;
- extraction des informations de contact de l'entreprise lorsque le texte les contient.

### 5.5 Cascade d'evaluation

Le module `pipeline/scoring/cascade.py` applique les etapes :

1. recevabilite administrative ;
2. conformite technique ;
3. evaluation financiere ;
4. qualification ;
5. classement final.

La cascade met a jour les statuts, les motifs de rejet, les rangs et le statut final.

### 5.6 Correction humaine

Dans le modal "Details du soumissionnaire", chaque critere peut etre modifie.

L'evaluateur peut choisir :

- Conforme ;
- Non conforme ;
- Incertain.

La correction :

- conserve les donnees deja extraites ;
- met a jour ou cree la `DonneeExtraite` sans doublon ;
- enregistre la justification humaine ;
- journalise l'action ;
- relance la cascade ;
- met immediatement a jour le modal.

La modification impacte donc le classement, les resultats, le PV et le rapport.

### 5.7 Correction du prix financier

Si le prix est absent, ambigu ou corrige humainement, l'interface permet de mettre a jour :

- prix lu publiquement ;
- prix corrige ;
- source de la correction.

Apres sauvegarde, la cascade est relancee pour recalculer le classement.

## 6. Generation documentaire

### Contexte commun

Le module `pipeline/generation/document_context.py` prepare un contexte unique pour :

- la preview HTML ;
- le PV Word ;
- le rapport Word.

Cela evite les divergences entre l'affichage et les documents telecharges.

### Preview et DOCX

Le module `pipeline/generation/docx_preview.py` construit les documents Word en reprenant la meme structure que la preview.

Il gere :

- en-tete ;
- logo ;
- titres ;
- tableaux ;
- sections communes ;
- sections detaillees du rapport ;
- conclusion ;
- pied de page.

### PV

Le module `pipeline/generation/pv.py` produit un document synthetique.

Le PV contient les informations essentielles :

- appel d'offres ;
- soumissionnaires participants ;
- statut global ;
- score ou rang lorsque disponible ;
- attributaire propose ;
- conclusion administrative.

Le PV ne contient pas les details par critere, les justifications longues, le texte OCR ou les analyses techniques detaillees.

### Rapport d'evaluation

Le module `pipeline/generation/rapport.py` produit le document detaille.

Le rapport contient les informations techniques :

- criteres administratifs ;
- criteres techniques ;
- criteres financiers ;
- donnees extraites ;
- etats par critere ;
- justifications IA et humaines ;
- motifs et observations ;
- classement.

## 7. Interface utilisateur

Les templates principaux sont dans `templates/`.

Le classement et le modal de detail sont dans :

- `templates/soumissionnaires/classement.html`

Les previews documentaires sont dans :

- `templates/proces_verbaux/includes/document_preview.html`

Le style principal est dans :

- `static/css/app.css`

Les scripts principaux sont dans :

- `static/js/app.js`

Le modal de details utilise des tableaux adaptes a la largeur disponible.
Les textes longs sont renvoyes a la ligne afin d'eviter le scroll horizontal.

## 8. Parametrage

Les principaux parametres se trouvent dans `config/settings.py`.

Variables d'environnement utilisees :

- `DB_ENGINE`
- `DB_NAME`
- `DB_USER`
- `DB_PASSWORD`
- `DB_HOST`
- `DB_PORT`
- `DB_CHARSET`
- `GROQ_API_KEY`

Par defaut, le projet utilise SQLite.
Un moteur compatible MySQL/MariaDB peut etre configure via les variables d'environnement.

## 9. Points de controle pour maintenance

Avant une demonstration ou une mise en recette :

1. verifier que la base est migree ;
2. verifier que `GROQ_API_KEY` est disponible si les fonctions IA doivent etre utilisees ;
3. lancer `python manage.py check` ;
4. tester un flux complet avec au moins deux soumissionnaires ;
5. corriger manuellement un critere et verifier que le classement change si necessaire ;
6. generer le PV et confirmer qu'il reste synthetique ;
7. generer le rapport et confirmer que les details sont conserves.

## 10. Explication orale pour jury

Le projet automatise une partie longue et sensible de l'evaluation des appels d'offres tout en gardant l'humain au centre.

L'IA sert a extraire et proposer une interpretation.
L'evaluateur reste responsable de la validation.
Chaque correction humaine est sauvegardee, historisee et reintegree dans le classement.

La separation PV/Rapport est essentielle :

- le PV est le document officiel de synthese ;
- le rapport est le dossier justificatif technique.

Cette separation rend les documents plus lisibles pour une commission, tout en conservant la tracabilite necessaire en cas de controle.

## 11. Questions possibles du jury

### Comment evitez-vous que l'IA decide seule ?

L'IA extrait et propose des valeurs, mais les evaluateurs peuvent corriger chaque critere.
Les corrections humaines sont sauvegardees en base, historisees et utilisees par la cascade de classement.

### Que se passe-t-il si l'OCR ne voit pas une signature ?

Le systeme marque le point comme incertain lorsque la preuve visuelle ne peut pas etre confirmee par le texte.
L'evaluateur peut ensuite corriger manuellement apres verification du document.

### Pourquoi avoir separe le PV du rapport ?

Le PV doit etre une synthese officielle claire.
Le rapport contient les details techniques permettant de justifier les resultats.
Cela evite un PV trop volumineux et difficile a lire.

### Comment les doublons de donnees sont-ils evites ?

Le modele `DonneeExtraite` impose une unicite par couple soumissionnaire/critere.
Lors d'une correction, le code met a jour l'enregistrement existant ou en cree un seul si la donnee n'existait pas.

### Le classement tient-il compte des corrections ?

Oui.
Apres une correction humaine de critere ou de prix, la cascade est relancee.
Les statuts, rangs et resultats sont recalcules.

### Les documents Word correspondent-ils a la preview ?

Oui.
La preview HTML et les documents Word utilisent un contexte documentaire commun.
Le generateur DOCX reprend la structure prevue pour le PV et le rapport.

