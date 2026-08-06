from django.db import models
from appels_offres.models import AppelOffre, CritereGrille
from utilisateurs.models import Utilisateur


class Soumissionnaire(models.Model):
    class StatutConformite(models.TextChoices):
        A_VERIFIER = "a_verifier", "À vérifier"
        RECEVABLE = "recevable", "Recevable"
        NON_RECEVABLE = "non_recevable", "Non recevable"
        CONFORME_TECHNIQUE = "conforme_technique", "Conforme technique"
        NON_CONFORME_TECHNIQUE = "non_conforme_technique", "Non conforme technique"

    class StatutFinal(models.TextChoices):
        EN_COURS = "en_cours", "En cours"
        RETENU = "retenu", "Retenu (attributaire proposé)"
        ECARTE = "ecarte", "Écarté"

    appel_offre = models.ForeignKey(AppelOffre, on_delete=models.CASCADE, related_name="soumissionnaires")
    nom_entreprise = models.CharField(max_length=255)
    date_depot = models.DateField(auto_now_add=True)

    statut_conformite = models.CharField(max_length=30, choices=StatutConformite.choices,
                                          default=StatutConformite.A_VERIFIER)
    motif_rejet = models.TextField(blank=True, null=True)

    prix_lu_publiquement = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    prix_corrige = models.DecimalField(max_digits=14, decimal_places=2, null=True, blank=True)
    rang = models.PositiveIntegerField(null=True, blank=True)

    qualification_verifiee = models.BooleanField(default=False)
    qualification_conforme = models.BooleanField(null=True, blank=True)

    statut_final = models.CharField(max_length=20, choices=StatutFinal.choices, default=StatutFinal.EN_COURS)
    
    # Dans la classe Soumissionnaire, ajouter :
    adresse = models.TextField(blank=True, null=True)
    telephone = models.CharField(max_length=50, blank=True, null=True)
    email = models.EmailField(blank=True, null=True)
    beneficiaires_effectifs = models.TextField(blank=True, null=True)
    nationalite = models.CharField(max_length=100, blank=True, null=True)

    def __str__(self):
        return f"{self.nom_entreprise} ({self.appel_offre.reference})"


class DonneeExtraite(models.Model):
    soumissionnaire = models.ForeignKey(Soumissionnaire, on_delete=models.CASCADE, related_name="donnees_extraites")
    critere = models.ForeignKey(CritereGrille, on_delete=models.CASCADE)
    valeur_extraite = models.TextField()
    justification_ia = models.TextField(blank=True)

    def __str__(self):
        return f"{self.soumissionnaire.nom_entreprise} - {self.critere.libelle}"


class Score(models.Model):
    soumissionnaire = models.ForeignKey(Soumissionnaire, on_delete=models.CASCADE, related_name="scores")
    critere = models.ForeignKey(CritereGrille, on_delete=models.CASCADE)
    note = models.DecimalField(max_digits=6, decimal_places=2)
    justification = models.TextField()
    valide_par = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True)
    date_validation = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"{self.soumissionnaire.nom_entreprise} - {self.critere.libelle} : {self.note}"
# Create your models here.
