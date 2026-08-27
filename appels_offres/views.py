from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.conf import settings
from django.http import JsonResponse, HttpResponseForbidden
from django.db.models import Count
import os
import logging

from .models import AppelOffre, CritereGrille
from soumissionnaires.models import Soumissionnaire
from proces_verbaux.models import ProcesVerbal
from pipeline.extraction.lecture import extraire_texte
from pipeline.extraction.grille import generer_grille, ajouter_manuellement

logger = logging.getLogger(__name__)


def _refuser_acces(request, redirect_to="home"):
    messages.error(
        request,
        "Accès refusé : vous ne disposez pas des droits nécessaires pour effectuer cette action.",
    )
    return redirect(redirect_to)


def _dashboard_context():
    derniers_ao = AppelOffre.objects.all().order_by('created_at', 'id')[:5]
    pvs_a_signer = ProcesVerbal.objects.filter(
        statut=ProcesVerbal.Statut.BROUILLON
    ).select_related("appel_offre").order_by("-date_generation")
    return {
        'total_ao': AppelOffre.objects.count(),
        'total_soumissionnaires': Soumissionnaire.objects.count(),
        'total_pv': ProcesVerbal.objects.count(),
        'total_valides': ProcesVerbal.objects.filter(
            statut=ProcesVerbal.Statut.VALIDE
        ).count(),
        'total_pv_a_signer': pvs_a_signer.count(),
        'pvs_a_signer': pvs_a_signer[:5],
        'derniers_ao': derniers_ao,
    }


def home(request):
    """Point d'entree : redirige vers login ou dashboard selon le role."""
    if not request.user.is_authenticated:
        return redirect("login")
    if request.user.role == "secretaire":
        return redirect("dashboard_secretaire")
    if request.user.role == "evaluateur":
        return redirect("dashboard_evaluateur")
    if request.user.role == "president":
        return redirect("dashboard_president")
    return redirect("liste_ao")


@login_required
def dashboard_evaluateur(request):
    if request.user.role != "evaluateur":
        return _refuser_acces(request, "home")
    return render(request, 'appels_offres/home.html', _dashboard_context())


@login_required
def dashboard_president(request):
    if request.user.role != "president":
        return _refuser_acces(request, "home")
    return render(request, 'appels_offres/home.html', _dashboard_context())


@login_required
def dashboard_secretaire(request):
    """Tableau de bord dedie au role secretaire."""
    if request.user.role != "secretaire":
        return _refuser_acces(request, "home")

    appels_actifs = AppelOffre.objects.exclude(statut=AppelOffre.Statut.CLOTURE)
    dossiers = Soumissionnaire.objects.select_related("appel_offre")
    dossiers_a_traiter = dossiers.filter(
        statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER
    )
    appels_recents = (
        AppelOffre.objects.annotate(nombre_soumissionnaires=Count("soumissionnaires"))
        .order_by("created_at", "id")[:5]
    )
    ao_disponibles = [
        ao
        for ao in AppelOffre.objects.exclude(statut=AppelOffre.Statut.CLOTURE)
        .annotate(nombre_soumissionnaires=Count("soumissionnaires"))
        .prefetch_related("criteres")
        .order_by("created_at", "id")
        if ao.grille_evaluee
        and ao.statut in [AppelOffre.Statut.EN_COURS, AppelOffre.Statut.JUGE]
    ]
    derniers_dossiers = dossiers.order_by("-date_depot_dossier", "-date_depot", "-id")[:5]

    return render(
        request,
        "appels_offres/dashboard_secretaire.html",
        {
            "total_appels_actifs": appels_actifs.count(),
            "total_dossiers_recus": dossiers.count(),
            "total_dossiers_a_traiter": dossiers_a_traiter.count(),
            "total_appels_clotures": AppelOffre.objects.filter(
                statut=AppelOffre.Statut.CLOTURE
            ).count(),
            "appels_recents": appels_recents,
            "ao_disponibles": ao_disponibles,
            "derniers_dossiers": derniers_dossiers,
            "peut_importer_ao": True,
        },
    )


@login_required
def upload_ao(request):
    """Upload du cahier des charges avec extraction automatique"""
    # Seul le secrétaire peut uploader
    if request.user.role != 'secretaire':
        return _refuser_acces(request, "home")

    if request.method == 'POST':
        reference = request.POST.get('reference')
        titre = request.POST.get('titre')
        date_publication = request.POST.get('date_publication')
        fichier = request.FILES.get('fichier')

        if not all([reference, titre, date_publication, fichier]):
            messages.error(request, "Tous les champs sont obligatoires.")
            return redirect('upload_ao')

        if AppelOffre.objects.filter(reference=reference).exists():
            messages.error(request, f"❌ La référence '{reference}' existe déjà.")
            return render(request, 'appels_offres/upload_ao.html')

        # Création de l'appel d'offres
        ao = AppelOffre.objects.create(
            reference=reference,
            titre=titre,
            date_publication=date_publication,
            statut=AppelOffre.Statut.GRILLE_EN_ATTENTE
        )

        # Sauvegarde du fichier
        chemin_fichier = default_storage.save(
            f"cahiers_des_charges/{fichier.name}",
            ContentFile(fichier.read())
        )
        ao.document_source = chemin_fichier
        ao.save()

        # Extraction du texte
        chemin_complet = os.path.join(settings.MEDIA_ROOT, chemin_fichier)
        texte = extraire_texte(chemin_complet)
        ao.texte_extrait = texte
        ao.save()

        if not texte or len(texte.strip()) < 50:
            messages.warning(request, "Upload enregistre. Le cahier des charges a ete transmis pour evaluation.")
            return redirect('liste_ao')

        # Extraction des critères
        elements = generer_grille(ao, texte)

        if elements:
            messages.success(
                request,
                "Upload reussi. Le cahier des charges a ete enregistre et transmis pour evaluation."
            )
            return redirect('liste_ao')
        else:
            messages.warning(request, "Upload enregistre. Le cahier des charges a ete transmis pour evaluation.")
            return redirect('liste_ao')

    return render(request, 'appels_offres/upload_ao.html')


@login_required
def liste_ao(request):
    """Liste des appels d'offres (avec filtres et pagination)"""
    appels_offres = AppelOffre.objects.all().order_by('created_at', 'id')

    # Filtrage par statut
    statut_filter = request.GET.get('statut')
    if statut_filter and statut_filter != 'tous':
        appels_offres = appels_offres.filter(statut=statut_filter)

    # Pagination (5 par page)
    from django.core.paginator import Paginator
    paginator = Paginator(appels_offres, 5)
    page_number = request.GET.get('page')
    page_obj = paginator.get_page(page_number)

    return render(request, 'appels_offres/liste_ao.html', {
        'appels_offres': page_obj,
        'statut_actuel': statut_filter,
    })


@login_required
def detail_ao(request, ao_id):
    """Détail d'un appel d'offres"""
    ao = get_object_or_404(AppelOffre, id=ao_id)
    dossiers = ao.soumissionnaires.order_by(
        "-date_depot_dossier",
        "-date_depot",
        "-id",
    )

    if request.method == "POST":
        logger.info("[GRILLE] Validation demandee")
        logger.info("[GRILLE] AO : %s", ao.reference)
        logger.info("[GRILLE] Utilisateur : %s", request.user.username)
        logger.info("[GRILLE] Role : %s", request.user.role)
        logger.info("[GRILLE] Nombre de criteres : %s", ao.criteres.count())
    return render(
        request,
        'appels_offres/detail_ao.html',
        {
            'appel_offre': ao,
            'dossiers': dossiers,
        },
    )


@login_required
def validation_grille(request, ao_id):
    """
    Validation de la grille avec arborescence (groupes et sous-critères)
    Réservé aux évaluateurs
    """
    # Vérification des droits
    if request.user.role != 'evaluateur':
        logger.error(
            "[GRILLE][ERROR] Acces refuse pour %s (%s)",
            request.user.username,
            getattr(request.user, "role", ""),
        )
        return HttpResponseForbidden("Acces refuse")

    ao = get_object_or_404(AppelOffre, id=ao_id)

    # Ajout manuel d'un groupe ou sous-critère
    if request.method == 'POST' and request.POST.get('action') == 'ajouter_manuel':
        libelle = request.POST.get('libelle')
        categorie = request.POST.get('categorie')
        type_groupe = request.POST.get('type_groupe')
        note_max = request.POST.get('note_max')
        parent_id = request.POST.get('parent_id')

        if libelle and categorie and type_groupe:
            parent = None
            if parent_id:
                parent = get_object_or_404(CritereGrille, id=parent_id)

            ajouter_manuellement(ao, libelle, categorie, type_groupe, float(note_max or 0), parent)

            if parent:
                messages.success(request, f"✅ Sous-critère '{libelle}' ajouté avec succès.")
            else:
                messages.success(request, f"✅ Groupe '{libelle}' ajouté avec succès.")
        else:
            messages.error(request, "Tous les champs sont obligatoires.")
        return redirect('validation_grille', ao_id=ao_id)

    if request.method == 'POST' and request.POST.get('action') == 'valider_groupe':
        groupe_id = request.POST.get("groupe_id")
        groupe = get_object_or_404(CritereGrille, id=groupe_id, appel_offre=ao, parent__isnull=True)
        groupe.valide = True
        groupe.save(update_fields=["valide"])
        groupe.sous_criteres.update(valide=True)
        messages.success(request, "Groupe validé avec succès.")
        return redirect('validation_grille', ao_id=ao_id)

    # Validation globale de la grille: on ne bascule l'AO que via le bouton final.
    action = request.POST.get("action")
    validation_post_legacy = request.method == 'POST' and any(
        cle.startswith("critere_") and cle != "critere_id"
        for cle in request.POST.keys()
    )

    if request.method == 'POST' and (action == "valider_grille" or validation_post_legacy):
        groupes = ao.criteres.filter(parent=None).prefetch_related("sous_criteres")
        criteres = ao.criteres.all()
        if groupes.exists():
            criteres.update(valide=True)
            ao.statut = AppelOffre.Statut.EN_COURS
            ao.save(update_fields=["statut"])
            logger.info("[GRILLE] AO %s valide par %s", ao.reference, request.user.username)
            messages.success(request, "Grille d'évaluation validée avec succès.")
            return redirect('detail_ao', ao_id=ao_id)
        messages.error(request, "La grille contient encore des critères non validés.")
        return redirect('validation_grille', ao_id=ao_id)

    if request.method == 'POST' and request.POST.get('action') == 'appliquer_critere':
        critere_id = request.POST.get("critere_id")
        critere = get_object_or_404(CritereGrille, id=critere_id, appel_offre=ao)
        critere.libelle = request.POST.get("libelle", critere.libelle)
        critere.categorie = request.POST.get("categorie", critere.categorie)
        critere.type_groupe = request.POST.get("type_groupe", critere.type_groupe)
        critere.parent_id = request.POST.get("parent_id") or critere.parent_id
        note_max = request.POST.get("note_max")
        if note_max is not None and note_max != "":
            try:
                critere.note_max = float(note_max)
            except ValueError:
                messages.error(request, "Le poids / coefficient est invalide.")
                return redirect('validation_grille', ao_id=ao_id)
        critere.save()
        messages.success(request, "Critère modifié avec succès.")
        return redirect('validation_grille', ao_id=ao_id)

    groupes = list(ao.criteres.filter(parent=None).prefetch_related('sous_criteres'))
    groupes_par_categorie = [
        {
            "code": CritereGrille.Categorie.ADMINISTRATIF,
            "label": "RECEVABILITE",
            "groupes": [
                groupe for groupe in groupes
                if groupe.categorie == CritereGrille.Categorie.ADMINISTRATIF
            ],
        },
        {
            "code": CritereGrille.Categorie.TECHNIQUE,
            "label": "CAPACITE TECHNIQUE",
            "groupes": [
                groupe for groupe in groupes
                if groupe.categorie == CritereGrille.Categorie.TECHNIQUE
            ],
        },
        {
            "code": CritereGrille.Categorie.FINANCIER,
            "label": "CAPACITE FINANCIERE",
            "groupes": [
                groupe for groupe in groupes
                if groupe.categorie == CritereGrille.Categorie.FINANCIER
            ],
        },
    ]

    return render(request, 'appels_offres/validation_grille.html', {
        'appel_offre': ao,
        'groupes': groupes,
        'groupes_par_categorie': groupes_par_categorie,
        'criteres_tous': ao.criteres.select_related("parent").all(),
    })


@login_required
def supprimer_critere(request):
    """Supprime un critere (groupe ou sous-critere)."""
    if request.user.role != 'evaluateur':
        messages.error(request, "Vous n'avez pas les droits pour supprimer des criteres.")
        if request.user.role == "secretaire":
            return redirect("dashboard_secretaire")
        return redirect('home')

    if request.method == 'POST':
        critere_id = (
            request.POST.get('critere_id')
            or request.POST.get('critere_id_hidden')
            or request.POST.get('critere_id_input')
        )
        if critere_id:
            critere = get_object_or_404(CritereGrille, id=critere_id)
            ao_id = critere.appel_offre.id
            nom = critere.libelle
            critere.delete()
            messages.success(request, f"Critere '{nom}' supprime avec succes.")
            return redirect('validation_grille', ao_id=ao_id)

    messages.error(request, "Suppression impossible : identifiant de critere manquant.")
    return redirect('home')


@login_required
def modifier_critere(request):
    """Modifie un critere (groupe ou sous-critere)."""
    if request.user.role != 'evaluateur':
        return JsonResponse({'error': 'Droits insuffisants'}, status=403)

    if request.method == 'POST':
        critere_id = (
            request.POST.get('critere_id')
            or request.POST.get('critere_id_hidden')
            or request.POST.get('critere_id_input')
        )
        libelle = request.POST.get('libelle')
        categorie = request.POST.get('categorie')
        type_groupe = request.POST.get('type_groupe')
        note_max = request.POST.get('note_max')

        if critere_id and not libelle:
            libelle = request.POST.get(f'libelle_{critere_id}')
            categorie = request.POST.get(f'categorie_{critere_id}') or categorie
            type_groupe = request.POST.get(f'type_groupe_{critere_id}') or type_groupe
            note_max = request.POST.get(f'note_max_{critere_id}') or note_max

        if critere_id and not libelle:
            critere = get_object_or_404(CritereGrille, id=critere_id)
            libelle = critere.libelle
            categorie = categorie or critere.categorie
            type_groupe = type_groupe or critere.type_groupe
            note_max = note_max or critere.note_max

        if critere_id and libelle:
            critere = get_object_or_404(CritereGrille, id=critere_id)
            critere.libelle = libelle
            if categorie:
                critere.categorie = categorie
            if type_groupe:
                critere.type_groupe = type_groupe
            if note_max:
                critere.note_max = float(note_max)
            critere.save()
            messages.success(request, f"Critere '{libelle}' modifie avec succes.")
            return redirect('validation_grille', ao_id=critere.appel_offre.id)

    messages.error(request, "Modification impossible : donnees incompletes.")
    return redirect('home')

