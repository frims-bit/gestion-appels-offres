import re
import unicodedata
import logging

from appels_offres.models import CritereGrille
from soumissionnaires.models import Soumissionnaire

logger = logging.getLogger(__name__)


VALEURS_ABSENTES = (
    "absent",
    "inexistant",
    "manquant",
    "manquante",
    "introuvable",
    "non trouve",
    "non trouvé",
    "non retrouve",
    "non retrouvé",
)

VALEURS_INCERTAINES = (
    "incertain",
    "a verifier",
    "à vérifier",
    "non verifie",
    "non vérifié",
    "non fourni",
    "non fournie",
)

VALEURS_PRESENTES = (
    "present",
    "présent",
    "fourni",
    "fournie",
    "conforme",
    "confirmé",
    "confirmee",
)

VALEURS_NON_CONFORMES = (
    "non conforme",
    "incomplet",
    "incomplete",
    "insuffisant",
    "insuffisante",
)


def _normaliser(texte):
    texte = str(texte or "").lower()
    texte = unicodedata.normalize("NFD", texte)
    texte = "".join(
        caractere
        for caractere in texte
        if unicodedata.category(caractere) != "Mn"
    )
    return re.sub(r"\s+", " ", texte).strip()


def _criteres_valides(appel_offre, categorie=None, eliminatoire=None):
    criteres = appel_offre.criteres.filter(
        valide=True,
        parent__isnull=False,
    )

    if categorie:
        criteres = criteres.filter(categorie=categorie)

    if eliminatoire is not None:
        type_groupe = (
            CritereGrille.TypeGroupe.ELIMINATOIRE
            if eliminatoire
            else CritereGrille.TypeGroupe.NOTABLE
        )
        criteres = criteres.filter(type_groupe=type_groupe)

    return criteres.order_by("id")


def _contient_un(texte, motifs):
    texte_normalise = _normaliser(texte)
    return any(_normaliser(motif) in texte_normalise for motif in motifs)


def _est_critere_visuel(libelle):
    texte = _normaliser(libelle)
    return any(
        mot in texte
        for mot in ("signature", "cachet", "paraphe", "signe", "signee", "cachete")
    )


def _preuve_visuelle_non_analysee(justification, libelle_critere):
    texte = _normaliser(f"{justification} {libelle_critere}")
    indices = (
        "signature/cachet non detecte",
        "signature cachet non detecte",
        "aucune preuve visuelle",
        "preuve visuelle non analysee",
        "preuve visuelle non analysée",
        "ocr seul",
        "pas de preuve visuelle",
        "non detecte",
        "non analysé",
        "non analysée",
    )
    return _est_critere_visuel(libelle_critere) and any(indice in texte for indice in indices)


def _etat_donnee(donnee):
    valeur = donnee.valeur_extraite or ""
    justification = donnee.justification_ia or ""
    valeur_normalisee = _normaliser(valeur)
    justification_normalisee = _normaliser(justification)

    valeur_absente = _contient_un(valeur, VALEURS_ABSENTES)
    valeur_incertaine = _contient_un(valeur, VALEURS_INCERTAINES)
    valeur_presente = _contient_un(valeur, VALEURS_PRESENTES)
    valeur_non_conforme = _contient_un(valeur, VALEURS_NON_CONFORMES)
    justification_presente = _contient_un(justification, VALEURS_PRESENTES)
    justification_absente = _contient_un(justification, VALEURS_ABSENTES)
    justification_incertaine = _contient_un(justification, VALEURS_INCERTAINES)

    if not valeur_normalisee and not justification_normalisee:
        return "incertain", "valeur extraite vide"

    if (
        "decision humaine" in justification_normalisee
        or "correction erreur ia" in justification_normalisee
    ):
        if valeur_presente:
            return "present", valeur
        if valeur_absente:
            return "absent", valeur

    if valeur_incertaine or justification_incertaine:
        return "incertain", valeur or justification

    if _preuve_visuelle_non_analysee(justification, donnee.critere.libelle):
        return "incertain", "preuve visuelle non analysée : incertain"

    if valeur_absente and (justification_absente or "absence" in justification_normalisee):
        return "absent", valeur

    if valeur_absente and justification_presente:
        return (
            "incertain",
            "valeur absente mais justification indiquant une présence",
        )

    if valeur_absente and (
        "fourn" in justification_normalisee
        or "present" in justification_normalisee
        or "confirm" in justification_normalisee
    ):
        return "incertain", "valeur absente mais justification contradictoire"

    if valeur_non_conforme:
        return "incertain", valeur

    if valeur_presente or justification_presente:
        return "present", valeur

    if "non fourni" in justification_normalisee or "aucun" in justification_normalisee:
        return "incertain", valeur or justification

    return "incertain", valeur


def _donnee_par_critere(soumissionnaire):
    return {
        donnee.critere_id: donnee
        for donnee in soumissionnaire.donnees_extraites.select_related("critere")
    }


def _motif(titre, lignes):
    return titre + "\n\n" + "\n".join(
        f"- {libelle} : {raison}" for libelle, raison in lignes
    )


def reinitialiser_resultats(appel_offre):
    for soumissionnaire in appel_offre.soumissionnaires.all():
        if soumissionnaire.statut_conformite == Soumissionnaire.StatutConformite.A_VERIFIER:
            soumissionnaire.rang = None
            soumissionnaire.statut_final = Soumissionnaire.StatutFinal.EN_COURS
        else:
            soumissionnaire.rang = None
            soumissionnaire.statut_final = (
                Soumissionnaire.StatutFinal.ECARTE
                if soumissionnaire.statut_conformite
                in {
                    Soumissionnaire.StatutConformite.NON_RECEVABLE,
                    Soumissionnaire.StatutConformite.NON_CONFORME_TECHNIQUE,
                }
                else Soumissionnaire.StatutFinal.EN_COURS
            )
        soumissionnaire.save()


def etape_1_recevabilite(soumissionnaire):
    logger.info("[SCORING] Analyse des criteres administratifs : %s", soumissionnaire.nom_entreprise)

    if soumissionnaire.statut_conformite in {
        Soumissionnaire.StatutConformite.RECEVABLE,
        Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
        Soumissionnaire.StatutConformite.NON_RECEVABLE,
        Soumissionnaire.StatutConformite.NON_CONFORME_TECHNIQUE,
    }:
        return soumissionnaire

    criteres = _criteres_valides(
        soumissionnaire.appel_offre,
        categorie=CritereGrille.Categorie.ADMINISTRATIF,
    )
    donnees = _donnee_par_critere(soumissionnaire)
    absents = []
    incertains = []

    for critere in criteres:
        donnee = donnees.get(critere.id)
        if donnee is None:
            incertains.append((critere.libelle, "donnee non extraite"))
            continue

        etat, raison = _etat_donnee(donnee)
        if etat == "absent":
            absents.append((critere.libelle, raison))
        elif etat == "incertain":
            incertains.append((critere.libelle, raison))

    if absents:
        soumissionnaire.statut_conformite = (
            Soumissionnaire.StatutConformite.NON_RECEVABLE
        )
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.ECARTE
        soumissionnaire.motif_rejet = _motif("Candidat non recevable :", absents)
        soumissionnaire.rang = None
    elif incertains:
        soumissionnaire.statut_conformite = (
            Soumissionnaire.StatutConformite.A_VERIFIER
        )
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.EN_COURS
        soumissionnaire.motif_rejet = _motif(
            "Recevabilite administrative a verifier manuellement :",
            incertains,
        )
        soumissionnaire.rang = None
    else:
        soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.RECEVABLE
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.EN_COURS
        soumissionnaire.motif_rejet = None

    soumissionnaire.save()
    logger.info(
        "[SCORING] Criteres administratifs : %s",
        soumissionnaire.statut_conformite,
    )
    return soumissionnaire


def etape_2_conformite_technique(soumissionnaire):
    logger.info("[SCORING] Analyse des criteres techniques : %s", soumissionnaire.nom_entreprise)

    if soumissionnaire.statut_conformite in {
        Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
        Soumissionnaire.StatutConformite.NON_RECEVABLE,
        Soumissionnaire.StatutConformite.NON_CONFORME_TECHNIQUE,
    }:
        return soumissionnaire

    if soumissionnaire.statut_conformite != Soumissionnaire.StatutConformite.RECEVABLE:
        return soumissionnaire

    criteres = _criteres_valides(
        soumissionnaire.appel_offre,
        categorie=CritereGrille.Categorie.TECHNIQUE,
        eliminatoire=True,
    )
    if not criteres.exists():
        criteres = _criteres_valides(
            soumissionnaire.appel_offre,
            categorie=CritereGrille.Categorie.TECHNIQUE,
        )

    donnees = _donnee_par_critere(soumissionnaire)
    absents = []
    incertains = []

    for critere in criteres:
        donnee = donnees.get(critere.id)
        if donnee is None:
            incertains.append((critere.libelle, "donnee non extraite"))
            continue

        etat, raison = _etat_donnee(donnee)
        if etat == "absent":
            absents.append((critere.libelle, raison))
        elif etat == "incertain":
            incertains.append((critere.libelle, raison))

    if absents:
        soumissionnaire.statut_conformite = (
            Soumissionnaire.StatutConformite.NON_CONFORME_TECHNIQUE
        )
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.ECARTE
        soumissionnaire.motif_rejet = _motif(
            "Candidat non conforme techniquement :",
            absents,
        )
        soumissionnaire.rang = None
    elif incertains:
        soumissionnaire.statut_conformite = (
            Soumissionnaire.StatutConformite.A_VERIFIER
        )
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.EN_COURS
        soumissionnaire.motif_rejet = _motif(
            "Conformite technique a verifier manuellement :",
            incertains,
        )
        soumissionnaire.rang = None
    else:
        soumissionnaire.statut_conformite = (
            Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE
        )
        soumissionnaire.statut_final = Soumissionnaire.StatutFinal.EN_COURS
        soumissionnaire.motif_rejet = None

    soumissionnaire.save()
    logger.info(
        "[SCORING] Criteres techniques : %s",
        soumissionnaire.statut_conformite,
    )
    return soumissionnaire


def etape_3_evaluation_financiere(appel_offre):
    logger.info("[SCORING] Evaluation financiere...")
    for soumissionnaire in appel_offre.soumissionnaires.all():
        if soumissionnaire.statut_conformite != (
            Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE
        ):
            continue

        if soumissionnaire.prix_corrige is None and soumissionnaire.prix_lu_publiquement:
            soumissionnaire.prix_corrige = soumissionnaire.prix_lu_publiquement
            soumissionnaire.save(update_fields=["prix_corrige"])
        elif soumissionnaire.prix_corrige is None:
            soumissionnaire.motif_rejet = (
                "Evaluation financiere a completer : prix corrige absent."
            )
            soumissionnaire.save(update_fields=["motif_rejet"])
        logger.info(
            "[SCORING] Critere financier : %s",
            "conforme" if soumissionnaire.prix_corrige is not None else "a verifier",
        )


def etape_4_qualification(soumissionnaire):
    if soumissionnaire.statut_conformite not in {
        Soumissionnaire.StatutConformite.RECEVABLE,
        Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
    }:
        return False

    if not soumissionnaire.qualification_verifiee:
        soumissionnaire.qualification_verifiee = True
        soumissionnaire.qualification_conforme = True
        soumissionnaire.save(
            update_fields=["qualification_verifiee", "qualification_conforme"]
        )

    return soumissionnaire.qualification_conforme is True


def etape_5_classement(appel_offre):
    logger.info("[CLASSEMENT] Calcul du classement final...")
    appel_offre.soumissionnaires.update(rang=None)

    candidats = list(
        appel_offre.soumissionnaires.filter(
            statut_conformite__in=(
                Soumissionnaire.StatutConformite.RECEVABLE,
                Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
            ),
            qualification_conforme=True,
            prix_corrige__isnull=False,
        ).order_by("prix_corrige", "id")
    )

    for rang, candidat in enumerate(candidats, start=1):
        candidat.rang = rang
        candidat.statut_final = (
            Soumissionnaire.StatutFinal.RETENU
            if rang == 1
            else Soumissionnaire.StatutFinal.EN_COURS
        )
        candidat.save(update_fields=["rang", "statut_final"])
        logger.info(
            "[CLASSEMENT] Rang %s : %s",
            rang,
            candidat.nom_entreprise,
        )

    return candidats


def _tri_affichage(soumissionnaire):
    return (
        soumissionnaire.rang is None,
        soumissionnaire.rang or 999999,
        soumissionnaire.nom_entreprise.lower(),
    )


def cascade_complete(appel_offre):
    logger.info("[SCORING] Debut de l'evaluation : %s", appel_offre.reference)

    if not appel_offre.grille_evaluee:
        raise ValueError(
            "La grille doit etre generee et validee avant de lancer la cascade."
        )

    logger.info("[SCORING] Analyse des criteres...")
    reinitialiser_resultats(appel_offre)

    soumissionnaires = list(appel_offre.soumissionnaires.all())

    for soumissionnaire in soumissionnaires:
        logger.info("[SCORING] Evaluation du dossier : %s", soumissionnaire.nom_entreprise)
        etape_1_recevabilite(soumissionnaire)
        etape_2_conformite_technique(soumissionnaire)

    conformes = appel_offre.soumissionnaires.filter(
        statut_conformite=Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
    ).order_by("id")

    for soumissionnaire in conformes:
        etape_4_qualification(soumissionnaire)

    etape_3_evaluation_financiere(appel_offre)

    logger.info("[SCORING] Calcul du score...")
    classes = etape_5_classement(appel_offre)

    tous = list(appel_offre.soumissionnaires.all())
    tous.sort(key=_tri_affichage)

    logger.info(
        "[SCORING] Evaluation terminee : %s classe(s), %s non classe(s)",
        len(classes),
        len(tous) - len(classes),
    )

    return tous
