from django.db import models
from appels_offres.models import AppelOffre
from utilisateurs.models import Utilisateur


class HistoriqueAction(models.Model):
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True)
    appel_offre = models.ForeignKey(AppelOffre, on_delete=models.CASCADE, related_name="historique")
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_action"]

    def __str__(self):
        return f"{self.date_action} - {self.action} ({self.appel_offre.reference})"