from django.test import TestCase

from appels_offres.models import AppelOffre
from historique.models import HistoriqueAction
from utilisateurs.models import Utilisateur


class HistoriqueUtilisateurTests(TestCase):
    def test_historique_conserve_nom_apres_suppression_utilisateur(self):
        user = Utilisateur.objects.create_user(
            username="jean",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )
        ao = AppelOffre.objects.create(
            reference="AO-HIST-001",
            titre="Historique",
            date_publication="2026-08-08",
            statut=AppelOffre.Statut.EN_COURS,
        )
        historique = HistoriqueAction.objects.create(
            utilisateur=user,
            appel_offre=ao,
            action="Validation humaine",
            details="Decision : Present / conforme",
        )

        user.delete()
        historique.refresh_from_db()

        self.assertIsNone(historique.utilisateur)
        self.assertEqual(historique.utilisateur_nom, "jean")
        self.assertEqual(historique.utilisateur_role, Utilisateur.Role.EVALUATEUR)
        self.assertEqual(historique.utilisateur_affichage, "jean")
