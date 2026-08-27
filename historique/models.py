from django.db import models
from appels_offres.models import AppelOffre
from utilisateurs.models import Utilisateur


class HistoriqueAction(models.Model):
    utilisateur = models.ForeignKey(Utilisateur, on_delete=models.SET_NULL, null=True, blank=True)
    utilisateur_nom = models.CharField(max_length=150, blank=True)
    utilisateur_role = models.CharField(max_length=20, blank=True)
    appel_offre = models.ForeignKey(AppelOffre, on_delete=models.CASCADE, related_name="historique")
    action = models.CharField(max_length=100)
    details = models.TextField(blank=True)
    date_action = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-date_action"]

    def __str__(self):
        return f"{self.date_action} - {self.action} ({self.appel_offre.reference})"

    def save(self, *args, **kwargs):
        if self.utilisateur_id and self.utilisateur:
            if not self.utilisateur_nom:
                self.utilisateur_nom = self.utilisateur.get_full_name() or self.utilisateur.username
            if not self.utilisateur_role:
                self.utilisateur_role = self.utilisateur.role
        super().save(*args, **kwargs)

    @property
    def utilisateur_affichage(self):
        if self.utilisateur:
            return self.utilisateur.get_full_name() or self.utilisateur.username
        return self.utilisateur_nom or "Systeme"
