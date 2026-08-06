import json
from groq import Groq
from django.conf import settings
from soumissionnaires.models import Soumissionnaire, DonneeExtraite

client = Groq(api_key=settings.GROQ_API_KEY)

SYSTEM_PROMPT_BASE = """Tu es un expert en marchés publics togolais, spécialisé dans l'analyse des dossiers de soumission.

Tu analyses le dossier d'un candidat et extrais précisément la valeur pour le critère demandé.

## RÈGLES D'EXTRACTION

1. Cherche UNIQUEMENT la valeur demandée dans le texte du dossier.
2. Si l'information n'est pas présente, réponds "Non fourni" sans supposer.
3. Ne fais PAS de paraphrase : extrais l'information exacte ou cite l'extrait.
4. Pour les documents administratifs (lettre, garantie, RCCM, etc.) :
   - Vérifie la présence
   - Extrais les dates, numéros, montants si disponibles
5. Pour les spécifications techniques (RAM, processeur, etc.) :
   - Extrais la valeur exacte (ex: "16 Go", "Intel i7", "512 Go SSD")
6. Pour les informations de contact (email, téléphone, adresse) :
   - Extrais l'information complète
7. Pour les critères financiers (prix, capacité, références) :
   - Extrais le montant exact
   - Indique le pourcentage si pertinent

## FORMAT DE RÉPONSE EXACT
```json
{
  "valeur": "ce que le dossier dit sur ce critère",
  "justification": "extrait exact du texte qui justifie la valeur"
}
```

## EXEMPLES

### Critère : "Lettre de soumission datée, signée et cachetée"
{"valeur": "Fournie", "justification": "Lettre de soumission datée du 15/07/2026, signée et cachetée"}

### Critère : "Garantie de soumission (2% du montant)"
{"valeur": "Fournie - BGD Togo N°GAR-2026-TECH", "justification": "Garantie de soumission BGD Togo N°GAR-2026-TECH pour 900 000 FCFA"}

### Critère : "RAM 16 Go minimum"
{"valeur": "16 Go DDR4", "justification": "Mémoire RAM : 16 Go DDR4 (4x4 Go) Kingston"}

### Critère : "Email de contact"
{"valeur": "contact@technoplus.tg", "justification": "Email : contact@technoplus.tg"}

### Critère : "Références similaires (≥ 2 marchés ≥ 20 M FCFA)"
{"valeur": "3 marchés référencés : Moov Africa (35 M FCFA, 2024), Togo Telecom (28 M FCFA, 2025), Ministère (22 M FCFA, 2025)", "justification": "Liste des références : Moov Africa - 35 M FCFA, Togo Telecom - 28 M FCFA, Ministère - 22 M FCFA"}

### Critère : "Capacité financière >= 15% du montant"
{"valeur": "Ligne de crédit de 15 000 000 FCFA (33% du montant)", "justification": "Attestation de ligne de crédit Moov Africa N°LC-2026-045 pour 15 000 000 FCFA"}

## INSTRUCTIONS IMPORTANTES
- Ne mets RIEN d'autre que le JSON
- La justification doit être un extrait textuel du dossier
- Si l'information n'est pas trouvée, mets "Non fourni" dans valeur
- Pour les critères de présence/absence, mets "Présent" ou "Absent"
"""


def _sections_pertinentes_pour_critere(libelle_critere: str, texte_dossier: str) -> str:
    """
    RAG ciblé : ne garde que les paragraphes du dossier qui contiennent
    au moins un mot significatif (>3 lettres) du libellé du critère,
    afin de réduire le bruit et le nombre de tokens envoyés au LLM.
    """
    mots_cles = [m for m in libelle_critere.lower().split() if len(m) > 3]
    lignes = texte_dossier.split("\n")
    sections = []

    for i, ligne in enumerate(lignes):
        ligne_lower = ligne.lower()
        if any(mot in ligne_lower for mot in mots_cles):
            debut = max(0, i - 2)
            fin = min(len(lignes), i + 3)
            sections.append("\n".join(lignes[debut:fin]))

    return "\n\n".join(sections) if sections else texte_dossier[:10000]


def _extraire_par_critere(soumissionnaire: Soumissionnaire, critere, texte_dossier: str) -> dict:
    """Appel Groq pour un critère spécifique, avec RAG sur le dossier."""
    system_prompt = f"""{SYSTEM_PROMPT_BASE}

Critère à analyser : "{critere.libelle}"
Catégorie : {critere.categorie}
Type : {critere.type_groupe}
"""

    texte_envoye = _sections_pertinentes_pour_critere(critere.libelle, texte_dossier)

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": f"Voici le dossier du candidat {soumissionnaire.nom_entreprise} :\n\n{texte_envoye}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.2,
    )

    return json.loads(response.choices[0].message.content)


def extraire_donnees_candidat(soumissionnaire: Soumissionnaire, texte_dossier: str):
    """
    Extrait les données du dossier d'un candidat pour chaque critère validé
    de l'appel d'offres, en utilisant un RAG ciblé par critère.
    """
    criteres = soumissionnaire.appel_offre.criteres.filter(valide=True)

    if not criteres:
        print(f"[candidats] Aucun critère validé pour {soumissionnaire.appel_offre.reference}")
        return

    print(f"[candidats] Extraction pour {soumissionnaire.nom_entreprise} : {criteres.count()} critères")

    for critere in criteres:
        try:
            print(f"[candidats] Traitement : {critere.libelle}")
            resultat = _extraire_par_critere(soumissionnaire, critere, texte_dossier)

            valeur = resultat.get("valeur", "Non extrait")
            justification = resultat.get("justification", "")

            print(f"[candidats]   Valeur : {str(valeur)[:50]}...")

            DonneeExtraite.objects.create(
                soumissionnaire=soumissionnaire,
                critere=critere,
                valeur_extraite=valeur,
                justification_ia=justification,
            )
        except json.JSONDecodeError as e:
            print(f"[candidats] Erreur JSON pour {critere.libelle}: {e}")
        except Exception as e:
            print(f"[candidats] Erreur pour {critere.libelle}: {e}")

    print(f"[candidats] Terminé pour {soumissionnaire.nom_entreprise}")