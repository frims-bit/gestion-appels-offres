from django.shortcuts import render, redirect, get_object_or_404
from django.contrib import messages
from django.contrib.auth.decorators import login_required
from django.core.files.base import ContentFile
from django.http import JsonResponse, HttpResponseForbidden
from django.db.models import Prefetch
from django.utils import timezone
from decimal import Decimal, InvalidOperation
import unicodedata
import logging


from .models import Soumissionnaire, DonneeExtraite
from appels_offres.models import AppelOffre
from historique.models import HistoriqueAction

from pipeline.extraction.lecture import extraire_texte
from pipeline.extraction.candidats import extraire_donnees_candidat
from pipeline.scoring.cascade import _etat_donnee, cascade_complete

logger = logging.getLogger(__name__)


def _format_montant(valeur):
    if valeur is None:
        return None
    return f"{valeur:,.2f}".replace(",", " ").replace(".00", "")


def _ao_autorises_depot(utilisateur):
    return [
        ao
        for ao in AppelOffre.objects.exclude(statut=AppelOffre.Statut.CLOTURE)
        .prefetch_related("criteres", "soumissionnaires")
        .order_by("created_at", "id")
        if ao.grille_evaluee
        and ao.statut in [AppelOffre.Statut.EN_COURS, AppelOffre.Statut.JUGE]
    ]


def _ao_depot_autorise(ao, utilisateur):
    return (
        ao.statut in [AppelOffre.Statut.EN_COURS, AppelOffre.Statut.JUGE]
        and ao.grille_evaluee
    )


def _resume_motif(motif):
    if not motif:
        return ""

    motif_normalise = unicodedata.normalize("NFD", str(motif).lower())
    motif_normalise = "".join(
        caractere
        for caractere in motif_normalise
        if unicodedata.category(caractere) != "Mn"
    )
    lignes = [
        ligne.strip()
        for ligne in str(motif).splitlines()
        if ligne.strip()
    ]
    elements = [ligne for ligne in lignes if ligne.startswith("-")]

    if elements and "verifier" in motif_normalise:
        return f"{len(elements)} element(s) a verifier"

    if elements:
        if "verifier" in motif.lower() or "vÃ©rifier" in motif.lower():
            return f"{len(elements)} element(s) a verifier"
        return f"{len(elements)} motif(s) de rejet"

    return lignes[0][:120] if lignes else ""


def _normaliser_decision_humaine(decision):
    decision = str(decision or "").strip().lower()
    if decision in {"present", "conforme", "recevable", "conforme_technique"}:
        return "Present"
    if decision in {"incertain", "a_verifier", "à_verifier", "a verifier"}:
        return "Incertain"
    if decision in {"absent", "non_conforme", "non_recevable"}:
        return "Absent"
    return None


def _libelle_decision_humaine(valeur):
    if valeur == "Present":
        return "Présent / conforme"
    if valeur == "Incertain":
        return "Incertain / à vérifier"
    if valeur == "Absent":
        return "Absent / non conforme"
    return valeur


def _creer_historique_validation(
    soumissionnaire,
    utilisateur,
    action,
    decision,
    motif,
    donnees,
    statut_avant=None,
    statut_apres=None,
):
    lignes = [
        f"Soumissionnaire #{soumissionnaire.id} : {soumissionnaire.nom_entreprise}",
        f"Décision : {_libelle_decision_humaine(decision)}",
    ]
    if statut_avant is not None:
        lignes.append(f"Statut avant : {statut_avant}")
    if statut_apres is not None:
        lignes.append(f"Statut après : {statut_apres}")
    if motif:
        lignes.append(f"Motif : {motif}")
    if donnees:
        lignes.append(
            "Données concernées : "
            + ", ".join(f"#{d.id} {d.critere.libelle}" for d in donnees)
        )

    return HistoriqueAction.objects.create(
        utilisateur=utilisateur,
        utilisateur_nom=(
            utilisateur.get_full_name() or utilisateur.username
            if utilisateur
            else ""
        ),
        utilisateur_role=utilisateur.role if utilisateur else "",
        appel_offre=soumissionnaire.appel_offre,
        action=action,
        details="\n".join(lignes),
    )


def _appliquer_decision_humaine(
    soumissionnaire,
    decision,
    utilisateur,
    motif="",
    correction_ia=False,
    donnee_id=None,
    critere_id=None,
    valeur_corrigee="",
):
    valeur = _normaliser_decision_humaine(decision)
    if valeur is None:
        raise ValueError("Decision manuelle invalide")

    toutes_les_donnees = list(
        soumissionnaire.donnees_extraites.select_related("critere")
    )
    donnees_a_corriger = [
        donnee
        for donnee in toutes_les_donnees
        if _etat_donnee(donnee)[0] in {"absent", "incertain"}
    ]
    if donnee_id:
        if not str(donnee_id).isdigit():
            raise ValueError("Donnée à corriger inexistante")
        donnees = [
            donnee for donnee in toutes_les_donnees if donnee.id == int(donnee_id)
        ]
    elif critere_id:
        if not str(critere_id).isdigit():
            raise ValueError("Critere a corriger inexistant")
        critere = soumissionnaire.appel_offre.criteres.filter(
            id=int(critere_id),
            parent__isnull=False,
        ).first()
        if critere is None:
            raise ValueError("Critere a corriger inexistant")
        donnee, _ = DonneeExtraite.objects.get_or_create(
            soumissionnaire=soumissionnaire,
            critere=critere,
            defaults={
                "valeur_extraite": "Incertain",
                "justification_ia": "Donnee absente creee pour validation humaine.",
            },
        )
        donnees = [donnee]
    else:
        donnees = donnees_a_corriger

    if not donnees and correction_ia:
        donnees = toutes_les_donnees[:1]

    if not donnees:
        criteres_sans_donnee = (
            soumissionnaire.appel_offre.criteres.filter(
                valide=True,
                parent__isnull=False,
            )
            .exclude(id__in=[donnee.critere_id for donnee in toutes_les_donnees])
            .order_by("categorie", "id")
        )
        donnees = [
            DonneeExtraite.objects.create(
                soumissionnaire=soumissionnaire,
                critere=critere,
                valeur_extraite="Incertain",
                justification_ia="Donnée absente créée pour validation humaine.",
            )
            for critere in criteres_sans_donnee
        ]

    if not donnees:
        raise ValueError("Aucune donnée à valider")

    type_trace = "CORRIGE - Correction erreur IA" if correction_ia else "CONFIRME - Decision humaine"
    trace = f"{type_trace} par {utilisateur.username}: {valeur}."
    if motif:
        trace = f"{trace} Motif: {motif}."

    for donnee in donnees:
        ancienne_valeur = donnee.valeur_extraite
        donnee.valeur_extraite = valeur_corrigee or valeur
        donnee.justification_ia = (
            f"{donnee.justification_ia}\n\n"
            f"{trace} Ancienne valeur: {ancienne_valeur}."
        ).strip()
        donnee.save(update_fields=["valeur_extraite", "justification_ia"])

    return len(donnees), valeur, donnees



@login_required
def upload_dossier(request):

    if request.user.role != "secretaire":

        messages.error(
            request,
            "Seul le secrétaire peut uploader des dossiers."
        )

        return redirect("home")



    appels_offres = _ao_autorises_depot(request.user)



    if request.method == "POST":

        ao_id = request.POST.get("appel_offre_id")
        nom_entreprise = request.POST.get("nom_entreprise")
        fichier = request.FILES.get("fichier")



        if not all([
            ao_id,
            nom_entreprise,
            fichier
        ]):

            messages.error(
                request,
                "Tous les champs sont obligatoires."
            )

            return redirect(
                "upload_dossier"
            )



        ao = get_object_or_404(
            AppelOffre,
            id=ao_id
        )

        if not _ao_depot_autorise(ao, request.user):
            messages.error(
                request,
                "Upload refuse : la grille doit etre evaluee et l'appel d'offres ne doit pas etre cloture."
            )
            return redirect("upload_dossier")



        try:
            logger.info("[UPLOAD] Dossier recu : %s", nom_entreprise)
            logger.info("[UPLOAD] Fichier : %s", fichier.name)
            logger.info("[EXTRACTION] Debut du traitement du dossier : %s", nom_entreprise)

            soumissionnaire, created = Soumissionnaire.objects.get_or_create(
                appel_offre=ao,
                nom_entreprise=nom_entreprise,
                defaults={
                    "statut_conformite":
                        Soumissionnaire.StatutConformite.A_VERIFIER,

                    "statut_final":
                        Soumissionnaire.StatutFinal.EN_COURS,

                    "depose_par": request.user,

                    "date_depot_dossier": timezone.now()
                }
            )



            if not created:

                soumissionnaire.statut_conformite = (
                    Soumissionnaire.StatutConformite.A_VERIFIER
                )

                soumissionnaire.statut_final = (
                    Soumissionnaire.StatutFinal.EN_COURS
                )

                soumissionnaire.motif_rejet = None
                soumissionnaire.rang = None

                soumissionnaire.qualification_verifiee = False
                soumissionnaire.qualification_conforme = None
                soumissionnaire.depose_par = request.user
                soumissionnaire.date_depot_dossier = timezone.now()

                DonneeExtraite.objects.filter(
                    soumissionnaire=soumissionnaire
                ).delete()



            # ==========================
            # SAUVEGARDE DU PDF
            # ==========================
            soumissionnaire.fichier_dossier.save(
                fichier.name,
                ContentFile(fichier.read()),
                 save=True
            )
            logger.info(
                "[UPLOAD] Enregistrement du dossier termine : soumissionnaire_id=%s",
                soumissionnaire.id,
            )

            chemin_complet = soumissionnaire.fichier_dossier.path



            # ==========================
            # EXTRACTION TEXTE
            # ==========================

            logger.info("[EXTRACTION] Lecture des documents...")
            texte = extraire_texte(
                chemin_complet
            )
            taille_texte = len(texte or "") if isinstance(texte, str) else 0
            ocr_necessaire = (
                isinstance(texte, str)
                and texte.startswith("[OCR]")
            ) or taille_texte < 50
            logger.info("[EXTRACTION] Lecture terminee : %s caracteres extraits", taille_texte)
            if ocr_necessaire:
                logger.info("[EXTRACTION] Document traite avec OCR ou texte insuffisant")
            else:
                logger.info("[EXTRACTION] Texte natif detecte, OCR non necessaire")

            texte_exploitable = (
                isinstance(texte, str)
                and texte.strip()
                and not texte.startswith("[Erreur")
                and not texte.startswith("[OCR] Aucun texte")
            )

            if not texte_exploitable or len(texte.strip()) < 50:

                soumissionnaire.texte_extrait = texte or ""
                soumissionnaire.depose_par = request.user
                soumissionnaire.date_depot_dossier = timezone.now()
                soumissionnaire.save()
                logger.warning(
                    "[EXTRACTION] ERREUR lors du traitement du dossier : %s",
                    nom_entreprise,
                )


                messages.warning(
                    request,
                    "PDF vide ou impossible à lire."
                )


                return redirect(
                    "traitement_dossiers",
                    ao_id=ao.id
                )



            soumissionnaire.texte_extrait = texte
            soumissionnaire.depose_par = request.user
            soumissionnaire.date_depot_dossier = timezone.now()
            soumissionnaire.save()



            # ==========================
            # EXTRACTION IA
            # ==========================

            logger.info("[EXTRACTION] Extraction des informations...")
            extraire_donnees_candidat(
                soumissionnaire,
                texte
            )
            logger.info(
                "[EXTRACTION] Informations extraites : %s critere(s) analyses",
                soumissionnaire.donnees_extraites.count(),
            )
            logger.info("[EXTRACTION] Verification des criteres...")
            cascade_complete(ao)
            soumissionnaire.refresh_from_db()
            logger.info("[EXTRACTION] Extraction terminee avec succes")
            logger.info("[CLASSEMENT] Classement mis a jour")


            messages.success(
                request,
                f"Traitement termine : dossier {nom_entreprise} analyse avec succes."
            )



            return redirect(
                "traitement_dossiers",
                ao_id=ao.id
            )



        except Exception as e:
            logger.exception(
                "[EXTRACTION] ERREUR lors du traitement du dossier : %s",
                nom_entreprise,
            )


            messages.error(
                request,
                f"Erreur import : {e}"
            )


            return redirect(
                "upload_dossier"
            )



    return render(
        request,
        "soumissionnaires/upload_dossier.html",
        {
            "appels_offres": appels_offres
        }
    )




@login_required
def traitement_dossiers(request, ao_id):
    if request.user.role != "secretaire":
        return redirect("home")

    ao = get_object_or_404(AppelOffre, id=ao_id)
    dossiers = Soumissionnaire.objects.filter(
        appel_offre=ao,
        depose_par=request.user,
    ).order_by("date_depot_dossier", "id")

    if not dossiers.exists():
        return redirect("upload_dossier")

    messages.success(
        request,
        f"Upload terminé : les dossiers pour {ao.reference} ont été transmis à l'évaluation.",
    )
    return render(
        request,
        "soumissionnaires/traitement_dossiers.html",
        {
            "appel_offre": ao,
            "dossiers": dossiers,
        },
    )


@login_required
def mes_depots(request):
    if request.user.role != "secretaire":
        return redirect("home")

    dossiers = (
        Soumissionnaire.objects.filter(depose_par=request.user)
        .select_related("appel_offre")
        .order_by("appel_offre__reference", "date_depot_dossier", "id")
    )
    depots = {}
    for dossier in dossiers:
        depots.setdefault(dossier.appel_offre, []).append(dossier)

    return render(
        request,
        "soumissionnaires/mes_depots.html",
        {
            "depots": depots,
        },
    )


@login_required
def detail_depot(request, ao_id):
    if request.user.role != "secretaire":
        return redirect("home")

    ao = get_object_or_404(AppelOffre, id=ao_id)
    dossiers = Soumissionnaire.objects.filter(
        appel_offre=ao,
        depose_par=request.user,
    ).order_by("date_depot_dossier", "id")

    if not dossiers.exists():
        return redirect("mes_depots")

    return render(
        request,
        "soumissionnaires/detail_depot.html",
        {
            "appel_offre": ao,
            "dossier": dossiers.first(),
            "dossiers": dossiers,
        },
    )


@login_required
def classement(request, ao_id):

    if request.user.role not in [
        "evaluateur",
        "president"
    ]:
        messages.error(
            request,
            "Accès refusé."
        )
        return redirect("home")

    ao = get_object_or_404(
        AppelOffre,
        id=ao_id
    )

    try:

        logger.info("[CLASSEMENT] Mise a jour du classement : %s", ao.reference)
        classement_list = cascade_complete(ao)
        logger.info("[CLASSEMENT] Classement mis a jour : %s soumissionnaire(s)", len(classement_list))

    except Exception as e:

        logger.exception("[CLASSEMENT] ERREUR lors de la mise a jour du classement")

        messages.error(
            request,
            f"Erreur lors de l'évaluation : {e}"
        )

        classement_list = list(
            ao.soumissionnaires.all()
        )

    for soumissionnaire in classement_list:
        soumissionnaire.motif_resume = _resume_motif(
            soumissionnaire.motif_rejet
        )
        soumissionnaire.prix_corrige_formate = _format_montant(
            soumissionnaire.prix_corrige
        )
        soumissionnaire.prix_lu_formate = _format_montant(
            soumissionnaire.prix_lu_publiquement
        )
        soumissionnaire.prix_financier_a_saisir = (
            soumissionnaire.prix_corrige is None
            or soumissionnaire.prix_financier_statut in {"", "absent", "incertain"}
        )

    return render(
        request,
        "soumissionnaires/classement.html",
        {
            "appel_offre": ao,
            "classement": classement_list
        }
    )
@login_required
def soumissionnaire_details(request, soumissionnaire_id):
    if request.user.role not in {"evaluateur", "president"}:
        return HttpResponseForbidden("Accès interdit.")

    s = get_object_or_404(
        Soumissionnaire.objects.select_related("appel_offre").prefetch_related(
            "appel_offre__criteres",
            Prefetch(
                "donnees_extraites",
                queryset=DonneeExtraite.objects.select_related("critere").order_by(
                    "critere__categorie",
                    "critere__id",
                ),
            )
        ),
        id=soumissionnaire_id
    )


    data = {
        "id": s.id,
        "nom_entreprise": s.nom_entreprise,
        "appel_offre": {
            "id": s.appel_offre_id,
            "reference": s.appel_offre.reference,
            "titre": s.appel_offre.titre,
        },
        "entreprise": {
            "nom": s.nom_entreprise,
            "adresse": s.adresse,
            "telephone": s.telephone,
            "email": s.email,
            "nationalite": s.nationalite,
            "beneficiaires_effectifs": s.beneficiaires_effectifs,
        },
        "prix_corrige": str(s.prix_corrige) if s.prix_corrige is not None else None,
        "prix_corrige_formate": _format_montant(s.prix_corrige),
        "prix_lu_publiquement": str(s.prix_lu_publiquement)
        if s.prix_lu_publiquement is not None else None,
        "prix_lu_publiquement_formate": _format_montant(s.prix_lu_publiquement),
        "prix_financier": {
            "valeur_brute": s.prix_financier_brut,
            "valeur_normalisee": str(s.prix_corrige) if s.prix_corrige is not None else None,
            "devise": s.prix_financier_devise,
            "source": s.prix_financier_source,
            "statut": s.prix_financier_statut,
            "validation_humaine": s.prix_financier_validation_humaine,
            "a_saisir": (
                s.prix_corrige is None
                or s.prix_financier_statut in {"", "absent", "incertain"}
            ),
        },
        "statut_final": s.statut_final,
        "statut_final_label": s.get_statut_final_display(),
        "statut_conformite": s.statut_conformite,
        "statut_conformite_label": s.get_statut_conformite_display(),
        "motif_rejet": s.motif_rejet,
        "motif_resume": _resume_motif(s.motif_rejet),
        "criteres": [],
        "justifications": [],
    }



    donnees_par_critere = {
        d.critere_id: d
        for d in s.donnees_extraites.all()
    }
    criteres = sorted(
        [critere for critere in s.appel_offre.criteres.all() if critere.parent_id is not None],
        key=lambda critere: (critere.categorie, critere.id),
    )

    for critere in criteres:
        d = donnees_par_critere.get(critere.id)
        valeur_extraite = d.valeur_extraite if d else "Non extrait"
        justification_ia = d.justification_ia if d else "Donnée absente pour ce soumissionnaire."
        etat, raison_etat = _etat_donnee(d) if d else ("absent", "Aucune donnée extraite.")

        data["criteres"].append(
            {
                "libelle": critere.libelle,
                "id": d.id if d else None,
                "critere_id": critere.id,
                "categorie": critere.categorie,
                "valeur_extraite": valeur_extraite,
                "justification_ia": justification_ia,
                "donnee_manquante": d is None,
                "etat": etat,
                "raison_etat": raison_etat,
            }
        )


        if d and d.justification_ia:

            data["justifications"].append(
                {
                    "critere": critere.libelle,
                    "justification": d.justification_ia
                }
            )


    data["statut_conformite"] = s.statut_conformite
    data["statut_conformite_label"] = s.get_statut_conformite_display()
    data["qualification_verifiee"] = s.qualification_verifiee
    data["qualification_conforme"] = s.qualification_conforme
    data["peut_valider_manuellement"] = s.statut_conformite == Soumissionnaire.StatutConformite.A_VERIFIER
    data["criteres_a_verifier"] = [
        critere
        for critere in data["criteres"]
        if critere["etat"] in {"absent", "incertain"}
    ]
    data["historique"] = [
        {
            "date": h.date_action.strftime("%d/%m/%Y %H:%M"),
            "utilisateur": h.utilisateur_affichage,
            "action": h.action,
            "details": h.details,
        }
        for h in HistoriqueAction.objects.filter(
            appel_offre=s.appel_offre,
            details__contains=f"Soumissionnaire #{s.id}",
        ).select_related("utilisateur")[:10]
    ]

    return JsonResponse(data)





@login_required
def supprimer_soumissionnaire(request, soumissionnaire_id):
    if request.user.role not in {"evaluateur", "president"}:
        return JsonResponse({"success": False, "error": "Acces refuse"}, status=403)

    if request.method != "POST":

        return JsonResponse(
            {
                "success":False
            },
            status=405
        )


    s = get_object_or_404(
        Soumissionnaire,
        id=soumissionnaire_id
    )


    s.delete()


    return JsonResponse(
        {
            "success":True
        }
    )





@login_required
def valider_manuellement(request, soumissionnaire_id):

    if request.method != "POST":
        return JsonResponse({"success": False}, status=405)

    if request.user.role not in ["evaluateur", "president"]:
        return JsonResponse({"success": False, "error": "Accès refusé"}, status=403)

    soumissionnaire = get_object_or_404(Soumissionnaire, id=soumissionnaire_id)
    decision = request.POST.get("decision")
    motif = (request.POST.get("motif") or "").strip()
    correction_ia = request.POST.get("correction_ia") == "1"
    donnee_id = request.POST.get("donnee_id")
    critere_id = request.POST.get("critere_id")
    valeur_corrigee = (request.POST.get("valeur_corrigee") or "").strip()

    if not motif and decision in {"absent", "rejeter", "incertain"}:
        return JsonResponse(
            {
                "success": False,
                "message": "Motif obligatoire pour cette decision humaine",
                "error": "Motif obligatoire pour cette decision humaine",
            },
            status=400,
        )

    if decision == "confirmer":
        decision = "present"
        correction_ia = bool(donnee_id)
    elif decision == "corriger":
        decision = "present"
        correction_ia = bool(donnee_id)
    elif decision == "rejeter":
        decision = "absent"
        correction_ia = bool(donnee_id)
    elif decision == "incertain":
        correction_ia = bool(donnee_id)

    statut_avant = soumissionnaire.statut_conformite
    anciennes_valeurs = {
        donnee.id: donnee.valeur_extraite
        for donnee in soumissionnaire.donnees_extraites.all()
    }

    try:
        nb_donnees, valeur, donnees_modifiees = _appliquer_decision_humaine(
            soumissionnaire,
            decision,
            request.user,
            motif=motif,
            correction_ia=correction_ia,
            donnee_id=donnee_id,
            critere_id=critere_id,
            valeur_corrigee=valeur_corrigee,
        )
    except (ValueError, TypeError) as exc:
        return JsonResponse(
            {"success": False, "message": str(exc), "error": str(exc)},
            status=400,
        )

    if valeur == "Present":
        for donnee in donnees_modifiees:
            ancienne_valeur = anciennes_valeurs.get(donnee.id) or donnee.valeur_extraite
            trace_decision = f"Statut humain : {ancienne_valeur} -> Present."
            donnee.justification_ia = f"{donnee.justification_ia}\n{trace_decision}".strip()
            donnee.save(update_fields=["valeur_extraite", "justification_ia"])

    HistoriqueAction.objects.create(
        utilisateur=None,
        appel_offre=soumissionnaire.appel_offre,
        action="IA - état avant décision humaine",
        details=(
            f"Soumissionnaire #{soumissionnaire.id} : {soumissionnaire.nom_entreprise}\n"
            f"Statut IA : {statut_avant}"
        ),
    )

    _creer_historique_validation(
        soumissionnaire=soumissionnaire,
        utilisateur=request.user,
        action=(
            "Correction erreur IA"
            if correction_ia
            else "Validation humaine"
        ),
        decision=valeur,
        motif=motif,
        donnees=donnees_modifiees,
        statut_avant=statut_avant,
    )

    soumissionnaire.statut_conformite = Soumissionnaire.StatutConformite.A_VERIFIER
    soumissionnaire.statut_final = Soumissionnaire.StatutFinal.EN_COURS
    soumissionnaire.rang = None
    soumissionnaire.save(
        update_fields=["statut_conformite", "statut_final", "rang"]
    )
    cascade_complete(soumissionnaire.appel_offre)
    soumissionnaire.refresh_from_db()

    return JsonResponse({
        "success": True,
        "message": "Validation manuelle enregistree",
        "statut": soumissionnaire.statut_conformite,
        "motif": motif,
        "statut_conformite": soumissionnaire.statut_conformite,
        "statut_conformite_label": soumissionnaire.get_statut_conformite_display(),
        "statut_final": soumissionnaire.statut_final,
        "statut_final_label": soumissionnaire.get_statut_final_display(),
        "motif_rejet": soumissionnaire.motif_rejet,
        "motif_resume": _resume_motif(soumissionnaire.motif_rejet),
        "prix_lu_publiquement_formate": _format_montant(
            soumissionnaire.prix_lu_publiquement
        ),
        "prix_corrige_formate": _format_montant(soumissionnaire.prix_corrige),
        "donnees_confirmees": nb_donnees,
        "donnees": [
            {
                "id": donnee.id,
                "valeur_extraite": donnee.valeur_extraite,
                "justification_ia": donnee.justification_ia,
            }
            for donnee in donnees_modifiees
        ],
        "message": "Validation manuelle enregistrée",
    })


@login_required
def update_prix_financier(request, soumissionnaire_id):

    if request.method != "POST":
        return JsonResponse(
            {
                "success": False,
                "message": "Methode non autorisee",
                "error": "Methode non autorisee",
            },
            status=405,
        )

    if request.user.role not in ["evaluateur", "president"]:
        return JsonResponse({"success": False, "error": "Acces refuse"}, status=403)

    soumissionnaire = get_object_or_404(Soumissionnaire, id=soumissionnaire_id)
    prix_lu = (request.POST.get("prix_lu_publiquement") or "").strip()
    prix_corrige = (request.POST.get("prix_corrige") or "").strip()
    prix_source = (request.POST.get("source") or "Validation humaine du prix financier").strip()

    try:
        soumissionnaire.prix_lu_publiquement = (
            Decimal(prix_lu) if prix_lu else None
        )
        soumissionnaire.prix_corrige = (
            Decimal(prix_corrige)
            if prix_corrige
            else soumissionnaire.prix_lu_publiquement
        )
    except (InvalidOperation, ValueError):
        return JsonResponse(
            {"success": False, "error": "Prix invalide"},
            status=400,
        )

    if soumissionnaire.prix_corrige is None:
        return JsonResponse(
            {"success": False, "error": "Prix financier obligatoire"},
            status=400,
        )

    soumissionnaire.prix_financier_brut = prix_corrige or prix_lu
    soumissionnaire.prix_financier_devise = soumissionnaire.prix_financier_devise or "XOF"
    soumissionnaire.prix_financier_source = prix_source
    soumissionnaire.prix_financier_statut = "corrige_humain"
    soumissionnaire.prix_financier_validation_humaine = True
    soumissionnaire.rang = None
    soumissionnaire.save(
        update_fields=[
            "prix_lu_publiquement",
            "prix_corrige",
            "prix_financier_brut",
            "prix_financier_devise",
            "prix_financier_source",
            "prix_financier_statut",
            "prix_financier_validation_humaine",
            "rang",
        ]
    )

    logger.info(
        "[PRIX] validation humaine : %s | brut=%s | normalise=%s | source=%s | statut=%s",
        soumissionnaire.nom_entreprise,
        soumissionnaire.prix_financier_brut,
        soumissionnaire.prix_corrige,
        soumissionnaire.prix_financier_source,
        soumissionnaire.prix_financier_statut,
    )

    cascade_complete(soumissionnaire.appel_offre)
    soumissionnaire.refresh_from_db()

    return JsonResponse(
        {
            "success": True,
            "prix_lu_publiquement": str(soumissionnaire.prix_lu_publiquement)
            if soumissionnaire.prix_lu_publiquement is not None else None,
            "prix_corrige": str(soumissionnaire.prix_corrige)
            if soumissionnaire.prix_corrige is not None else None,
            "prix_lu_publiquement_formate": _format_montant(
                soumissionnaire.prix_lu_publiquement
            ),
            "prix_corrige_formate": _format_montant(soumissionnaire.prix_corrige),
            "prix_financier_statut": soumissionnaire.prix_financier_statut,
            "prix_financier_validation_humaine": (
                soumissionnaire.prix_financier_validation_humaine
            ),
            "statut_conformite": soumissionnaire.statut_conformite,
            "statut_conformite_label": soumissionnaire.get_statut_conformite_display(),
            "statut_final": soumissionnaire.statut_final,
            "statut_final_label": soumissionnaire.get_statut_final_display(),
            "rang": soumissionnaire.rang,
        }
    )


@login_required
def update_attributaire(request, ao_id):
    if request.user.role != "secretaire":
        messages.error(request, "Seul le secretaire peut modifier l'attributaire.")
        return redirect("home")

    if request.method != "POST":

        return redirect(
            "detail_ao",
            ao_id=ao_id
        )



    ao = get_object_or_404(
        AppelOffre,
        id=ao_id
    )


    attributaire = ao.soumissionnaires.filter(
        statut_final=Soumissionnaire.StatutFinal.RETENU
    ).first()



    if not attributaire:

        messages.error(
            request,
            "Aucun attributaire."
        )

        return redirect(
            "detail_ao",
            ao_id=ao_id
        )



    attributaire.adresse = request.POST.get(
        "adresse",
        ""
    )

    attributaire.telephone = request.POST.get(
        "telephone",
        ""
    )

    attributaire.email = request.POST.get(
        "email",
        ""
    )

    attributaire.nationalite = request.POST.get(
        "nationalite",
        "Togolaise"
    )

    attributaire.beneficiaires_effectifs = request.POST.get(
        "beneficiaires",
        ""
    )


    attributaire.save()



    messages.success(
        request,
        "Informations mises à jour."
    )


    return redirect(
        "detail_ao",
        ao_id=ao_id
    )
