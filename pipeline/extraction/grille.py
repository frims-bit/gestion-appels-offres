import json
from groq import Groq
from django.conf import settings
from appels_offres.models import AppelOffre, CritereGrille

client = Groq(api_key=settings.GROQ_API_KEY)

SYSTEM_PROMPT = """Tu es un expert en marchés publics togolais avec 20 ans d'expérience à la Direction des Marchés Publics.

Tu analyses un cahier des charges et extrais la grille d'évaluation complète et structurée.

## RÈGLES D'EXTRACTION

### 1. IDENTIFICATION DES GROUPES
Les groupes sont les grandes familles de critères. Identifie-les selon le plan du document :

| Groupe | Catégorie | Type | Description |
|--------|-----------|------|-------------|
| Recevabilité administrative | administratif | eliminatoire | Pièces administratives obligatoires (éliminatoire) |
| Conformité technique | technique | eliminatoire | Spécifications techniques minimales (éliminatoire) |
| Évaluation financière | financier | notable | Prix, délais, conditions (notable) |
| Qualification | administratif | notable | Capacités financière et technique (notable) |

### 2. IDENTIFICATION DES SOUS-CRITÈRES

#### Recevabilité administrative (éliminatoire)
Extrais chaque pièce obligatoire mentionnée dans la section "Pièces à fournir" ou "Recevabilité" :
- Lettre de soumission datée, signée et cachetée → eliminatoire, note_max=0
- Garantie de soumission (2% du montant) → eliminatoire, note_max=0
- Bordereau des prix unitaires signé → eliminatoire, note_max=0
- RCCM (Registre de Commerce) → eliminatoire, note_max=0
- Attestation de régularité fiscale (< 3 mois) → eliminatoire, note_max=0
- Déclaration sur l'honneur de non-exclusion → eliminatoire, note_max=0

#### Conformité technique (éliminatoire)
Extrais chaque spécification technique minimale (RAM, processeur, disque dur, etc.) :
- Chaque spécification → eliminatoire, note_max=0
- Le non-respect d'une seule spécification entraîne le rejet

#### Évaluation financière (notable)
- Prix total TTC → notable, note_max=40
- Délai de livraison → notable, note_max=10
- Conditions de paiement → notable, note_max=10

#### Qualification (notable)
- Capacité financière (ligne de crédit ≥ 15% du montant) → notable, note_max=20
- Références similaires (≥ 2 marchés ≥ 20 M FCFA) → notable, note_max=15
- Technicien certifié (≥ 3 ans d'expérience) → notable, note_max=15
- Service après-vente (centre agréé à Lomé) → notable, note_max=10

### 3. RÈGLES DE NOTATION
| Type | Note max | Règle |
|------|----------|-------|
| eliminatoire | 0 | Présent/Absent uniquement |
| notable | 10-40 | Notation progressive selon qualité |

### 4. STRUCTURE DE RÉPONSE EXACTE
```json
{
  "groupes": [
    {
      "libelle": "Recevabilité administrative",
      "categorie": "administratif",
      "type_groupe": "eliminatoire",
      "note_max": 0,
      "sous_criteres": [
        {
          "libelle": "Lettre de soumission datée, signée et cachetée",
          "type": "eliminatoire",
          "note_max": 0,
          "description": "Document obligatoire"
        }
      ]
    },
    {
      "libelle": "Évaluation financière",
      "categorie": "financier",
      "type_groupe": "notable",
      "note_max": 40,
      "sous_criteres": [
        {
          "libelle": "Prix total TTC le moins-disant",
          "type": "notable",
          "note_max": 40,
          "description": "Offre financière la plus avantageuse"
        }
      ]
    }
  ]
}
```

### 5. INSTRUCTIONS IMPORTANTES
- Ne mets RIEN d'autre que le JSON
- Si un groupe n'a pas de sous-critères, ne le crée pas
- Les notes maximales doivent être des nombres entiers
- Les libellés doivent être précis et complets

Réponds UNIQUEMENT avec le JSON.
"""

CATEGORIES_VALIDES = {"technique", "financier", "administratif"}
TYPES_VALIDES = {"eliminatoire", "notable"}


def extraire_sections_pertinentes(texte: str) -> str:
    """
    RAG (recherche par mots-clés) : extrait les sections du document
    contenant probablement des critères d'évaluation, pour réduire le bruit
    envoyé au LLM et améliorer la précision de l'extraction.
    """
    mots_cles = [
        "critère", "évaluation", "notation", "recevabilité", "conformité",
        "qualification", "spécification", "pièce", "document", "fournir",
        "exigence", "condition", "attribution", "score", "note", "pondération",
    ]

    lignes = texte.split("\n")
    sections = []
    i = 0
    while i < len(lignes):
        ligne = lignes[i].lower()
        if any(mot in ligne for mot in mots_cles):
            debut = max(0, i - 5)
            fin = min(len(lignes), i + 6)
            sections.append("\n".join(lignes[debut:fin]))
            i = fin
        else:
            i += 1

    resultat = "\n\n".join(sections) if sections else texte[:15000]
    return resultat


def _nettoyer_groupe(g: dict) -> dict:
    """Valide et normalise les champs d'un groupe renvoyé par le LLM."""
    libelle = (g.get("libelle") or "Groupe non nommé").strip()
    categorie = g.get("categorie", "technique")
    if categorie not in CATEGORIES_VALIDES:
        categorie = "technique"

    type_groupe = g.get("type_groupe", "notable")
    if type_groupe not in TYPES_VALIDES:
        type_groupe = "notable"

    note_max = 0 if type_groupe == "eliminatoire" else int(g.get("note_max", 0) or 0)

    return {
        "libelle": libelle,
        "categorie": categorie,
        "type_groupe": type_groupe,
        "note_max": note_max,
    }


def _nettoyer_sous_critere(sc: dict) -> dict:
    libelle = (sc.get("libelle") or "Sous-critère non nommé").strip()
    sc_type = sc.get("type", "notable")
    if sc_type not in TYPES_VALIDES:
        sc_type = "notable"

    note_max = 0 if sc_type == "eliminatoire" else int(sc.get("note_max", 0) or 0)

    return {"libelle": libelle, "type": sc_type, "note_max": note_max}


def generer_grille(appel_offre: AppelOffre, texte_cdc: str) -> list:
    """
    Extrait les groupes et sous-critères d'un cahier des charges via Groq,
    en s'appuyant sur une étape RAG pour cibler les sections pertinentes.
    """
    if not texte_cdc or len(texte_cdc.strip()) < 50:
        print("[grille] Texte trop court.")
        return []

    sections = extraire_sections_pertinentes(texte_cdc)
    texte_envoye = sections if len(sections) > 100 else texte_cdc[:15000]

    print(f"[grille] Texte envoyé à Groq : {len(texte_envoye)} caractères")

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Voici le cahier des charges à analyser :\n\n{texte_envoye}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        contenu_brut = response.choices[0].message.content
        resultat = json.loads(contenu_brut)
    except json.JSONDecodeError as e:
        print(f"[grille] Erreur JSON : {e}")
        return []
    except Exception as e:
        print(f"[grille] Erreur Groq : {e}")
        return []

    groupes_bruts = resultat.get("groupes", [])
    print(f"[grille] Groq a retourné {len(groupes_bruts)} groupes")

    elements_crees = []

    for g in groupes_bruts:
        g_clean = _nettoyer_groupe(g)

        groupe = CritereGrille.objects.create(
            appel_offre=appel_offre,
            libelle=g_clean["libelle"],
            categorie=g_clean["categorie"],
            type_groupe=g_clean["type_groupe"],
            note_max=g_clean["note_max"],
            source=CritereGrille.Source.IA,
            valide=False,
            parent=None,
        )
        elements_crees.append(groupe)
        print(f"[grille] Groupe créé : {g_clean['libelle']} "
              f"(type={g_clean['type_groupe']}, note_max={g_clean['note_max']})")

        for sc in g.get("sous_criteres", []):
            sc_clean = _nettoyer_sous_critere(sc)

            sous_critere = CritereGrille.objects.create(
                appel_offre=appel_offre,
                libelle=sc_clean["libelle"],
                categorie=g_clean["categorie"],
                type_groupe=sc_clean["type"],
                note_max=sc_clean["note_max"],
                source=CritereGrille.Source.IA,
                valide=False,
                parent=groupe,
            )
            elements_crees.append(sous_critere)
            print(f"[grille] Sous-critère créé : {sc_clean['libelle']} "
                  f"(type={sc_clean['type']}, note_max={sc_clean['note_max']})")

    return elements_crees


def ajouter_manuellement(appel_offre: AppelOffre, libelle: str, categorie: str,
                          type_groupe: str, note_max: int, parent=None) -> CritereGrille:
    """Ajoute manuellement un critère (groupe ou sous-critère)."""
    return CritereGrille.objects.create(
        appel_offre=appel_offre,
        libelle=libelle,
        categorie=categorie,
        type_groupe=type_groupe,
        note_max=note_max,
        source=CritereGrille.Source.MANUEL,
        valide=False,
        parent=parent,
    )