import json
from groq import Groq
from django.conf import settings
from soumissionnaires.models import Soumissionnaire

client = Groq(api_key=settings.GROQ_API_KEY)

# ============ PROMPTS ============

SYSTEM_PROMPT_RECEVABILITE = """Tu es un expert en marchés publics togolais. Tu vérifies la recevabilité administrative d'une offre.

## RÈGLES DE RECEVABILITÉ

Une offre est RECEVABLE si TOUTES les pièces obligatoires suivantes sont fournies :

| Pièce | État requis |
|-------|-------------|
| Lettre de soumission datée, signée et cachetée | Présente, datée, signée, cachetée |
| Garantie de soumission (2% du montant) | Présente, valide |
| Bordereau des prix unitaires signé | Présent, signé |
| RCCM (Registre de Commerce) | Présent, valide |
| Attestation de régularité fiscale | Présente, < 3 mois |
| Déclaration sur l'honneur de non-exclusion | Présente |

Si UNE SEULE pièce est manquante ou non conforme, l'offre est NON RECEVABLE.

## EXEMPLES DE MOTIFS DE REJET
- "Garantie de soumission absente"
- "Lettre de soumission non signée"
- "Attestation fiscale périmée (date: 01/01/2025 > 3 mois)"
- "RCCM non fourni"
- "Bordereau des prix non signé"

## FORMAT DE RÉPONSE EXACT
{"recevable": true, "motif": "Toutes les pièces sont conformes"}
ou
{"recevable": false, "motif": "Garantie de soumission absente"}

Ne mets RIEN d'autre que le JSON.
"""

SYSTEM_PROMPT_CONFORMITE = """Tu vérifies la conformité technique d'une offre.

## RÈGLES DE CONFORMITÉ

Une offre est CONFORME si elle respecte TOUTES les spécifications techniques minimales.

Exemple de spécifications :
- RAM ≥ 16 Go
- Processeur ≥ Intel Core i5 ou équivalent AMD
- Disque dur ≥ 512 Go SSD
- Écran ≥ 24" Full HD
- Garantie ≥ 3 ans

Si UNE SEULE spécification n'est pas respectée, l'offre est NON CONFORME.

## EXEMPLES DE MOTIFS DE REJET
- "RAM : 8 Go proposé, 16 Go minimum exigé"
- "Processeur : Intel Core i3, i5 minimum exigé"
- "Disque dur : 256 Go, 512 Go minimum exigé"
- "Écran : 21.5\\", 24\\" minimum exigé"

## FORMAT DE RÉPONSE EXACT
{"conforme": true, "motif": "Toutes les spécifications sont respectées"}
ou
{"conforme": false, "motif": "RAM : 8 Go, 16 Go minimum exigé"}

Ne mets RIEN d'autre que le JSON.
"""

SYSTEM_PROMPT_QUALIFICATION = """Tu vérifies la qualification d'un soumissionnaire pour un marché public togolais.

## CRITÈRES DE QUALIFICATION

| Critère | Exigence minimale |
|---------|-------------------|
| Capacité financière | Ligne de crédit ou liquidités ≥ 15% du montant de l'offre |
| Références similaires | ≥ 2 marchés de même nature ≥ 20 M FCFA sur 3 ans |
| Technicien certifié | ≥ 1 ingénieur/technicien avec ≥ 3 ans d'expérience |
| Service après-vente | Centre de service agréé à Lomé ou partenariat contractuel |

Une offre est QUALIFIÉE si TOUS les critères sont remplis.

## EXEMPLES DE MOTIFS DE REJET
- "Capacité financière : Ligne de crédit 5 M FCFA (11% du montant), 15% exigé"
- "Références : 1 seul marché de 15 M FCFA, 2 marchés ≥ 20 M FCFA exigés"
- "Technicien : Aucun ingénieur certifié, 1 exigé"
- "Service après-vente : Aucun centre agréé à Lomé"

## FORMAT DE RÉPONSE EXACT
{"qualifie": true, "motif": "Tous les critères de qualification sont remplis"}
ou
{"qualifie": false, "motif": "Capacité financière insuffisante : 5 M FCFA (11%), 15% exigé"}

Ne mets RIEN d'autre que le JSON.
"""


# ============ UTILITAIRES ============

def _demander_json(system_prompt: str, user_prompt: str) -> dict:
    """Appel Groq avec retour JSON garanti et gestion d'erreur centralisée."""
    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": user_prompt[:10000]},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        print("[cascade] Appel Groq réussi")
        return json.loads(response.choices[0].message.content)
    except Exception as e:
        print(f"[cascade] Erreur Groq : {e}")
        return {}


def _texte_categorie(soumissionnaire: Soumissionnaire, categorie: str) -> str:
    """Reconstitue le texte des données extraites pour une catégorie donnée."""
    donnees = soumissionnaire.donnees_extraites.filter(critere__categorie=categorie)
    if not donnees:
        return ""
    return "\n".join(f"• {d.critere.libelle}: {d.valeur_extraite}" for d in donnees)


# ============ ÉTAPES DE LA CASCADE ============

def etape_1_recevabilite(soumissionnaire: Soumissionnaire):
    """Étape 1 : vérification de la recevabilité administrative."""
    print(f"[cascade] Étape 1 - Recevabilité pour {soumissionnaire.nom_entreprise}")

    texte = _texte_categorie(soumissionnaire, "administratif")

    if not texte:
        print(f"[cascade] Aucune donnée administrative pour {soumissionnaire.nom_entreprise}")
        soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.A_VERIFIER
        soumissionnaire.save()
        return soumissionnaire

    user_prompt = f"""Voici les pièces administratives du soumissionnaire {soumissionnaire.nom_entreprise} :

{texte}

Détermine si l'offre est recevable (toutes les pièces sont présentes et conformes)."""

    resultat = _demander_json(SYSTEM_PROMPT_RECEVABILITE, user_prompt)

    if resultat.get("recevable"):
        soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.RECEVABLE
        print(f"[cascade] {soumissionnaire.nom_entreprise} → RECEVABLE")
    else:
        soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.NON_RECEVABLE
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.ECARTE
        soumissionnaire.motif_rejet = resultat.get("motif", "Non recevable")
        print(f"[cascade] {soumissionnaire.nom_entreprise} → NON RECEVABLE : {soumissionnaire.motif_rejet}")

    soumissionnaire.save()
    return soumissionnaire


def etape_2_conformite_technique(soumissionnaire: Soumissionnaire):
    """Étape 2 : vérification de la conformité technique."""
    if soumissionnaire.statut_conformite != Soumissionnaire.StatutConformite.RECEVABLE:
        print(f"[cascade] {soumissionnaire.nom_entreprise} non recevable, skip conformité")
        return soumissionnaire

    print(f"[cascade] Étape 2 - Conformité technique pour {soumissionnaire.nom_entreprise}")

    texte = _texte_categorie(soumissionnaire, "technique")

    if not texte:
        print(f"[cascade] Aucune donnée technique pour {soumissionnaire.nom_entreprise}")
        return soumissionnaire

    user_prompt = f"""Voici les spécifications techniques du soumissionnaire {soumissionnaire.nom_entreprise} :

{texte}

Détermine si l'offre est conforme à toutes les spécifications minimales."""

    resultat = _demander_json(SYSTEM_PROMPT_CONFORMITE, user_prompt)

    if resultat.get("conforme"):
        soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE
        print(f"[cascade] {soumissionnaire.nom_entreprise} → CONFORME TECHNIQUE")
    else:
        soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.NON_CONFORME_TECHNIQUE
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.ECARTE
        soumissionnaire.motif_rejet = resultat.get("motif", "Non conforme technique")
        print(f"[cascade] {soumissionnaire.nom_entreprise} → NON CONFORME : {soumissionnaire.motif_rejet}")

    soumissionnaire.save()
    return soumissionnaire


def etape_3_classement(appel_offre):
    """Étape 3 : classement des offres techniquement conformes."""

    print("[cascade] Étape 3 - Classement")

    # Nettoyage des anciens rangs
    appel_offre.soumissionnaires.update(
        rang=None
    )

    candidats = appel_offre.soumissionnaires.filter(
        statut_conformite=Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
        statut_final=Soumissionnaire.StatutFinal.EN_COURS
    ).exclude(
        prix_corrige__isnull=True
    ).order_by(
        "prix_corrige"
    )


    classement = list(candidats)


    if not classement:
        print("[cascade] Aucun candidat conforme à classer")
        return []


    for rang, candidat in enumerate(classement, start=1):

        candidat.rang = rang
        candidat.save()

        print(
            f"[cascade] Rang {rang} : "
            f"{candidat.nom_entreprise} "
            f"- {candidat.prix_corrige} FCFA"
        )


    return classement


def etape_4_qualification(soumissionnaire):
    """Étape 4 : qualification du premier candidat classé."""

    if soumissionnaire.rang != 1:
        return soumissionnaire


    print(
        f"[cascade] Qualification de "
        f"{soumissionnaire.nom_entreprise}"
    )


    # Supprime les anciens gagnants
    Soumissionnaire.objects.filter(
        appel_offre=soumissionnaire.appel_offre,
        statut_final=Soumissionnaire.StatutFinal.RETENU
    ).exclude(
        id=soumissionnaire.id
    ).update(
        statut_final=Soumissionnaire.StatutFinal.ECARTE
    )


    texte_technique = _texte_categorie(
        soumissionnaire,
        "technique"
    )

    texte_financier = _texte_categorie(
        soumissionnaire,
        "financier"
    )


    if not texte_technique and not texte_financier:

        soumissionnaire.qualification_verifiee = True
        soumissionnaire.qualification_conforme = True
        soumissionnaire.statut_final = (
            Soumissionnaire.StatutFinal.RETENU
        )

        soumissionnaire.save()

        return soumissionnaire



    resultat = _demander_json(
        SYSTEM_PROMPT_QUALIFICATION,
        f"""
Soumissionnaire :
{soumissionnaire.nom_entreprise}


Informations techniques :

{texte_technique}


Informations financières :

{texte_financier}
"""
    )


    soumissionnaire.qualification_verifiee = True


    if resultat.get("qualifie"):

        soumissionnaire.qualification_conforme = True
        soumissionnaire.statut_final = (
            Soumissionnaire.StatutFinal.RETENU
        )

        print(
            f"[cascade] {soumissionnaire.nom_entreprise} RETENU"
        )


    else:

        soumissionnaire.qualification_conforme = False
        soumissionnaire.statut_final = (
            Soumissionnaire.StatutFinal.ECARTE
        )

        soumissionnaire.motif_rejet = (
            resultat.get(
                "motif",
                "Non qualifié"
            )
        )


    soumissionnaire.save()

    return soumissionnaire
def cascade_complete(appel_offre):
    """
    Lance toute la cascade d'évaluation.
    """

    print(
        f"[cascade] LANCEMENT POUR {appel_offre.reference}"
    )


    # Nettoyage des anciens résultats
    appel_offre.soumissionnaires.update(
        rang=None
    )


    # Réinitialisation des retenus précédents
    appel_offre.soumissionnaires.filter(
        statut_final=Soumissionnaire.StatutFinal.RETENU
    ).update(
        statut_final=Soumissionnaire.StatutFinal.EN_COURS
    )


    # Etapes 1 et 2
    for s in appel_offre.soumissionnaires.all():

        if s.statut_conformite == (
            Soumissionnaire.StatutConformite.A_VERIFIER
        ):

            etape_1_recevabilite(s)

            etape_2_conformite_technique(s)



    # Classement
    classement = etape_3_classement(
        appel_offre
    )


    # Qualification du meilleur prix
    if classement:

        gagnant = classement[0]

        etape_4_qualification(
            gagnant
        )


    print(
        "[cascade] CASCADE TERMINÉE"
    )


    return classement