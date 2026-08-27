
from django.contrib.auth.models import AbstractUser
from django.db import models


class Utilisateur(AbstractUser):
    class Role(models.TextChoices):
        SECRETAIRE = "secretaire", "Secrétaire"
        EVALUATEUR = "evaluateur", "Évaluateur"
        PRESIDENT = "president", "Président de commission"

    role = models.CharField(max_length=20, choices=Role.choices, default=Role.SECRETAIRE)

    def a_permission(self, action):
        permissions_par_role = {
            self.Role.SECRETAIRE: ["creer_appel_offre", "importer_dossier", "telecharger"],
            self.Role.EVALUATEUR: ["valider_grille", "valider_classement", "consulter_historique", "telecharger"],
            self.Role.PRESIDENT: ["signer_pv", "consulter_historique", "telecharger"],
        }
        return action in permissions_par_role.get(self.role, [])

    def __str__(self):
        return f"{self.username} ({self.role})"
