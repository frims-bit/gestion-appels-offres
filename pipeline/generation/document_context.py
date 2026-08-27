from decimal import Decimal, InvalidOperation

from appels_offres.models import CritereGrille
from soumissionnaires.models import DonneeExtraite, Score, Soumissionnaire


NON_RENSEIGNE = "Non renseigne"
TIRET = "-"


def texte(value, default=NON_RENSEIGNE):
    return value if value not in (None, "") else default


def texte_pv(value):
    return value if value not in (None, "") else ""


def format_date(value, with_time=False):
    if value is None:
        return NON_RENSEIGNE
    if isinstance(value, str):
        return value
    fmt = "%d/%m/%Y %H:%M" if with_time else "%d/%m/%Y"
    return value.strftime(fmt)


def format_montant(value):
    if value is None:
        return NON_RENSEIGNE
    try:
        return f"{Decimal(value):,.0f}".replace(",", " ")
    except (InvalidOperation, ValueError):
        return NON_RENSEIGNE


def format_montant_pv(value):
    montant = format_montant(value)
    return "" if montant == NON_RENSEIGNE else montant


def _etat_valeur(value):
    normalise = str(value or "").strip().lower()
    if not normalise:
        return "Non renseigne"
    if any(mot in normalise for mot in ("absent", "non fourni", "non conforme", "rejete")):
        return "Non conforme"
    if any(mot in normalise for mot in ("incertain", "a verifier", "ambigu")):
        return "A verifier"
    if any(mot in normalise for mot in ("present", "fourni", "conforme", "valide")):
        return "Conforme"
    return "Renseigne"


def _score_total(soumissionnaire):
    notes = [score.note for score in soumissionnaire.scores.all()]
    if not notes:
        return ""
    return sum(notes)


def _score_calcule_depuis_donnees(soumissionnaire, criteres, donnees):
    total = Decimal("0")
    calculable = False
    for critere in criteres:
        note_max = Decimal(critere.note_max or 0)
        if note_max <= 0:
            continue
        donnee = donnees.get((soumissionnaire.id, critere.id))
        statut = _etat_valeur(donnee.valeur_extraite if donnee else "")
        if statut == "Conforme":
            total += note_max
            calculable = True
        elif statut == "Non conforme":
            calculable = True
    return total if calculable else ""


def _score_affichage(score):
    return "" if score in (None, "", NON_RENSEIGNE) else format_montant(score)


def build_document_context(appel_offre):
    soumissionnaires = list(
        appel_offre.soumissionnaires.prefetch_related(
            "donnees_extraites__critere",
            "scores__critere",
        ).order_by("rang", "nom_entreprise", "id")
    )
    criteres = list(
        appel_offre.criteres.filter(parent__isnull=False).select_related("parent").order_by(
            "categorie",
            "parent_id",
            "id",
        )
    )

    donnees = {
        (donnee.soumissionnaire_id, donnee.critere_id): donnee
        for donnee in DonneeExtraite.objects.filter(
            soumissionnaire__appel_offre=appel_offre,
        ).select_related("critere", "soumissionnaire")
    }
    scores = {
        (score.soumissionnaire_id, score.critere_id): score
        for score in Score.objects.filter(
            soumissionnaire__appel_offre=appel_offre,
        ).select_related("critere", "soumissionnaire")
    }

    def critere_rows(categorie=None):
        rows = []
        for soumissionnaire in soumissionnaires:
            for critere in criteres:
                if categorie and critere.categorie != categorie:
                    continue
                donnee = donnees.get((soumissionnaire.id, critere.id))
                score = scores.get((soumissionnaire.id, critere.id))
                valeur = donnee.valeur_extraite if donnee else NON_RENSEIGNE
                rows.append(
                    {
                        "soumissionnaire": soumissionnaire.nom_entreprise,
                        "critere": critere.libelle,
                        "groupe": critere.parent.libelle if critere.parent else "",
                        "categorie": critere.get_categorie_display(),
                        "valeur": texte(valeur),
                        "statut": _etat_valeur(valeur),
                        "justification": texte(donnee.justification_ia if donnee else "", TIRET),
                        "score": format_montant(score.note) if score else "",
                        "note_max": format_montant(critere.note_max),
                    }
                )
        return rows

    soumissionnaire_rows = []
    finance_rows = []
    classement_rows = []
    for soumissionnaire in soumissionnaires:
        prix = soumissionnaire.prix_corrige or soumissionnaire.prix_lu_publiquement
        score_total = _score_total(soumissionnaire)
        if score_total == "":
            score_total = _score_calcule_depuis_donnees(soumissionnaire, criteres, donnees)
        soumissionnaire_rows.append(
            {
                "nom": soumissionnaire.nom_entreprise,
                "date_depot": format_date(soumissionnaire.date_depot_dossier, True)
                if soumissionnaire.date_depot_dossier
                else format_date(soumissionnaire.date_depot),
                "statut": soumissionnaire.get_statut_conformite_display(),
                "statut_final": soumissionnaire.get_statut_final_display(),
                "rang": soumissionnaire.rang or "",
                "motif": texte(soumissionnaire.motif_rejet, TIRET),
            }
        )
        finance_rows.append(
            {
                "soumissionnaire": soumissionnaire.nom_entreprise,
                "prix_lu": format_montant_pv(soumissionnaire.prix_lu_publiquement),
                "prix_corrige": format_montant_pv(soumissionnaire.prix_corrige),
                "statut": soumissionnaire.get_statut_conformite_display(),
                "rang": soumissionnaire.rang or NON_RENSEIGNE,
            }
        )
        if soumissionnaire.rang:
            classement_rows.append(
                {
                    "rang": soumissionnaire.rang,
                    "soumissionnaire": soumissionnaire.nom_entreprise,
                    "statut": soumissionnaire.get_statut_final_display(),
                    "prix": format_montant_pv(prix),
                    "score": _score_affichage(score_total),
                }
            )

    classement_rows.sort(key=lambda row: row["rang"])
    attributaire = next(
        (
            s
            for s in soumissionnaires
            if s.statut_final == Soumissionnaire.StatutFinal.RETENU
        ),
        None,
    )

    if attributaire:
        conclusion = (
            f"Au vu des resultats enregistres, {attributaire.nom_entreprise} est l'attributaire retenu."
        )
    elif classement_rows:
        conclusion = "Un classement existe, mais aucun attributaire retenu n'est enregistre."
    else:
        conclusion = "Les informations disponibles ne permettent pas d'etablir une recommandation finale."

    return {
        "appel_offre": {
            "reference": texte(appel_offre.reference),
            "objet": texte(appel_offre.titre),
            "date_publication": format_date(appel_offre.date_publication),
            "statut": appel_offre.get_statut_display(),
            "autorite_contractante": NON_RENSEIGNE,
        },
        "soumissionnaires": soumissionnaire_rows,
        "recevabilite": critere_rows(CritereGrille.Categorie.ADMINISTRATIF),
        "technique": critere_rows(CritereGrille.Categorie.TECHNIQUE),
        "financier": finance_rows,
        "classement": classement_rows,
        "attributaire": {
            "nom": attributaire.nom_entreprise,
            "adresse": texte(attributaire.adresse),
            "telephone": texte(attributaire.telephone),
            "email": texte(attributaire.email),
            "beneficiaires_effectifs": texte(attributaire.beneficiaires_effectifs),
            "nationalite": texte(attributaire.nationalite),
            "prix": format_montant(attributaire.prix_corrige or attributaire.prix_lu_publiquement),
        }
        if attributaire
        else None,
        "conclusion": conclusion,
        "annexe_technique": [
            {
                "numero": index,
                "designation": row["critere"],
                "minimum": row["critere"],
                "propose": row["valeur"],
                "conformite": row["statut"],
                "soumissionnaire": row["soumissionnaire"],
            }
            for index, row in enumerate(critere_rows(CritereGrille.Categorie.TECHNIQUE), start=1)
        ],
    }
