from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.conf import settings
from django.http import JsonResponse
from datetime import date
import os

from .models import Soumissionnaire
from appels_offres.models import AppelOffre
from pipeline.extraction.lecture import extraire_texte
from pipeline.extraction.candidats import extraire_donnees_candidat
from pipeline.scoring.cascade import (
    etape_1_recevabilite,
    etape_2_conformite_technique,
    etape_3_classement,
    etape_4_qualification
)


@login_required
def upload_dossier(request):
    """
    Upload du dossier d'un soumissionnaire + extraction des données
    Réservé aux secrétaires
    """
    # Vérification des droits
    if request.user.role != 'secretaire':
        messages.error(request, "Seul le secrétaire peut uploader des dossiers.")
        return redirect('home')

    # Récupérer les AO disponibles (en cours ou en attente de grille)
    appels_offres = AppelOffre.objects.filter(statut__in=['en_cours', 'grille_en_attente'])

    if request.method == 'POST':
        ao_id = request.POST.get('appel_offre_id')
        nom_entreprise = request.POST.get('nom_entreprise')
        prix = request.POST.get('prix')
        fichier = request.FILES.get('fichier')

        # Validation des champs
        if not all([ao_id, nom_entreprise, prix, fichier]):
            messages.error(request, "Tous les champs sont obligatoires.")
            return render(request, 'soumissionnaires/upload_dossier.html', {
                'appels_offres': appels_offres,
                'form_data': request.POST  # On garde les données pour ne pas tout retaper
            })

        ao = get_object_or_404(AppelOffre, id=ao_id)

        # Vérifier qu'il n'y a pas déjà un soumissionnaire avec ce nom pour cet AO
        if Soumissionnaire.objects.filter(appel_offre=ao, nom_entreprise=nom_entreprise).exists():
            messages.warning(request, f" Un soumissionnaire '{nom_entreprise}' existe déjà pour cet appel d'offres.")
            return render(request, 'soumissionnaires/upload_dossier.html', {
                'appels_offres': appels_offres,
                'form_data': request.POST
            })

        try:
            # Création du soumissionnaire
            soumissionnaire = Soumissionnaire.objects.create(
                appel_offre=ao,
                nom_entreprise=nom_entreprise,
                date_depot=date.today(),
                prix_lu_publiquement=prix,
                prix_corrige=prix,
                statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
                statut_final=Soumissionnaire.StatutFinal.EN_COURS,
            )

            # Sauvegarde du fichier
            chemin_fichier = default_storage.save(
                f"dossiers_candidats/{fichier.name}",
                ContentFile(fichier.read())
            )

            # Extraction du texte
            chemin_complet = os.path.join(settings.MEDIA_ROOT, chemin_fichier)
            texte = extraire_texte(chemin_complet)

            # Extraction des données par critère (si les critères sont validés)
            try:
                extraire_donnees_candidat(soumissionnaire, texte)
                messages.success(request, f" Données extraites avec succès pour {nom_entreprise}.")
            except Exception as e:
                messages.warning(request, f"Données extraites partiellement : {e}")

            # Message de succès principal
            messages.success(request, f" Dossier de {nom_entreprise} importé avec succès !")

            # Redirection vers le détail de l'AO
            return redirect('detail_ao', ao_id=ao.id)

        except Exception as e:
            messages.error(request, f"Erreur lors de l'import : {e}")
            return render(request, 'soumissionnaires/upload_dossier.html', {
                'appels_offres': appels_offres,
                'form_data': request.POST
            })

    # GET : afficher le formulaire vide
    return render(request, 'soumissionnaires/upload_dossier.html', {
        'appels_offres': appels_offres,
        'form_data': None
    })


@login_required
def classement(request, ao_id):
    """Classement des soumissionnaires pour un AO"""
    if request.user.role not in ['evaluateur', 'president']:
        messages.error(request, "Vous n'avez pas les droits pour voir ce classement.")
        return redirect('home')

    ao = get_object_or_404(AppelOffre, id=ao_id)

    # Lancer la cascade d'évaluation
    try:
        classement_list = cascade_complete(ao)

        messages.success(request, f"Évaluation terminée pour {len(classement_list)} soumissionnaires.")
    except Exception as e:
        messages.error(request, f"Erreur lors de l'évaluation : {e}")

    classement_list = ao.soumissionnaires.exclude(rang=None).order_by('rang')

    return render(request, 'soumissionnaires/classement.html', {
        'appel_offre': ao,
        'classement': classement_list
    })


@login_required
def soumissionnaire_details(request, soumissionnaire_id):
    """API pour les détails d'un soumissionnaire (appel AJAX)"""
    s = get_object_or_404(Soumissionnaire, id=soumissionnaire_id)

    data = {
        'id': s.id,
        'nom_entreprise': s.nom_entreprise,
        'prix_corrige': str(s.prix_corrige) if s.prix_corrige else None,
        'statut_final': s.statut_final,
        'motif_rejet': s.motif_rejet,
        'criteres': [],
        'justifications': [],
    }

    for donnee in s.donnees_extraites.all():
        data['criteres'].append({
            'libelle': donnee.critere.libelle,
            'categorie': donnee.critere.categorie,
            'valeur_extraite': donnee.valeur_extraite,
        })
        if donnee.justification_ia:
            data['justifications'].append({
                'critere': donnee.critere.libelle,
                'justification': donnee.justification_ia,
            })

    return JsonResponse(data)


@login_required
def supprimer_soumissionnaire(request, soumissionnaire_id):
    """Supprime un soumissionnaire (API AJAX)"""
    if request.user.role not in ['evaluateur', 'secretaire']:
        return JsonResponse({'success': False, 'error': 'Droits insuffisants'}, status=403)

    if request.method == 'POST':
        s = get_object_or_404(Soumissionnaire, id=soumissionnaire_id)
        s.delete()
        return JsonResponse({'success': True})

    return JsonResponse({'success': False, 'error': 'Méthode non autorisée'}, status=405)
@login_required
def update_attributaire(request, ao_id):
    """Mise à jour des informations de l'attributaire"""
    ao = get_object_or_404(AppelOffre, id=ao_id)
    attributaire = ao.soumissionnaires.filter(statut_final='retenu').first()

    if not attributaire:
        messages.error(request, "Aucun attributaire désigné pour cet appel d'offres.")
        return redirect('detail_ao', ao_id=ao_id)

    attributaire.adresse = request.POST.get('adresse', '')
    attributaire.telephone = request.POST.get('telephone', '')
    attributaire.email = request.POST.get('email', '')
    attributaire.nationalite = request.POST.get('nationalite', 'Togolaise')
    attributaire.beneficiaires_effectifs = request.POST.get('beneficiaires', '')
    attributaire.save()

    messages.success(request, "✅ Informations de l'attributaire mises à jour.")
    return redirect('detail_ao', ao_id=ao_id)