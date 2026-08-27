import json
import re
import logging
from groq import Groq
from django.conf import settings
from appels_offres.models import AppelOffre, CritereGrille

logger = logging.getLogger(__name__)
client = Groq(api_key=settings.GROQ_API_KEY)

SYSTEM_PROMPT = """Tu es un expert en marches publics togolais avec 20 ans d'experience a la Direction des Marches Publics.

Tu analyses un cahier des charges et extrais la grille d'evaluation complete et structuree.

Regles d'extraction :
- Extrais toutes les exigences reellement presentes dans le document, sans les resumer en quelques criteres generiques.
- Utilise les libelles du document et n'invente pas de familles.
- Les exemples ci-dessous ne sont pas une liste ferme, ce sont seulement des exemples possibles si le document les contient.
- D'autres familles peuvent exister et doivent etre conservees telles quelles si elles apparaissent dans le document.
- Pour chaque groupe, extrais toutes les exigences, conditions, preuves, seuils ou notes explicitement mentionnes.
- Les pieces administratives doivent etre separees piece par piece si elles sont listees.
- Les specifications techniques doivent etre separees specification par specification si elles sont listees.
- Les conditions d'eligibilite, de qualification, les seuils minimaux, les methodes de notation et les causes de rejet doivent etre conservees comme criteres distincts.
- Si un groupe contient une exigence autonome mais peu de sous-criteres, tu peux le representer avec une seule sous-critere resumant cette exigence.
- Deduis le type et la note maximale a partir du document.
- Si une exigence est eliminatoire, note_max = 0.
- Si une note ou ponderation est indiquée, conserve-la.
- Ne cree pas de criteres bonus non mentionnes.

Structure de reponse :
{
  "groupes": [
    {
      "libelle": "...",
      "categorie": "administratif|technique|financier",
      "type_groupe": "eliminatoire|notable",
      "note_max": 0,
      "sous_criteres": [
        {
          "libelle": "...",
          "type": "eliminatoire|notable",
          "note_max": 0,
          "description": "..."
        }
      ]
    }
  ]
}

N'envoie rien d'autre que le JSON.
"""

CATEGORIES_VALIDES = {"technique", "financier", "administratif"}
TYPES_VALIDES = {"eliminatoire", "notable"}
MIN_CRITERES_ATTENDUS = 8

GROUPES_FALLBACK = {
    "administratif": "Recevabilite administrative",
    "technique": "Conformite et capacites techniques",
    "financier": "Evaluation financiere",
}

MOTS_CLES_EXIGENCES = [
    "doit", "doivent", "obligatoire", "obligatoires", "fournir", "produire",
    "joindre", "presenter", "exige", "exigence", "condition", "eligible",
    "eligibilite", "qualification", "capacite", "experience", "attestation",
    "registre", "rccm", "nif", "fiscal", "social", "garantie", "caution",
    "specification", "minimum", "seuil", "note", "notation", "points",
    "montant", "prix", "classement", "eliminatoire", "rejet",
]

ADMIN_KEYWORDS = [
    "piece", "pieces", "administr", "attestation", "rccm", "nif", "fiscal",
    "social", "registre", "caution", "garantie", "declaration", "quitus",
]
FINANCE_KEYWORDS = [
    "prix", "montant", "financier", "ttc", "ht", "bordereau", "devis",
    "classement", "moins disant", "offre financiere",
]
TECH_KEYWORDS = [
    "technique", "specification", "materiel", "equipement", "qualification",
    "experience", "personnel", "moyen", "capacite", "seuil", "minimum",
]


def extraire_sections_pertinentes(texte: str) -> str:
    mots_cles = [
        "critere", "evaluation", "notation", "recevabilite", "conformite",
        "qualification", "specification", "piece", "document", "fournir",
        "exigence", "condition", "attribution", "score", "note", "ponderation",
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

    extrait = "\n\n".join(sections) if sections else ""
    if extrait:
        return f"{texte[:12000]}\n\n{extrait}"[:35000]
    return texte[:35000]


def _normaliser_libelle(valeur: str) -> str:
    return re.sub(r"\s+", " ", str(valeur or "").strip(" \t-•:;,.")).strip()


def _ligne_exigence(ligne: str) -> bool:
    ligne_min = ligne.lower()
    if len(ligne_min) < 12 or len(ligne_min) > 260:
        return False
    if not any(mot in ligne_min for mot in MOTS_CLES_EXIGENCES):
        return False
    return bool(
        re.match(r"^\s*(?:[-•*]|\d+[.)]|\([a-z0-9]+\)|[a-z]\))\s+", ligne_min)
        or any(mot in ligne_min for mot in MOTS_CLES_EXIGENCES[:18])
    )


def _categorie_depuis_ligne(ligne: str) -> str:
    ligne_min = ligne.lower()
    if any(mot in ligne_min for mot in FINANCE_KEYWORDS):
        return "financier"
    if any(mot in ligne_min for mot in ADMIN_KEYWORDS):
        return "administratif"
    if any(mot in ligne_min for mot in TECH_KEYWORDS):
        return "technique"
    return "technique"


def _type_depuis_ligne(ligne: str) -> str:
    ligne_min = ligne.lower()
    if any(mot in ligne_min for mot in ["obligatoire", "eliminatoire", "rejet", "irrecevable", "non conforme", "minimum", "doit"]):
        return "eliminatoire"
    if any(mot in ligne_min for mot in ["note", "points", "ponderation", "score"]):
        return "notable"
    return "eliminatoire"


def _note_depuis_ligne(ligne: str, type_groupe: str) -> int:
    if type_groupe == "eliminatoire":
        return 0
    match = re.search(r"(\d{1,3})\s*(?:points|pts|%)", ligne.lower())
    return int(match.group(1)) if match else 0


def extraire_exigences_textuelles(texte: str) -> list:
    exigences = []
    vus = set()
    for ligne in texte.splitlines():
        libelle = _normaliser_libelle(ligne)
        if not _ligne_exigence(libelle):
            continue
        cle = libelle.lower()
        if cle in vus:
            continue
        vus.add(cle)
        type_groupe = _type_depuis_ligne(libelle)
        exigences.append(
            {
                "libelle": libelle[:255],
                "categorie": _categorie_depuis_ligne(libelle),
                "type": type_groupe,
                "note_max": _note_depuis_ligne(libelle, type_groupe),
            }
        )
    return exigences


def _nettoyer_groupe(g: dict) -> dict:
    libelle = (g.get("libelle") or "").strip()
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
    libelle = (sc.get("libelle") or "").strip()
    sc_type = sc.get("type", "notable")
    if sc_type not in TYPES_VALIDES:
        sc_type = "notable"

    note_max = 0 if sc_type == "eliminatoire" else int(sc.get("note_max", 0) or 0)

    return {"libelle": libelle, "type": sc_type, "note_max": note_max}


def _libelles_existants(appel_offre: AppelOffre) -> set:
    return {
        _normaliser_libelle(libelle).lower()
        for libelle in appel_offre.criteres.values_list("libelle", flat=True)
    }


def _creer_exigences_textuelles(appel_offre: AppelOffre, exigences: list, elements_crees: list) -> None:
    if not exigences:
        return

    groupes_par_categorie = {
        groupe.categorie: groupe
        for groupe in appel_offre.criteres.filter(parent__isnull=True)
    }
    vus = _libelles_existants(appel_offre)

    for exigence in exigences:
        libelle = exigence["libelle"]
        cle = _normaliser_libelle(libelle).lower()
        if not cle or cle in vus:
            continue

        categorie = exigence["categorie"]
        groupe = groupes_par_categorie.get(categorie)
        if groupe is None:
            groupe = CritereGrille.objects.create(
                appel_offre=appel_offre,
                libelle=GROUPES_FALLBACK[categorie],
                categorie=categorie,
                type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
                note_max=0,
                source=CritereGrille.Source.IA,
                valide=False,
                parent=None,
            )
            groupes_par_categorie[categorie] = groupe
            elements_crees.append(groupe)
            vus.add(_normaliser_libelle(groupe.libelle).lower())
            logger.info("[grille] Groupe complementaire cree : %s", groupe.libelle)

        sous_critere = CritereGrille.objects.create(
            appel_offre=appel_offre,
            libelle=libelle,
            categorie=categorie,
            type_groupe=exigence["type"],
            note_max=exigence["note_max"],
            source=CritereGrille.Source.IA,
            valide=False,
            parent=groupe,
        )
        elements_crees.append(sous_critere)
        vus.add(cle)
        logger.info(
            "[grille] Critere extrait du texte cree : %s (categorie=%s, type=%s)",
            sous_critere.libelle,
            categorie,
            exigence["type"],
        )


def _journaliser_grille(appel_offre: AppelOffre) -> None:
    groupes = appel_offre.criteres.filter(parent__isnull=True)
    criteres = appel_offre.criteres.filter(parent__isnull=False)
    logger.info("[grille] Nombre de groupes generes : %s", groupes.count())
    logger.info("[grille] Nombre de criteres generes : %s", criteres.count())
    logger.info("[grille] Nombre de sous-criteres generes : 0")
    logger.debug("[grille] Liste des criteres apres generation :")
    for critere in appel_offre.criteres.select_related("parent").order_by("parent__id", "id"):
        prefixe = "GROUPE" if critere.parent_id is None else f" - {critere.parent.libelle}"
        logger.debug(
            "[grille] %s | %s | categorie=%s | type=%s | note=%s",
            prefixe,
            critere.libelle,
            critere.categorie,
            critere.type_groupe,
            critere.note_max,
        )


def generer_grille(appel_offre: AppelOffre, texte_cdc: str) -> list:
    if not texte_cdc or len(texte_cdc.strip()) < 50:
        logger.warning("[grille] Texte trop court.")
        return []

    sections = extraire_sections_pertinentes(texte_cdc)
    texte_envoye = sections if len(sections) > 100 else texte_cdc[:20000]
    exigences_textuelles = extraire_exigences_textuelles(texte_cdc)

    logger.info("[grille] Texte envoye a Groq : %s caracteres", len(texte_envoye))
    logger.info("[grille] Exigences textuelles candidates : %s", len(exigences_textuelles))

    try:
        response = client.chat.completions.create(
            model="openai/gpt-oss-120b",
            messages=[
                {"role": "system", "content": SYSTEM_PROMPT},
                {"role": "user", "content": f"Voici le cahier des charges a analyser :\n\n{texte_envoye}"},
            ],
            response_format={"type": "json_object"},
            temperature=0.2,
        )
        contenu_brut = response.choices[0].message.content
        resultat = json.loads(contenu_brut)
    except json.JSONDecodeError as e:
        logger.warning("[grille] Erreur JSON : %s", e)
        return []
    except Exception as e:
        logger.exception("[grille] Erreur Groq : %s", e)
        return []

    groupes_bruts = resultat.get("groupes", [])
    logger.info("[grille] Groq a retourne %s groupes", len(groupes_bruts))

    elements_crees = []

    for g in groupes_bruts:
        g_clean = _nettoyer_groupe(g)
        if not g_clean["libelle"]:
            continue

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
        logger.info(
            "[grille] Groupe cree : %s (type=%s, note_max=%s)",
            g_clean["libelle"],
            g_clean["type_groupe"],
            g_clean["note_max"],
        )

        for sc in g.get("sous_criteres", []):
            sc_clean = _nettoyer_sous_critere(sc)
            if not sc_clean["libelle"]:
                continue

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
            logger.info(
                "[grille] Sous-critere cree : %s (type=%s, note_max=%s)",
                sc_clean["libelle"],
                sc_clean["type"],
                sc_clean["note_max"],
            )

    criteres_generes = appel_offre.criteres.filter(parent__isnull=False).count()
    if criteres_generes < MIN_CRITERES_ATTENDUS or len(exigences_textuelles) > criteres_generes:
        logger.info(
            "[grille] Complement textuel active : %s criteres IA pour %s exigences candidates",
            criteres_generes,
            len(exigences_textuelles),
        )
        _creer_exigences_textuelles(appel_offre, exigences_textuelles, elements_crees)

    _journaliser_grille(appel_offre)
    return elements_crees


def ajouter_manuellement(appel_offre: AppelOffre, libelle: str, categorie: str, type_groupe: str, note_max: int, parent=None) -> CritereGrille:
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
