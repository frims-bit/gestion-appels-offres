from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.storage import default_storage
from django.core.files.base import ContentFile
from django.conf import settings
import os

from .models import AppelOffre, CritereGrille
from soumissionnaires.models import Soumissionnaire
from pipeline.extraction.lecture import extraire_texte
from pipeline.extraction.grille import generer_grille, ajouter_manuellement


def home(request):
    """Page d'accueil"""
    derniers_ao = AppelOffre.objects.all().order_by('-date_publication')[:5]
    context = {
        'total_ao': AppelOffre.objects.count(),
        'total_soumissionnaires': Soumissionnaire.objects.count(),
        'total_pv': 0,
        'total_valides': 0,
        'derniers_ao': derniers_ao,
    }
    return render(request, 'appels_offres/home.html', context)


@login_required
def upload_ao(request):
    """Upload du cahier des charges avec extraction automatique"""
    # Seul le secrétaire peut uploader
    if request.user.role != 'secretaire':
        messages.error(request, "Seul le secrétaire peut uploader un cahier des charges.")
        return redirect('home')

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
            messages.warning(request, "Le texte extrait est trop court. Vérifie le document.")
            return redirect('detail_ao', ao_id=ao.id)

        # Extraction des critères
        elements = generer_grille(ao, texte)

        if elements:
            groupes = ao.criteres.filter(parent=None)
            messages.success(
                request,
                f"✅ Grille générée avec {groupes.count()} groupes et "
                f"{ao.criteres.filter(parent__isnull=False).count()} sous-critères."
            )
            return redirect('validation_grille', ao_id=ao.id)
        else:
            messages.warning(request, "⚠️ Aucun critère extrait automatiquement.")
            return redirect('detail_ao', ao_id=ao.id)

    return render(request, 'appels_offres/upload_ao.html')


@login_required
def liste_ao(request):
    """Liste des appels d'offres (avec filtres et pagination)"""
    appels_offres = AppelOffre.objects.all().order_by('-date_publication')

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
    return render(request, 'appels_offres/detail_ao.html', {'appel_offre': ao})


@login_required
def validation_grille(request, ao_id):
    """
    Validation de la grille avec arborescence (groupes et sous-critères)
    Réservé aux évaluateurs
    """
    # Vérification des droits
    if request.user.role != 'evaluateur':
        messages.error(request, "Vous n'avez pas les droits pour modifier cette grille.")
        return redirect('detail_ao', ao_id=ao_id)

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

            ajouter_manuellement(ao, libelle, categorie, type_groupe, float(note_max), parent)

            if parent:
                messages.success(request, f"✅ Sous-critère '{libelle}' ajouté avec succès.")
            else:
                messages.success(request, f"✅ Groupe '{libelle}' ajouté avec succès.")
        else:
            messages.error(request, "Tous les champs sont obligatoires.")
        return redirect('validation_grille', ao_id=ao_id)

    # Validation des critères
    if request.method == 'POST':
        for critere in ao.criteres.all():
            valide = request.POST.get(f'critere_{critere.id}') == 'on'
            critere.valide = valide
            critere.save()

            note = request.POST.get(f'note_{critere.id}')
            if note is not None and note.strip():
                try:
                    critere.note_max = float(note)
                    critere.save()
                except ValueError:
                    pass

        # Vérifier si tous les groupes et sous-critères sont validés
        groupes = ao.criteres.filter(parent=None)
        tous_valides = True

        for g in groupes:
            if not g.valide:
                tous_valides = False
                break
            for sous in g.sous_criteres.all():
                if not sous.valide:
                    tous_valides = False
                    break
            if not tous_valides:
                break

        if tous_valides and groupes.exists():
            ao.statut = AppelOffre.Statut.EN_COURS
            ao.save()
            messages.success(request, "✅ Tous les groupes et sous-critères sont validés !")
        else:
            messages.info(request, "✅ Modifications enregistrées.")

        return redirect('detail_ao', ao_id=ao_id)

    groupes = ao.criteres.filter(parent=None).prefetch_related('sous_criteres')

    return render(request, 'appels_offres/validation_grille.html', {
        'appel_offre': ao,
        'groupes': groupes
    })


@login_required
def supprimer_critere(request):
    """Supprime un critère (groupe ou sous-critère)"""
    if request.user.role != 'evaluateur':
        messages.error(request, "Vous n'avez pas les droits pour supprimer des critères.")
        return redirect('home')

    if request.method == 'POST' and request.POST.get('action') == 'supprimer':
        critere_id = request.POST.get('critere_id')
        if critere_id:
            critere = get_object_or_404(CritereGrille, id=critere_id)
            ao_id = critere.appel_offre.id
            nom = critere.libelle
            critere.delete()
            messages.success(request, f"✅ Critère '{nom}' supprimé avec succès.")
            return redirect('validation_grille', ao_id=ao_id)

    return redirect('home')


@login_required
def modifier_critere(request):
    """Modifie un critère (groupe ou sous-critère)"""
    if request.user.role != 'evaluateur':
        return JsonResponse({'error': 'Droits insuffisants'}, status=403)

    if request.method == 'POST':
        critere_id = request.POST.get('critere_id')
        libelle = request.POST.get('libelle')
        categorie = request.POST.get('categorie')
        type_groupe = request.POST.get('type_groupe')
        note_max = request.POST.get('note_max')

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
            messages.success(request, f"✅ Critère '{libelle}' modifié avec succès.")
            return redirect('validation_grille', ao_id=critere.appel_offre.id)

    return redirect('home')