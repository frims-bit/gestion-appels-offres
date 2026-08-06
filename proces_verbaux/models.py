from django.db import models
from appels_offres.models import AppelOffre
from utilisateurs.models import Utilisateur


class ProcesVerbal(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        VALIDE = "valide", "Validé et signé"

    appel_offre = models.OneToOneField(AppelOffre, on_delete=models.CASCADE, related_name="proces_verbal")
    statut = models.CharField(max_length=20, choices=Statut.choices, default=Statut.BROUILLON)
    fichier = models.FileField(upload_to="proces_verbaux/", null=True, blank=True)
    valide_par = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True)
    date_generation = models.DateTimeField(auto_now_add=True)
    date_validation = models.DateTimeField(null=True, blank=True)

    def __str__(self):
        return f"PV - {self.appel_offre.reference} ({self.statut})"