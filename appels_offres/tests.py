import json
from types import SimpleNamespace
from unittest.mock import patch

from django.test import TestCase
from django.urls import reverse

from appels_offres.models import AppelOffre, CritereGrille
from pipeline.extraction.grille import generer_grille
from soumissionnaires.models import Soumissionnaire
from utilisateurs.models import Utilisateur


class HomeRoutingTests(TestCase):
    def _user(self, username, role):
        return Utilisateur.objects.create_user(
            username=username,
            email=f"{username}@example.com",
            password="secret",
            role=role,
        )

    def test_root_redirects_anonymous_to_login(self):
        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_root_redirects_secretary_to_dashboard(self):
        self.client.force_login(self._user("sec_home", Utilisateur.Role.SECRETAIRE))

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("dashboard_secretaire"), fetch_redirect_response=False)

    def test_root_redirects_evaluator_to_dashboard(self):
        self.client.force_login(self._user("eval_home", Utilisateur.Role.EVALUATEUR))

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("dashboard_evaluateur"), fetch_redirect_response=False)

    def test_root_redirects_president_to_dashboard(self):
        self.client.force_login(self._user("pres_home", Utilisateur.Role.PRESIDENT))

        response = self.client.get(reverse("home"))

        self.assertRedirects(response, reverse("dashboard_president"), fetch_redirect_response=False)

    def test_dashboards_require_authentication(self):
        for url_name in [
            "dashboard_secretaire",
            "dashboard_evaluateur",
            "dashboard_president",
        ]:
            response = self.client.get(reverse(url_name))
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_logout_redirects_to_root_then_login(self):
        self.client.force_login(self._user("logout_home", Utilisateur.Role.SECRETAIRE))

        response = self.client.get(reverse("logout"))

        self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
        response = self.client.get(reverse("home"))
        self.assertRedirects(response, reverse("login"), fetch_redirect_response=False)

    def test_login_then_root_routes_each_role(self):
        scenarios = [
            ("sec_login", Utilisateur.Role.SECRETAIRE, "dashboard_secretaire"),
            ("eval_login", Utilisateur.Role.EVALUATEUR, "dashboard_evaluateur"),
            ("pres_login", Utilisateur.Role.PRESIDENT, "dashboard_president"),
        ]
        for username, role, dashboard in scenarios:
            user = self._user(username, role)
            self.client.logout()

            response = self.client.post(
                reverse("login"),
                {"email": user.email, "password": "secret"},
            )
            self.assertRedirects(response, reverse("home"), fetch_redirect_response=False)
            response = self.client.get(reverse("home"))
            self.assertRedirects(response, reverse(dashboard), fetch_redirect_response=False)

    def test_protected_pages_redirect_anonymous_to_login(self):
        urls = [
            reverse("dashboard_secretaire"),
            reverse("dashboard_evaluateur"),
            reverse("dashboard_president"),
            reverse("liste_ao"),
        ]
        for url in urls:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(reverse("login")))

    def test_logout_invalidates_session_for_protected_pages(self):
        self.client.force_login(self._user("sec_session", Utilisateur.Role.SECRETAIRE))
        self.assertEqual(self.client.get(reverse("dashboard_secretaire")).status_code, 200)

        self.client.get(reverse("logout"))

        for url in [reverse("dashboard_secretaire"), reverse("dashboard_evaluateur"), reverse("dashboard_president")]:
            response = self.client.get(url)
            self.assertEqual(response.status_code, 302)
            self.assertTrue(response["Location"].startswith(reverse("login")))


class DetailAoSoumissionnairesTests(TestCase):
    def setUp(self):
        self.user = Utilisateur.objects.create_user(
            username="eval_detail",
            email="eval_detail@example.com",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )
        self.ao = AppelOffre.objects.create(
            reference="AO-DETAIL-001",
            titre="AO detail",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )

    def test_detail_ao_shows_tenderers(self):
        Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Alpha SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Beta SARL",
            statut_conformite=Soumissionnaire.StatutConformite.NON_RECEVABLE,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("detail_ao", args=[self.ao.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Dossiers")
        self.assertContains(response, "Alpha SARL")
        self.assertContains(response, "Beta SARL")
        self.assertContains(response, Soumissionnaire.StatutConformite.A_VERIFIER.label)
        self.assertContains(response, "Non recevable")


class ListeAOEvaluateurTests(TestCase):
    def setUp(self):
        self.user = Utilisateur.objects.create_user(
            username="eval_ao",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )

    def _ao(self, reference, statut):
        return AppelOffre.objects.create(
            reference=reference,
            titre=f"Titre {reference}",
            date_publication="2026-08-01",
            statut=statut,
        )

    def _grille_validee(self, ao):
        groupe = CritereGrille.objects.create(
            appel_offre=ao,
            libelle="Administratif",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            valide=True,
        )
        CritereGrille.objects.create(
            appel_offre=ao,
            libelle="Piece administrative",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            valide=True,
            parent=groupe,
        )

    def test_liste_ao_order_and_grid_flag(self):
        premier = self._ao("AO-001", AppelOffre.Statut.EN_COURS)
        deuxieme = self._ao("AO-002", AppelOffre.Statut.GRILLE_EN_ATTENTE)
        troisieme = self._ao("AO-003", AppelOffre.Statut.JUGE)
        self._grille_validee(premier)
        self.client.force_login(self.user)

        response = self.client.get(reverse("liste_ao"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            list(response.context["appels_offres"].object_list),
            [premier, deuxieme, troisieme],
        )
        self.assertContains(response, "Grille validée", count=1)
        self.assertContains(response, "En attente de validation", count=2)


class GenerationGrilleTests(TestCase):
    def setUp(self):
        self.ao = AppelOffre.objects.create(
            reference="AO-GEN-001",
            titre="Generation grille",
            date_publication="2026-08-01",
            statut=AppelOffre.Statut.GRILLE_EN_ATTENTE,
        )

    def _groq_response(self, groupes):
        content = json.dumps({"groupes": groupes})
        message = SimpleNamespace(content=content)
        choice = SimpleNamespace(message=message)
        return SimpleNamespace(choices=[choice])

    @patch("pipeline.extraction.grille.client.chat.completions.create")
    def test_generation_grille_incomplete(self, groq_mock):
        groq_mock.return_value = self._groq_response(
            [
                {
                    "libelle": "Recevabilite administrative",
                    "categorie": "administratif",
                    "type_groupe": "eliminatoire",
                    "note_max": 0,
                    "sous_criteres": [
                        {"libelle": "Presence de toutes les pieces obligatoires", "type": "eliminatoire", "note_max": 0}
                    ],
                },
                {
                    "libelle": "Qualification",
                    "categorie": "technique",
                    "type_groupe": "eliminatoire",
                    "note_max": 0,
                    "sous_criteres": [
                        {"libelle": "Exigence minimale de qualification", "type": "eliminatoire", "note_max": 0}
                    ],
                },
                {
                    "libelle": "Conformite technique",
                    "categorie": "technique",
                    "type_groupe": "eliminatoire",
                    "note_max": 0,
                    "sous_criteres": [
                        {"libelle": "Conformite a toutes les specifications minimales de la Section 3", "type": "eliminatoire", "note_max": 0}
                    ],
                },
                {
                    "libelle": "Evaluation financiere",
                    "categorie": "financier",
                    "type_groupe": "notable",
                    "note_max": 0,
                    "sous_criteres": [
                        {"libelle": "Montant total TTC - classement par ordre croissant", "type": "notable", "note_max": 0}
                    ],
                },
            ]
        )
        texte = """
        Le soumissionnaire doit fournir une attestation fiscale en cours de validite.
        Le dossier doit contenir le RCCM ou registre de commerce.
        Le soumissionnaire doit joindre le NIF.
        La garantie de soumission est obligatoire.
        Le candidat doit presenter au moins trois experiences similaires.
        Le personnel technique minimum doit comprendre un chef de projet.
        Les specifications techniques minimales exigent des ordinateurs Core i7.
        La capacite financiere minimale exige un chiffre d'affaires de 100 000 000 FCFA.
        Le montant total TTC de l'offre financiere sera utilise pour le classement.
        Toute piece obligatoire absente entraine le rejet de l'offre.
        """

        generer_grille(self.ao, texte)

        criteres = self.ao.criteres.filter(parent__isnull=False)
        libelles = list(criteres.values_list("libelle", flat=True))
        self.assertGreater(criteres.count(), 4)
        self.assertTrue(any("attestation fiscale" in libelle.lower() for libelle in libelles))
        self.assertTrue(any("rccm" in libelle.lower() or "registre" in libelle.lower() for libelle in libelles))
        self.assertTrue(any("core i7" in libelle.lower() for libelle in libelles))
        self.assertTrue(any("chiffre d'affaires" in libelle.lower() for libelle in libelles))


class ValidationGrilleInterfaceTests(TestCase):
    def setUp(self):
        self.evaluateur = Utilisateur.objects.create_user(
            username="eval_grille",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )
        self.secretaire = Utilisateur.objects.create_user(
            username="sec_grille",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        self.ao = AppelOffre.objects.create(
            reference="AO-GRILLE-001",
            titre="Grille",
            date_publication="2026-08-01",
            statut=AppelOffre.Statut.GRILLE_EN_ATTENTE,
        )

    def _critere(self, libelle, categorie, parent=None):
        return CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle=libelle,
            categorie=categorie,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=True,
            parent=parent,
        )

    def test_grille_grouped_by_category(self):
        admin = self._critere("Administratif", CritereGrille.Categorie.ADMINISTRATIF)
        tech = self._critere("Technique", CritereGrille.Categorie.TECHNIQUE)
        self._critere("Piece administrative", CritereGrille.Categorie.ADMINISTRATIF, admin)
        self._critere("Moyens techniques", CritereGrille.Categorie.TECHNIQUE, tech)
        self.client.force_login(self.evaluateur)

        response = self.client.get(reverse("validation_grille", args=[self.ao.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "RECEVABILITE")
        self.assertContains(response, "CAPACITE TECHNIQUE")

    def test_delete_critere_blocks_non_evaluator(self):
        critere = self._critere("A supprimer", CritereGrille.Categorie.ADMINISTRATIF)
        self.client.force_login(self.secretaire)

        response = self.client.post(
            reverse("supprimer_critere"),
            {"action": "supprimer", "critere_id": str(critere.id)},
        )

        self.assertRedirects(response, reverse("dashboard_secretaire"))
        self.assertTrue(CritereGrille.objects.filter(id=critere.id).exists())

    def test_edit_critere(self):
        critere = self._critere("Ancien", CritereGrille.Categorie.ADMINISTRATIF)
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("modifier_critere"),
            {
                "critere_id": str(critere.id),
                "libelle": "Nouveau",
                "categorie": CritereGrille.Categorie.TECHNIQUE,
                "type_groupe": CritereGrille.TypeGroupe.NOTABLE,
                "note_max": "12",
            },
        )

        self.assertRedirects(response, reverse("validation_grille", args=[self.ao.id]))
        critere.refresh_from_db()
        self.assertEqual(critere.libelle, "Nouveau")
        self.assertEqual(critere.categorie, CritereGrille.Categorie.TECHNIQUE)

    def test_edit_from_modal_stays_on_grid(self):
        groupe = self._critere("Groupe", CritereGrille.Categorie.ADMINISTRATIF)
        critere = self._critere("Ancien modal", CritereGrille.Categorie.ADMINISTRATIF, groupe)
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("validation_grille", args=[self.ao.id]),
            {
                "action": "appliquer_critere",
                "critere_id": str(critere.id),
                "libelle": "Nouveau modal",
                "categorie": CritereGrille.Categorie.TECHNIQUE,
                "type_groupe": CritereGrille.TypeGroupe.NOTABLE,
                "note_max": "8",
                "parent_id": str(groupe.id),
            },
        )

        self.assertRedirects(response, reverse("validation_grille", args=[self.ao.id]))
        critere.refresh_from_db()
        self.assertEqual(critere.libelle, "Nouveau modal")
        self.assertEqual(critere.categorie, CritereGrille.Categorie.TECHNIQUE)
        self.ao.refresh_from_db()
        self.assertEqual(self.ao.statut, AppelOffre.Statut.GRILLE_EN_ATTENTE)

    def test_add_critere(self):
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("validation_grille", args=[self.ao.id]),
            {
                "action": "ajouter_manuel",
                "libelle": "Condition test",
                "categorie": CritereGrille.Categorie.ADMINISTRATIF,
                "type_groupe": CritereGrille.TypeGroupe.NOTABLE,
                "note_max": "5",
            },
        )

        self.assertRedirects(response, reverse("validation_grille", args=[self.ao.id]))
        self.assertTrue(
            CritereGrille.objects.filter(
                appel_offre=self.ao,
                libelle="Condition test",
            ).exists()
        )

    def test_delete_critere(self):
        critere = self._critere("A supprimer", CritereGrille.Categorie.ADMINISTRATIF)
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("supprimer_critere"),
            {"action": "supprimer", "critere_id": str(critere.id)},
        )

        self.assertRedirects(response, reverse("validation_grille", args=[self.ao.id]))
        self.assertFalse(CritereGrille.objects.filter(id=critere.id).exists())

    def test_delete_group(self):
        groupe = self._critere("Groupe a supprimer", CritereGrille.Categorie.ADMINISTRATIF)
        sous = self._critere("Sous critere", CritereGrille.Categorie.ADMINISTRATIF, groupe)
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("supprimer_critere"),
            {"action": "supprimer", "critere_id": str(groupe.id)},
        )

        self.assertRedirects(response, reverse("validation_grille", args=[self.ao.id]))
        self.assertFalse(CritereGrille.objects.filter(id=groupe.id).exists())
        self.assertFalse(CritereGrille.objects.filter(id=sous.id).exists())

    def test_validate_group(self):
        groupe = CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle="Administratif",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=False,
        )
        sous = CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle="Piece administrative",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=False,
            parent=groupe,
        )
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("validation_grille", args=[self.ao.id]),
            {"action": "valider_groupe", "groupe_id": str(groupe.id)},
        )

        self.assertRedirects(response, reverse("validation_grille", args=[self.ao.id]))
        groupe.refresh_from_db()
        sous.refresh_from_db()
        self.assertTrue(groupe.valide)
        self.assertTrue(sous.valide)

    def test_validate_grid(self):
        groupe = self._critere("Administratif", CritereGrille.Categorie.ADMINISTRATIF)
        sous = self._critere(
            "Piece administrative",
            CritereGrille.Categorie.ADMINISTRATIF,
            groupe,
        )
        self.client.force_login(self.evaluateur)

        response = self.client.post(
            reverse("validation_grille", args=[self.ao.id]),
            {
                f"critere_{groupe.id}": "on",
                f"critere_{sous.id}": "on",
                f"note_{groupe.id}": "0",
                f"note_{sous.id}": "0",
            },
        )

        self.assertRedirects(response, reverse("detail_ao", args=[self.ao.id]))
        self.ao.refresh_from_db()
        self.assertEqual(self.ao.statut, AppelOffre.Statut.EN_COURS)
        self.assertTrue(self.ao.grille_evaluee)
