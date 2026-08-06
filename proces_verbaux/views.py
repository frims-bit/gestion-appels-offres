from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.http import FileResponse
import os

from .models import ProcesVerbal
from appels_offres.models import AppelOffre
from pipeline.generation.pv import generer_pv


@login_required
def generer_pv_view(request, ao_id):
    """Génération du PV"""
    ao = get_object_or_404(AppelOffre, id=ao_id)

    if request.method == 'POST':
        try:
            pv = generer_pv(ao)
            messages.success(request, "PV généré avec succès !")
            return redirect('generer_pv', ao_id=ao_id)
        except Exception as e:
            messages.error(request, f"Erreur lors de la génération : {e}")
            return redirect('generer_pv', ao_id=ao_id)

    pv = ProcesVerbal.objects.filter(appel_offre=ao).first()
    return render(request, 'proces_verbaux/generer_pv.html', {
        'appel_offre': ao,
        'pv': pv,
        'pv_genere': pv and pv.fichier and bool(pv.fichier.name)
    })


@login_required
def telecharger_pv(request, ao_id):
    """Téléchargement du PV"""
    ao = get_object_or_404(AppelOffre, id=ao_id)
    pv = get_object_or_404(ProcesVerbal, appel_offre=ao)

    if not pv.fichier or not os.path.exists(pv.fichier.path):
        messages.error(request, "Le fichier PV est introuvable.")
        return redirect('generer_pv', ao_id=ao_id)

    return FileResponse(open(pv.fichier.path, 'rb'), as_attachment=True)


@login_required
def signature_pv(request):
    """Liste des PV à signer (Président)"""
    if request.user.role != 'president':
        messages.error(request, "Seul le président peut signer les PV.")
        return redirect('home')

    pvs = ProcesVerbal.objects.filter(statut=ProcesVerbal.Statut.BROUILLON)
    return render(request, 'proces_verbaux/signature_pv.html', {'pvs': pvs})


@login_required
def valider_pv(request, pv_id):
    """Validation d'un PV par le président"""
    if request.user.role != 'president':
        messages.error(request, "Seul le président peut valider un PV.")
        return redirect('home')

    pv = get_object_or_404(ProcesVerbal, id=pv_id)
    pv.statut = ProcesVerbal.Statut.VALIDE
    pv.valide_par = request.user
    pv.save()

    ao = pv.appel_offre
    ao.statut = AppelOffre.Statut.CLOTURE
    ao.save()

    messages.success(request, "PV validé et signé avec succès !")
    return redirect('signature_pv')