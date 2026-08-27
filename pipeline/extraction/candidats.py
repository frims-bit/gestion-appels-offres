import json
import re
import unicodedata
import logging
from decimal import Decimal, InvalidOperation

from django.conf import settings
from groq import Groq

from soumissionnaires.models import DonneeExtraite


logger = logging.getLogger(__name__)
client = Groq(api_key=settings.GROQ_API_KEY)


SYSTEM_PROMPT_BASE = """
Tu es un expert en marches publics togolais.

Tu analyses uniquement les passages du dossier candidat fournis.

Regles :
- Ne jamais inventer.
- Utiliser uniquement le texte fourni dans le message utilisateur.
- Chercher les formulations equivalentes, pas seulement le titre exact.
- Ne jamais declarer absent uniquement parce que le nom du document differe.
- Si le critere complet est confirme par le texte, repondre "Present".
- Si le texte confirme que la piece ou l'exigence manque, repondre "Absent".
- Si le texte contient un indice utile mais ne confirme pas tout le critere, repondre "Incertain".
- La justification doit expliquer le passage utile ou l'absence constatee.
- Un modele texte ne peut pas voir une signature manuscrite, un cachet image ou un paraphe invisible dans l'OCR. Dans ce cas, repondre "Incertain" si le document est present mais que signature/cachet/date ne sont pas verifies dans le texte.

Reponds uniquement en JSON strict :

{
 "valeur": "Present|Absent|Incertain",
 "justification": "explication courte fondee sur le texte fourni"
}
"""


SYNONYMES_CRITERES = {
    "lettre soumission": [
        "acte engagement",
        "acte d engagement",
        "lettre offre",
        "formulaire soumission",
        "offre signee",
        "soumission signee",
    ],
    "garantie soumission": [
        "caution bancaire",
        "garantie bancaire",
        "caution soumission",
        "garantie d offre",
        "garantie de l offre",
    ],
    "bordereau prix": [
        "bpu",
        "bordereau des prix",
        "prix unitaires",
        "detail quantitatif",
        "devis quantitatif",
    ],
    "registre commerce rccm": [
        "rccm",
        "registre du commerce",
        "registre de commerce",
        "credit mobilier",
        "immatriculation commerciale",
        "numero rccm",
    ],
    "attestation fiscale": [
        "regularite fiscale",
        "quitus fiscal",
        "attestation de regularite fiscale",
        "otr",
        "office togolais des recettes",
    ],
    "declaration honneur": [
        "declaration sur l honneur",
        "non exclusion",
        "non-exclusion",
        "attestation de non exclusion",
    ],
}


def _normaliser(texte):
    texte = str(texte or "").lower()
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(
        caractere
        for caractere in texte
        if unicodedata.category(caractere) != "Mn"
    )
    return re.sub(r"[^a-z0-9%]+", " ", texte).strip()


def _mots_cles(libelle_critere):
    mots_vides = {
        "avec",
        "dans",
        "des",
        "les",
        "pour",
        "une",
        "sur",
        "aux",
        "par",
        "est",
        "sont",
        "moins",
        "mois",
        "montant",
    }
    libelle_normalise = _normaliser(libelle_critere)
    mots = {
        mot
        for mot in libelle_normalise.split()
        if len(mot) > 2 and mot not in mots_vides
    }

    for declencheur, synonymes in SYNONYMES_CRITERES.items():
        declencheur_mots = set(declencheur.split())
        if declencheur_mots.intersection(mots) or declencheur in libelle_normalise:
            for synonyme in synonymes:
                mots.update(_normaliser(synonyme).split())

    return sorted(mots)


def _fusionner_sections(lignes, sections):
    intervalles = sorted((debut, fin) for _, debut, fin in sections)
    fusionnes = []

    for debut, fin in intervalles:
        if not fusionnes or debut > fusionnes[-1][1]:
            fusionnes.append([debut, fin])
        else:
            fusionnes[-1][1] = max(fusionnes[-1][1], fin)

    extraits = [
        "\n".join(lignes[debut:fin]).strip()
        for debut, fin in fusionnes
    ]
    return "\n\n---\n\n".join(extrait for extrait in extraits if extrait)


def _sections_pertinentes_pour_critere(libelle_critere, texte_dossier):
    if not isinstance(texte_dossier, str):
        raise ValueError(
            "Le texte du dossier candidat est vide ou invalide. "
            "Verifie soumissionnaire.texte_extrait avant l'extraction IA."
        )

    texte_dossier = texte_dossier.strip()
    if not texte_dossier:
        raise ValueError(
            "Le texte du dossier candidat est vide. "
            "L'extraction IA necessite soumissionnaire.texte_extrait."
        )

    mots = _mots_cles(libelle_critere)
    lignes = texte_dossier.splitlines()
    lignes_normalisees = [_normaliser(ligne) for ligne in lignes]
    sections = []

    for i, ligne_normalisee in enumerate(lignes_normalisees):
        score = sum(1 for mot in mots if mot and mot in ligne_normalisee)
        if score:
            sections.append((score, max(0, i - 8), min(len(lignes), i + 12)))

    if sections:
        sections.sort(key=lambda item: item[0], reverse=True)
        contexte = _fusionner_sections(lignes, sections[:12])
        if len(contexte) >= 250:
            return contexte[:18000]

    # Fallback large : certains dossiers n'ont pas de titres standards.
    return texte_dossier[:18000]


def contexte_ia_pour_critere(libelle_critere, texte_dossier):
    return _sections_pertinentes_pour_critere(libelle_critere, texte_dossier)


def _normaliser_valeur_ia(valeur):
    valeur_norm = _normaliser(valeur)

    if "incertain" in valeur_norm or any(
        mot in valeur_norm
        for mot in ("non fourni", "non fournie", "a verifier", "a vérifier")
    ):
        return "Incertain"

    if any(mot in valeur_norm for mot in ("absent", "manquant", "inexistant", "introuvable")):
        return "Absent"

    if any(mot in valeur_norm for mot in ("present", "fourni", "fournie", "oui", "confirm")):
        return "Present"

    return "Incertain"


def _est_critere_prix_financier(critere):
    libelle = _normaliser(getattr(critere, "libelle", ""))
    return (
        "montant" in libelle
        and ("ttc" in libelle or "offre" in libelle)
        and any(mot in libelle for mot in ("classement", "prix", "financier", "total"))
    )


FINANCE_PROMPT = """
Tu extrais les donnees financieres d'un dossier candidat.

Regles :
- Ne jamais inventer.
- Si plusieurs montants sont presents, conserver celui qui semble correspondre au prix principal propose et expliquer le contexte.
- Si le montant est absent ou ambigu, renvoyer Incertain.
- Extraire aussi la devise si elle est mentionnee.
- Extraire le montant HT si distinct, le montant TTC si distinct, et toute indication utile.

Reponds uniquement en JSON strict :
{
  "prix_propose": "valeur ou vide",
  "devise": "FCFA|EUR|USD|autre ou vide",
  "montant_ht": "valeur ou vide",
  "montant_ttc": "valeur ou vide",
  "etat": "Present|Absent|Incertain",
  "justification": "explication courte"
}
"""

MONTANT_PATTERN = re.compile(
    r"(?P<montant>\d{1,3}(?:[\s\u00a0.]?\d{3})+(?:,\d+|\.\d+)?|\d+(?:,\d+|\.\d+)?)"
    r"\s*(?P<devise>f\s*cfa|fcfa|xof|cfa|francs?\s*cfa|eur|euro|usd|\$)?",
    re.IGNORECASE,
)

EMAIL_PATTERN = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+", re.IGNORECASE)
PHONE_PATTERN = re.compile(
    r"(?:\+?228[\s.-]*)?(?:9[0123679]|7[09]|2[02])(?:[\s.-]*\d{2}){3}",
    re.IGNORECASE,
)

CHAMPS_ENTREPRISE = {
    "adresse": (
        "adresse",
        "siege social",
        "domicile",
        "bp",
        "boite postale",
    ),
    "telephone": (
        "telephone",
        "tel",
        "contact",
        "mobile",
    ),
    "beneficiaires_effectifs": (
        "beneficiaire effectif",
        "beneficiaires effectifs",
        "beneficial owner",
        "proprietaire effectif",
    ),
    "nationalite": (
        "nationalite",
        "nationalite du beneficiaire",
    ),
}

MOTS_TOTAL_TTC_FORTS = (
    "montant total ttc",
    "total ttc",
    "offre ttc",
    "montant de l offre ttc",
    "montant total de l offre",
    "total de l offre",
    "montant de la soumission",
    "montant total de la soumission",
)

MOTS_TOTAL_TTC_FAIBLES = (
    "ttc",
    "toutes taxes comprises",
)

MOTS_MONTANT_A_EXCLURE = (
    "prix unitaire",
    "p u",
    "pu ",
    "sous total",
    "sous-total",
    "montant ht",
    "total ht",
    "hors taxe",
    "hors taxes",
    "tva",
    "avance",
    "garantie",
    "caution",
)


def _nettoyer_nombre(valeur):
    if valeur in (None, ""):
        return None
    texte = str(valeur).strip().replace(" ", "").replace("\u00a0", "")
    texte = texte.replace(",", ".")
    texte = re.sub(r"[^0-9.]", "", texte)
    if not texte:
        return None
    try:
        return Decimal(texte)
    except (InvalidOperation, ValueError):
        return None


def _devise_normalisee(devise):
    devise_norm = _normaliser(devise)
    if any(mot in devise_norm for mot in ("fcfa", "f cfa", "xof", "cfa", "franc")):
        return "XOF"
    if "eur" in devise_norm or "euro" in devise_norm:
        return "EUR"
    if "usd" in devise_norm or "$" in str(devise or ""):
        return "USD"
    return devise.strip().upper() if devise else ""


def _fenetre_source(texte, debut, fin, rayon=180):
    source_debut = max(0, debut - rayon)
    source_fin = min(len(texte), fin + rayon)
    return re.sub(r"\s+", " ", texte[source_debut:source_fin]).strip()


def _ligne_source(texte, debut, fin):
    ligne_debut = texte.rfind("\n", 0, debut) + 1
    ligne_fin = texte.find("\n", fin)
    if ligne_fin == -1:
        ligne_fin = len(texte)
    return re.sub(r"\s+", " ", texte[ligne_debut:ligne_fin]).strip()


def _valeur_apres_libelle(ligne, libelles):
    ligne_clean = re.sub(r"\s+", " ", ligne or "").strip()
    ligne_norm = _normaliser(ligne_clean)
    for libelle in libelles:
        libelle_norm = _normaliser(libelle)
        if libelle_norm and libelle_norm in ligne_norm:
            parts = re.split(r"\s*[:;\-]\s*", ligne_clean, maxsplit=1)
            if len(parts) == 2 and len(parts[1].strip()) >= 2:
                return parts[1].strip(" .;:-")
            index = ligne_norm.find(libelle_norm)
            if index >= 0:
                reste = ligne_clean[index + len(libelle):].strip(" .;:-")
                if len(reste) >= 2:
                    return reste
    return ""


def _extraire_infos_entreprise(texte_dossier):
    texte = texte_dossier or ""
    lignes = [ligne.strip() for ligne in texte.splitlines() if ligne.strip()]
    infos = {}

    email = EMAIL_PATTERN.search(texte)
    if email:
        infos["email"] = email.group(0)

    for ligne in lignes:
        if "telephone" in _normaliser(ligne) or "tel" in _normaliser(ligne) or "contact" in _normaliser(ligne):
            telephone = PHONE_PATTERN.search(ligne)
            if telephone:
                infos["telephone"] = telephone.group(0).strip()
                break
    if "telephone" not in infos:
        telephone = PHONE_PATTERN.search(texte)
        if telephone:
            infos["telephone"] = telephone.group(0).strip()

    for champ, libelles in CHAMPS_ENTREPRISE.items():
        if champ in infos:
            continue
        for ligne in lignes:
            valeur = _valeur_apres_libelle(ligne, libelles)
            if valeur:
                infos[champ] = valeur[:500]
                break

    return infos


def _enregistrer_infos_entreprise(soumissionnaire, texte_dossier):
    infos = _extraire_infos_entreprise(texte_dossier)
    champs_modifies = []
    for champ, valeur in infos.items():
        if valeur and not getattr(soumissionnaire, champ):
            setattr(soumissionnaire, champ, valeur)
            champs_modifies.append(champ)
    if champs_modifies:
        soumissionnaire.save(update_fields=champs_modifies)
    return infos


def _score_montant_financier(source):
    source_norm = _normaliser(source)
    score = 0
    for mot in MOTS_TOTAL_TTC_FORTS:
        if mot in source_norm:
            score += 8
    for mot in MOTS_TOTAL_TTC_FAIBLES:
        if mot in source_norm:
            score += 3
    for mot in MOTS_MONTANT_A_EXCLURE:
        if mot in source_norm:
            score -= 8
    if "total" in source_norm and "ttc" in source_norm:
        score += 5
    if "offre" in source_norm and "ttc" in source_norm:
        score += 5
    return score


def extraire_prix_financier_structure(texte_dossier):
    candidats = []
    for match in MONTANT_PATTERN.finditer(texte_dossier or ""):
        montant = _nettoyer_nombre(match.group("montant"))
        if montant is None or montant <= 0:
            continue

        source = _fenetre_source(texte_dossier, match.start(), match.end())
        ligne = _ligne_source(texte_dossier, match.start(), match.end())
        score = _score_montant_financier(ligne) + max(0, _score_montant_financier(source) // 3)
        candidats.append(
            {
                "brut": match.group(0).strip(),
                "valeur": montant,
                "devise": _devise_normalisee(match.group("devise") or ""),
                "source": source,
                "ligne": ligne,
                "score": score,
            }
        )

    candidats_pertinents = [c for c in candidats if c["score"] > 0]
    if not candidats_pertinents:
        logger.info("[PRIX] valeur brute : Non trouve")
        logger.info("[PRIX] valeur normalisee : Non trouve")
        logger.info("[PRIX] source : Aucun total TTC explicite")
        logger.info("[PRIX] statut : incertain")
        return {
            "statut": "incertain",
            "brut": "",
            "valeur": None,
            "devise": "",
            "source": "Aucun montant total TTC explicite identifie.",
            "justification": "Prix financier introuvable ou non explicitement total TTC.",
            "candidats": candidats[:10],
        }

    candidats_pertinents.sort(key=lambda c: (c["score"], c["valeur"]), reverse=True)
    meilleur = candidats_pertinents[0]
    concurrents_forts = [
        c
        for c in candidats_pertinents[1:]
        if c["score"] >= meilleur["score"] - 2 and c["valeur"] != meilleur["valeur"]
    ]

    statut = "present"
    justification = "Montant total TTC explicite identifie dans le dossier."
    if meilleur["score"] < 8 or concurrents_forts:
        statut = "incertain"
        justification = "Plusieurs montants TTC possibles ou contexte insuffisamment explicite."

    logger.info("[PRIX] valeur brute : %s", meilleur["brut"])
    logger.info("[PRIX] valeur normalisee : %s", meilleur["valeur"])
    logger.debug("[PRIX] source : %s", meilleur["source"][:500])
    logger.info("[PRIX] statut : %s", statut)

    return {
        "statut": statut,
        "brut": meilleur["brut"],
        "valeur": meilleur["valeur"],
        "devise": meilleur["devise"] or "XOF",
        "source": meilleur["source"],
        "justification": justification,
        "candidats": candidats_pertinents[:10],
    }


def _extraire_finances(texte_dossier):
    contexte = texte_dossier[:18000]
    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {"role": "system", "content": FINANCE_PROMPT},
            {"role": "user", "content": f"Dossier candidat:\n\n{contexte}"},
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )
    resultat = json.loads(response.choices[0].message.content)
    return resultat


def _extraire_par_critere(soumissionnaire, critere, texte_dossier):
    prompt_system = f"""
{SYSTEM_PROMPT_BASE}

Critere :
{critere.libelle}

Categorie :
{critere.categorie}
"""

    texte = contexte_ia_pour_critere(critere.libelle, texte_dossier)

    logger.debug("[RAG] critere : %s", critere.libelle)
    logger.debug("[RAG] contexte_envoye_caracteres : %s", len(texte))
    logger.debug("[RAG] apercu : %s", texte[:600].replace("\n", " | "))

    response = client.chat.completions.create(
        model="openai/gpt-oss-120b",
        messages=[
            {
                "role": "system",
                "content": prompt_system,
            },
            {
                "role": "user",
                "content": f"""
Dossier du candidat :
{soumissionnaire.nom_entreprise}

Critere a verifier dans son integralite :
{critere.libelle}

Passages du dossier a utiliser :
{texte}
""",
            },
        ],
        response_format={"type": "json_object"},
        temperature=0.0,
    )

    contenu = response.choices[0].message.content
    logger.debug("[IA] reponse_brute : %s", contenu)
    resultat = json.loads(contenu)
    resultat["valeur"] = _normaliser_valeur_ia(resultat.get("valeur"))
    return resultat


def extraire_donnees_candidat(soumissionnaire, texte_dossier=None):
    """
    Extrait uniquement les criteres enfants valides de la grille.
    Les criteres parents ne sont pas envoyes au modele IA.
    """
    if texte_dossier is None:
        texte_dossier = soumissionnaire.texte_extrait

    if not isinstance(texte_dossier, str) or not texte_dossier.strip():
        raise ValueError(
            f"Aucun texte extrait pour {soumissionnaire.nom_entreprise}. "
            "Lance d'abord l'extraction PDF et sauvegarde soumissionnaire.texte_extrait."
        )

    logger.info("[OCR] texte_trouve : %s", "oui" if texte_dossier.strip() else "non")
    logger.info("[OCR] taille_texte : %s", len(texte_dossier))
    _enregistrer_infos_entreprise(soumissionnaire, texte_dossier)

    criteres = soumissionnaire.appel_offre.criteres.filter(
        valide=True,
        parent__isnull=False,
    ).order_by("id")

    if not criteres.exists():
        logger.warning("[candidats] Aucun critere valide")
        return

    logger.info(
        "[candidats] Extraction pour %s : %s criteres",
        soumissionnaire.nom_entreprise,
        criteres.count(),
    )

    DonneeExtraite.objects.filter(soumissionnaire=soumissionnaire).delete()

    for critere in criteres:
        try:
            if _est_critere_prix_financier(critere):
                logger.debug(
                    "[PRIX] critere financier ignore par l'extraction Present/Absent : %s",
                    critere.libelle,
                )
                continue

            logger.debug("[candidats] Traitement : %s", critere.libelle)
            resultat = _extraire_par_critere(soumissionnaire, critere, texte_dossier)
            valeur = resultat.get("valeur") or "Incertain"
            justification = resultat.get("justification") or ""

            DonneeExtraite.objects.create(
                soumissionnaire=soumissionnaire,
                critere=critere,
                valeur_extraite=valeur,
                justification_ia=justification,
            )

            logger.debug("[candidats] %s", valeur[:50])

        except Exception as e:
            logger.exception("[candidats] Erreur : %s - %s", critere.libelle, e)

    prix_structure = extraire_prix_financier_structure(texte_dossier)

    try:
        finances = _extraire_finances(texte_dossier)
    except Exception as e:
        logger.exception("[candidats] Extraction financiere IA impossible : %s", e)
        finances = {}

    montant_ht = _nettoyer_nombre(finances.get("montant_ht"))
    montant_ttc_ia = _nettoyer_nombre(finances.get("montant_ttc"))
    etat_finance_ia = _normaliser_valeur_ia(finances.get("etat"))
    justification_finance_ia = finances.get("justification") or ""

    prix_propose = prix_structure["valeur"] if prix_structure["statut"] == "present" else None
    devise = prix_structure["devise"] or _devise_normalisee(finances.get("devise") or "")

    soumissionnaire.prix_financier_brut = prix_structure["brut"]
    soumissionnaire.prix_financier_devise = devise
    soumissionnaire.prix_financier_source = prix_structure["source"]
    soumissionnaire.prix_financier_statut = prix_structure["statut"]
    soumissionnaire.prix_financier_validation_humaine = False

    if prix_propose is not None:
        soumissionnaire.prix_lu_publiquement = prix_propose
        soumissionnaire.prix_corrige = prix_propose
    else:
        soumissionnaire.prix_lu_publiquement = None
        soumissionnaire.prix_corrige = None

    soumissionnaire.save(
        update_fields=[
            "prix_lu_publiquement",
            "prix_corrige",
            "prix_financier_brut",
            "prix_financier_devise",
            "prix_financier_source",
            "prix_financier_statut",
            "prix_financier_validation_humaine",
        ]
    )

    finance_resume = [
        f"Prix propose: {prix_propose if prix_propose is not None else 'Non trouve'}",
        f"Devise: {devise or 'Non precise'}",
        f"Montant HT: {montant_ht if montant_ht is not None else 'Non trouve'}",
        f"Montant TTC IA: {montant_ttc_ia if montant_ttc_ia is not None else 'Non trouve'}",
        f"Etat IA: {etat_finance_ia}",
        f"Etat prix structure: {prix_structure['statut']}",
        f"Source prix: {prix_structure['source']}",
    ]
    if justification_finance_ia:
        finance_resume.append(f"Justification IA: {justification_finance_ia}")
    finance_resume.append(f"Justification prix: {prix_structure['justification']}")

    if soumissionnaire.donnees_extraites.exists():
        premiere_donnee = soumissionnaire.donnees_extraites.order_by("id").first()
        premiere_donnee.justification_ia = (
            f"{premiere_donnee.justification_ia}\n\n"
            + "\n".join(finance_resume)
        ).strip()
        premiere_donnee.save(update_fields=["justification_ia"])

    for critere_prix in [critere for critere in criteres if _est_critere_prix_financier(critere)]:
        valeur_prix = (
            f"{prix_structure['valeur']} {devise or 'XOF'}"
            if prix_structure["valeur"] is not None
            else "Incertain"
        )
        DonneeExtraite.objects.create(
            soumissionnaire=soumissionnaire,
            critere=critere_prix,
            valeur_extraite=valeur_prix,
            justification_ia="\n".join(finance_resume),
        )

    logger.info("[candidats] Termine pour %s", soumissionnaire.nom_entreprise)
