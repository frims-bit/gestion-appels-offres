import os
import logging

from django.conf import settings
from docx import Document

from historique.models import HistoriqueAction
from pipeline.generation.document_context import build_document_context
from pipeline.generation.docx_preview import build_preview_docx, clear_document_body
from pipeline.generation.report_paths import chemin_rapport, nom_fichier_rapport
from proces_verbaux.models import ProcesVerbal
from soumissionnaires.models import Soumissionnaire

logger = logging.getLogger(__name__)


def verifier_preconditions_rapport(appel_offre):
    pv = getattr(appel_offre, "proces_verbal", None)
    if pv is None:
        return False, "Impossible de generer le rapport : aucun proces-verbal n'est associe a cet appel d'offres."
    if pv.statut != ProcesVerbal.Statut.VALIDE:
        return False, "Impossible de generer le rapport : le PV doit etre valide et signe."

    soumissionnaires = list(appel_offre.soumissionnaires.all())
    if not soumissionnaires:
        return False, "Impossible de generer le rapport : aucun soumissionnaire n'est present."

    if not appel_offre.criteres.exists():
        return False, "Impossible de generer le rapport : aucune grille d'evaluation n'est enregistree."

    if not any(s.statut_final == Soumissionnaire.StatutFinal.RETENU for s in soumissionnaires):
        return False, "Impossible de generer le rapport : aucun attributaire RETENU n'est disponible."

    if not any(s.rang is not None for s in soumissionnaires):
        return False, "Impossible de generer le rapport : aucun classement final n'est disponible."

    return True, ""


def generer_rapport(appel_offre, utilisateur=None):
    est_ok, raison = verifier_preconditions_rapport(appel_offre)
    if not est_ok:
        raise ValueError(raison)

    pv = getattr(appel_offre, "proces_verbal", None)

    context = build_document_context(appel_offre)
    template_path = os.path.join(settings.BASE_DIR, "templates_docx", "Temp_Rapport_evaluation.docx")
    if not os.path.exists(template_path):
        raise FileNotFoundError("Template Word du rapport introuvable.")

    document = Document(template_path)
    clear_document_body(document)
    build_preview_docx(document, context, "rapport")

    full_path = chemin_rapport(appel_offre.reference)
    os.makedirs(os.path.dirname(full_path), exist_ok=True)
    document.save(full_path)
    if not os.path.exists(full_path):
        logger.error(
            "Rapport non cree apres sauvegarde. reference=%s expected_path=%s",
            appel_offre.reference,
            full_path,
        )
        raise IOError(f"Le fichier rapport attendu n'a pas ete cree: {full_path}")
    if os.path.getsize(full_path) <= 0:
        raise IOError(f"Le fichier rapport cree est vide: {full_path}")
    Document(full_path)

    HistoriqueAction.objects.create(
        utilisateur=utilisateur if getattr(utilisateur, "is_authenticated", False) else None,
        appel_offre=appel_offre,
        action="Generation du rapport",
        details=f"Rapport genere a partir du PV valide #{pv.id}.",
    )
    logger.info("Rapport genere avec succes. reference=%s path=%s", appel_offre.reference, full_path)
    return full_path
