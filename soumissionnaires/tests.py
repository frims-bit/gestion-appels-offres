from decimal import Decimal
from unittest.mock import patch

from django.core.files.uploadedfile import SimpleUploadedFile
from django.test import TestCase
from django.urls import reverse

from appels_offres.models import AppelOffre, CritereGrille
from historique.models import HistoriqueAction
from pipeline.extraction.candidats import (
    _extraire_infos_entreprise,
    extraire_prix_financier_structure,
)
from pipeline.scoring.cascade import _etat_donnee, cascade_complete
from soumissionnaires.models import DonneeExtraite, Soumissionnaire
from utilisateurs.models import Utilisateur


class ExtractionEntrepriseTests(TestCase):
    def test_extracts_company_contact_fields_from_ocr_text(self):
        texte = (
            "Adresse : 12 Rue du Commerce, Lome\n"
            "Telephone : +228 90 12 34 56\n"
            "Email : contact@technoplus.tg\n"
            "Beneficiaires effectifs : Mme Afi Exemple\n"
            "Nationalite : Togolaise"
        )

        infos = _extraire_infos_entreprise(texte)

        self.assertEqual(infos["adresse"], "12 Rue du Commerce, Lome")
        self.assertEqual(infos["telephone"], "+228 90 12 34 56")
        self.assertEqual(infos["email"], "contact@technoplus.tg")
        self.assertEqual(infos["beneficiaires_effectifs"], "Mme Afi Exemple")
        self.assertEqual(infos["nationalite"], "Togolaise")


class CascadeDecisionTests(TestCase):
    def setUp(self):
        self.ao = AppelOffre.objects.create(
            reference="AO-TEST-001",
            titre="Test AO",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )
        self.groupe = CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle="Administratif",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=True,
        )
        self.critere = CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle="BPU signe",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=True,
            parent=self.groupe,
        )
        self.soumissionnaire = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Test SARL",
        )

    def test_signature_evidence_uncertain(self):
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=self.soumissionnaire,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="Signature/cachet non detecte dans l'OCR, aucune preuve visuelle disponible",
        )

        etat, raison = _etat_donnee(donnee)

        self.assertEqual(etat, "incertain")
        self.assertIn("incertain", raison.lower() or "")

    def test_contradictory_evidence_is_uncertain(self):
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=self.soumissionnaire,
            critere=self.critere,
            valeur_extraite="Non fourni",
            justification_ia="Le bordereau est fourni mais l'information n'est pas explicitement confirmee",
        )

        etat, raison = _etat_donnee(donnee)

        self.assertEqual(etat, "incertain")


class CascadeWorkflowTests(TestCase):
    def setUp(self):
        self.ao = AppelOffre.objects.create(
            reference="AO-CASCADE-001",
            titre="AO cascade",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )
        self.admin_parent = self._critere("Administratif", CritereGrille.Categorie.ADMINISTRATIF)
        self.tech_parent = self._critere("Technique", CritereGrille.Categorie.TECHNIQUE)
        self.admin = self._critere(
            "Garantie de soumission",
            CritereGrille.Categorie.ADMINISTRATIF,
            parent=self.admin_parent,
        )
        self.signature = self._critere(
            "BPU signe",
            CritereGrille.Categorie.ADMINISTRATIF,
            parent=self.admin_parent,
        )
        self.tech = self._critere(
            "Experience similaire",
            CritereGrille.Categorie.TECHNIQUE,
            parent=self.tech_parent,
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

    def _candidat(self, nom, prix=None):
        return Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise=nom,
            prix_corrige=prix,
        )

    def _donnee(self, candidat, critere, valeur, justification=""):
        return DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=critere,
            valeur_extraite=valeur,
            justification_ia=justification,
        )

    def _dossier_complet(self, candidat):
        self._donnee(candidat, self.admin, "Present", "Piece trouvee dans le dossier")
        self._donnee(candidat, self.signature, "Present", "Mention signee visible dans le texte")
        self._donnee(candidat, self.tech, "Present", "Reference confirmee")

    def test_compliant_candidates_rank_by_price(self):
        a = self._candidat("Entreprise A", Decimal("10000000"))
        b = self._candidat("Entreprise B", Decimal("12000000"))
        self._dossier_complet(a)
        self._dossier_complet(b)

        resultat = cascade_complete(self.ao)

        a.refresh_from_db()
        b.refresh_from_db()
        self.assertEqual([s.nom_entreprise for s in resultat[:2]], ["Entreprise A", "Entreprise B"])
        self.assertEqual(a.rang, 1)
        self.assertEqual(b.rang, 2)

    def test_non_receivable_candidate(self):
        ok = self._candidat("Conforme", Decimal("10000000"))
        ko = self._candidat("Non recevable", Decimal("9000000"))
        self._dossier_complet(ok)
        self._donnee(ko, self.admin, "Absent", "Absence explicitement confirmee")
        self._donnee(ko, self.signature, "Present", "Mention signee")
        self._donnee(ko, self.tech, "Present", "Reference")

        resultat = cascade_complete(self.ao)

        ko.refresh_from_db()
        self.assertIn(ko.id, [s.id for s in resultat])
        self.assertEqual(ko.statut_conformite, Soumissionnaire.StatutConformite.NON_RECEVABLE)
        self.assertIsNone(ko.rang)

    def test_uncertain_signature_blocks_candidate(self):
        candidat = self._candidat("A verifier", Decimal("10000000"))
        self._donnee(candidat, self.admin, "Present", "Piece trouvee")
        self._donnee(candidat, self.signature, "Absent", "Signature/cachet non detecte par OCR seul")
        self._donnee(candidat, self.tech, "Present", "Reference")

        cascade_complete(self.ao)

        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.A_VERIFIER)
        self.assertIn("visuelle", candidat.motif_rejet.lower())
        self.assertIsNone(candidat.rang)

    def test_missing_price_no_rank(self):
        candidat = self._candidat("Prix absent")
        self._dossier_complet(candidat)

        cascade_complete(self.ao)

        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE)
        self.assertIsNone(candidat.rang)
        self.assertIn("prix corrige absent", candidat.motif_rejet.lower())

    def test_public_price_used_for_financial_step(self):
        candidat = self._candidat("Prix public")
        candidat.prix_lu_publiquement = Decimal("15000000")
        candidat.save(update_fields=["prix_lu_publiquement"])
        self._dossier_complet(candidat)

        cascade_complete(self.ao)

        candidat.refresh_from_db()
        self.assertEqual(candidat.prix_corrige, Decimal("15000000.00"))
        self.assertEqual(candidat.rang, 1)

    def test_high_price_rank_later(self):
        bas = self._candidat("Bas", Decimal("10000000"))
        haut = self._candidat("Haut", Decimal("999999999999"))
        self._dossier_complet(bas)
        self._dossier_complet(haut)

        cascade_complete(self.ao)

        bas.refresh_from_db()
        haut.refresh_from_db()
        self.assertEqual(bas.rang, 1)
        self.assertEqual(haut.rang, 2)

    def test_cascade_idempotent(self):
        ko = self._candidat("KO")
        self._donnee(ko, self.admin, "Absent", "Absence explicitement confirmee")
        self._donnee(ko, self.signature, "Present", "Mention signee")
        self._donnee(ko, self.tech, "Present", "Reference")

        premier = [(s.id, s.rang, s.statut_conformite) for s in cascade_complete(self.ao)]
        second = [(s.id, s.rang, s.statut_conformite) for s in cascade_complete(self.ao)]

        self.assertEqual(premier, second)
        ao_vide = AppelOffre.objects.create(
            reference="AO-VIDE",
            titre="AO vide",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )
        with self.assertRaisesMessage(
            ValueError,
            "La grille doit etre generee et validee avant de lancer la cascade.",
        ):
            cascade_complete(ao_vide)


class UploadAndFinancialInterfaceTests(TestCase):
    def setUp(self):
        self.user = Utilisateur.objects.create_user(
            username="eval",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )
        self.ao = AppelOffre.objects.create(
            reference="AO-UI-001",
            titre="AO interface",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )
        self.groupe = CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle="Administratif",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=True,
        )
        self.critere = CritereGrille.objects.create(
            appel_offre=self.ao,
            libelle="BPU signe",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            type_groupe=CritereGrille.TypeGroupe.ELIMINATOIRE,
            valide=True,
            parent=self.groupe,
        )

    def test_upload_form_no_public_price(self):
        user = Utilisateur.objects.create_user(
            username="sec",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        AppelOffre.objects.create(
            reference="AO-UPLOAD-001",
            titre="AO upload",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )
        self.client.force_login(user)

        response = self.client.get(reverse("upload_dossier"))

        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, 'name="prix_lu_publiquement"')

    def test_extractor_prefers_total_ttc(self):
        texte = """
        Bordereau des prix unitaires
        Prix unitaire : 12 000 F CFA
        Montant HT : 82 000 000 F CFA
        TVA : 16 500 000 F CFA
        Montant total de l'offre TTC : 98 500 000 F CFA TTC
        """

        resultat = extraire_prix_financier_structure(texte)

        self.assertEqual(resultat["statut"], "present")
        self.assertEqual(resultat["valeur"], Decimal("98500000"))
        self.assertEqual(resultat["devise"], "XOF")
        self.assertIn("Montant total", resultat["source"])

    def test_extractor_marks_ambiguous_price(self):
        texte = """
        Sous-total lot 1 TTC : 44 000 000 F CFA
        Sous-total lot 2 TTC : 52 000 000 F CFA
        Montant HT : 90 000 000 F CFA
        TVA : 16 200 000 F CFA
        """

        resultat = extraire_prix_financier_structure(texte)

        self.assertEqual(resultat["statut"], "incertain")
        self.assertIsNone(resultat["valeur"])

    def test_manual_financial_price_validation(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Prix Humain SARL",
            statut_conformite=Soumissionnaire.StatutConformite.CONFORME_TECHNIQUE,
            prix_financier_statut="incertain",
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Present",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("update_prix_financier", args=[candidat.id]),
            {
                "prix_lu_publiquement": "116230000",
                "prix_corrige": "116230000",
                "source": "Validation humaine : montant total TTC page 12",
            },
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        candidat.refresh_from_db()
        self.assertTrue(payload["success"])
        self.assertEqual(candidat.prix_corrige, Decimal("116230000"))
        self.assertEqual(candidat.prix_financier_statut, "corrige_humain")
        self.assertTrue(candidat.prix_financier_validation_humaine)
        self.assertEqual(payload["prix_financier_statut"], "corrige_humain")

    def test_upload_form_filters_ao(self):
        user = Utilisateur.objects.create_user(
            username="sec_filtre",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        attente = AppelOffre.objects.create(
            reference="AO-ATTENTE",
            titre="En attente",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.GRILLE_EN_ATTENTE,
        )
        cloture = AppelOffre.objects.create(
            reference="AO-CLOTURE",
            titre="Cloture",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.CLOTURE,
        )
        self.ao.statut = AppelOffre.Statut.EN_COURS
        self.ao.save(update_fields=["statut"])
        self.client.force_login(user)

        response = self.client.get(reverse("upload_dossier"))

        self.assertContains(response, self.ao.reference)
        self.assertNotContains(response, attente.reference)
        self.assertNotContains(response, cloture.reference)

    def test_upload_post_refuses_invalid_ao(self):
        user = Utilisateur.objects.create_user(
            username="sec_refus",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        self.client.force_login(user)

        for statut in [AppelOffre.Statut.GRILLE_EN_ATTENTE, AppelOffre.Statut.CLOTURE]:
            self.ao.statut = statut
            self.ao.save(update_fields=["statut"])
            response = self.client.post(
                reverse("upload_dossier"),
                {
                    "appel_offre_id": str(self.ao.id),
                    "nom_entreprise": f"Entreprise {statut}",
                    "fichier": SimpleUploadedFile(
                        "dossier.pdf",
                        b"%PDF-1.4 test",
                        content_type="application/pdf",
                    ),
                },
            )
            self.assertRedirects(response, reverse("upload_dossier"))
            self.assertFalse(
                Soumissionnaire.objects.filter(
                    nom_entreprise=f"Entreprise {statut}"
                ).exists()
            )

    @patch("soumissionnaires.views.extraire_donnees_candidat")
    @patch("soumissionnaires.views.extraire_texte")
    def test_upload_post_allows_valid_ao(
        self,
        extraire_texte_mock,
        extraire_donnees_mock,
    ):
        user = Utilisateur.objects.create_user(
            username="sec_autorise",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        self.ao.statut = AppelOffre.Statut.EN_COURS
        self.ao.save(update_fields=["statut"])
        extraire_texte_mock.return_value = "Texte exploitable " * 10
        self.client.force_login(user)

        response = self.client.post(
            reverse("upload_dossier"),
            {
                "appel_offre_id": str(self.ao.id),
                "nom_entreprise": "Entreprise Autorisee",
                "fichier": SimpleUploadedFile(
                    "dossier.pdf",
                    b"%PDF-1.4 test",
                    content_type="application/pdf",
                ),
            },
        )

        self.assertRedirects(response, reverse("traitement_dossiers", args=[self.ao.id]))
        self.assertTrue(
            Soumissionnaire.objects.filter(
                appel_offre=self.ao,
                nom_entreprise="Entreprise Autorisee",
                depose_par=user,
            ).exists()
        )
        extraire_donnees_mock.assert_called_once()

    def test_candidate_details_include_justification(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Detail SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
            justification_ia="OCR seul, verification humaine requise",
        )
        self.client.force_login(self.user)

        with self.assertNumQueries(6):
            response = self.client.get(
                reverse("soumissionnaire_details", args=[candidat.id])
            )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["criteres"][0]["valeur_extraite"], "Incertain")
        self.assertIn("verification humaine", payload["criteres"][0]["justification_ia"])
        self.assertEqual(len(payload["criteres_a_verifier"]), 1)
        self.assertEqual(payload["criteres_a_verifier"][0]["valeur_extraite"], "Incertain")

    def test_ranking_page_renders_modal(self):
        Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Modal SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("classement", args=[self.ao.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "validationFooter")
        self.assertContains(response, "btn-validation-action")
        self.assertContains(response, "Validation des elements a verifier")

    def test_manual_confirmation_present(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Manuel SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
            justification_ia="Signature non confirmee par OCR",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "present"},
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        donnee = candidat.donnees_extraites.get()
        self.assertEqual(donnee.valeur_extraite, "Present")
        self.assertIn("Decision humaine", donnee.justification_ia)
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.A_VERIFIER)

    def test_manual_incertian_marks_candidate_a_verifier(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Incertain SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.RECEVABLE,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="Lecture incertaine page 4",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "incertain", "motif": "Information a confirmer"},
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.RECEVABLE)
        self.assertEqual(candidat.statut_final, Soumissionnaire.StatutFinal.EN_COURS)

    def test_manual_incertian_without_reason_is_refused(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Incertain sans motif SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.RECEVABLE,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="Lecture incertaine page 4",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "incertain"},
        )

        self.assertEqual(response.status_code, 400)
        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.RECEVABLE)

    def test_manual_confirmation_keeps_ai_value(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Confirmation Element SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Attestation fiscale fournie",
            justification_ia="Lecture incertaine page 4",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "confirmer",
                "donnee_id": str(donnee.id),
                "correction_ia": "1",
            },
        )

        self.assertEqual(response.status_code, 200)
        donnee.refresh_from_db()
        self.assertEqual(donnee.valeur_extraite, "Attestation fiscale fournie")
        self.assertIn("CONFIRME", donnee.justification_ia)

    def test_manual_correction_updates_ai_value(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Correction Element SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Valeur IA incertaine",
            justification_ia="Lecture incertaine page 7",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "corriger",
                "donnee_id": str(donnee.id),
                "correction_ia": "1",
                "valeur_corrigee": "Attestation fiscale valide",
                "motif": "Verification humaine sur la page 7",
            },
        )

        self.assertEqual(response.status_code, 200)
        donnee.refresh_from_db()
        self.assertEqual(donnee.valeur_extraite, "Attestation fiscale valide")
        self.assertIn("CORRIGE", donnee.justification_ia)

    def test_manual_absent_excludes_candidate(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Absent SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
            justification_ia="Document a verifier",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "absent", "motif": "Piece absente apres verification"},
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.A_VERIFIER)
        self.assertEqual(candidat.statut_final, Soumissionnaire.StatutFinal.EN_COURS)
        self.assertIsNone(candidat.rang)

    def test_manual_absent_is_listed_for_review(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Absent Liste SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="Document absent",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("soumissionnaire_details", args=[candidat.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["criteres_a_verifier"]), 1)
        self.assertEqual(payload["criteres_a_verifier"][0]["valeur_extraite"], "Absent")

    def test_present_is_hidden_from_review_list(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Present SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.RECEVABLE,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Present",
            justification_ia="Document confirme",
        )
        self.client.force_login(self.user)

        response = self.client.get(reverse("soumissionnaire_details", args=[candidat.id]))

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["criteres_a_verifier"], [])

    def test_manual_absent_requires_reason(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Absent Sans Motif SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
            justification_ia="Document a verifier",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "absent"},
        )

        self.assertEqual(response.status_code, 400)
        self.assertFalse(response.json()["success"])

    def test_manual_confirmation_updates_uncertain_data(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="OCR SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="Signature/cachet non detecte par OCR seul",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "present"},
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        donnee = candidat.donnees_extraites.get()
        self.assertEqual(donnee.valeur_extraite, "Present")
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.A_VERIFIER)

    def test_human_confirmation_overrides_justification(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Trace SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
            justification_ia=(
                "Signature/cachet non detecte par OCR seul, information incertaine"
            ),
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "present"},
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        self.assertEqual(candidat.donnees_extraites.get().valeur_extraite, "Present")
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.A_VERIFIER)

    def test_ai_error_absent_to_present(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Correction SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.NON_RECEVABLE,
            statut_final=Soumissionnaire.StatutFinal.ECARTE,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="IA : document absent",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "present",
                "correction_ia": "1",
                "donnee_id": str(donnee.id),
                "motif": "Document present page 27",
            },
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.NON_RECEVABLE)

    def test_ai_error_present_to_absent(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Correction KO SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.RECEVABLE,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Present",
            justification_ia="IA : document present",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "absent",
                "correction_ia": "1",
                "donnee_id": str(donnee.id),
                "motif": "Document finalement absent",
            },
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.RECEVABLE)
        self.assertEqual(candidat.statut_final, Soumissionnaire.StatutFinal.EN_COURS)
        self.assertIsNone(candidat.rang)

    def test_non_authorized_user_cannot_validate(self):
        outsider = Utilisateur.objects.create_user(
            username="outsider",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Acces Bloque SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="Document absent",
        )
        self.client.force_login(outsider)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "present"},
        )

        self.assertEqual(response.status_code, 403)

    def test_ai_error_requires_reason(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Motif SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Absent",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "present",
                "correction_ia": "1",
                "donnee_id": str(donnee.id),
            },
        )

        self.assertEqual(response.status_code, 400)

    def test_decision_traceability_recorded(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Trace Historique SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "present",
                "donnee_id": str(donnee.id),
                "motif": "Document present",
            },
        )

        self.assertEqual(response.status_code, 200)
        historique = HistoriqueAction.objects.filter(
            appel_offre=self.ao,
            action="Validation humaine",
            details__contains=f"Soumissionnaire #{candidat.id}",
        ).get()
        self.assertEqual(historique.utilisateur, self.user)
        self.assertIsNotNone(historique.date_action)
        self.assertIn("Présent / conforme", historique.details)
        self.assertIn("Document present", historique.details)

    def test_decision_history_kept(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Historique SARL",
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        donnee = DonneeExtraite.objects.create(
            soumissionnaire=candidat,
            critere=self.critere,
            valeur_extraite="Incertain",
        )
        self.client.force_login(self.user)

        self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "present", "donnee_id": str(donnee.id), "motif": "Premier"},
        )
        self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {
                "decision": "absent",
                "correction_ia": "1",
                "donnee_id": str(donnee.id),
                "motif": "Second",
            },
        )

        historiques = HistoriqueAction.objects.filter(
            appel_offre=self.ao,
            details__contains=f"Soumissionnaire #{candidat.id}",
            utilisateur=self.user,
        )
        self.assertEqual(historiques.count(), 2)

    def test_correction_recalculates_rankings(self):
        candidat_a = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="A",
            prix_corrige=Decimal("10000000"),
        )
        DonneeExtraite.objects.create(
            soumissionnaire=candidat_a,
            critere=self.critere,
            valeur_extraite="Present",
        )
        candidat_b = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="B",
            prix_corrige=Decimal("9000000"),
        )
        donnee_b = DonneeExtraite.objects.create(
            soumissionnaire=candidat_b,
            critere=self.critere,
            valeur_extraite="Absent",
            justification_ia="IA : document absent",
        )
        cascade_complete(self.ao)
        candidat_b.refresh_from_db()
        self.assertIsNone(candidat_b.rang)
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat_b.id]),
            {
                "decision": "present",
                "correction_ia": "1",
                "donnee_id": str(donnee_b.id),
                "motif": "Document present",
            },
        )

        self.assertEqual(response.status_code, 200)
        candidat_b.refresh_from_db()
        self.assertEqual(candidat_b.rang, 1)

    def test_details_include_prices(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Prix SARL",
            prix_lu_publiquement=Decimal("45000000"),
            prix_corrige=Decimal("47000000"),
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("soumissionnaire_details", args=[candidat.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["prix_lu_publiquement_formate"], "45 000 000")
        self.assertEqual(payload["prix_corrige_formate"], "47 000 000")

    def test_details_hide_missing_prices(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Sans Prix SARL",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("soumissionnaire_details", args=[candidat.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertIsNone(payload["prix_lu_publiquement"])
        self.assertIsNone(payload["prix_lu_publiquement_formate"])
        self.assertIsNone(payload["prix_corrige"])
        self.assertIsNone(payload["prix_corrige_formate"])

    def test_manual_confirmation_creates_missing_data(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Ancien AO SARL",
            prix_corrige=Decimal("45000000"),
            statut_conformite=Soumissionnaire.StatutConformite.A_VERIFIER,
        )
        self.client.force_login(self.user)

        response = self.client.post(
            reverse("valider_manuellement", args=[candidat.id]),
            {"decision": "present", "motif": "Dossier confirme manuellement"},
        )

        self.assertEqual(response.status_code, 200)
        candidat.refresh_from_db()
        self.assertEqual(candidat.donnees_extraites.count(), 1)
        self.assertEqual(candidat.donnees_extraites.get().valeur_extraite, "Present")
        self.assertEqual(candidat.statut_conformite, Soumissionnaire.StatutConformite.A_VERIFIER)

    def test_details_include_missing_criteria(self):
        candidat = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Detail Ancien AO",
        )
        self.client.force_login(self.user)

        response = self.client.get(
            reverse("soumissionnaire_details", args=[candidat.id])
        )

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(len(payload["criteres"]), 1)
        self.assertEqual(payload["criteres"][0]["valeur_extraite"], "Non extrait")
        self.assertTrue(payload["criteres"][0]["donnee_manquante"])


class WorkflowSecretaireTests(TestCase):
    def setUp(self):
        self.secretaire = Utilisateur.objects.create_user(
            username="sec_workflow",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        self.evaluateur = Utilisateur.objects.create_user(
            username="eval_workflow",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )
        self.ao_valide = self._ao("AO-SEC-OK", AppelOffre.Statut.EN_COURS)
        self.ao_non_valide = self._ao("AO-SEC-NON", AppelOffre.Statut.GRILLE_EN_ATTENTE)
        self.ao_cloture = self._ao("AO-SEC-CLOTURE", AppelOffre.Statut.CLOTURE)
        self._grille_validee(self.ao_valide)
        self._grille_validee(self.ao_cloture)

    def _ao(self, reference, statut):
        return AppelOffre.objects.create(
            reference=reference,
            titre=f"Titre {reference}",
            date_publication="2026-01-01",
            statut=statut,
        )

    def _grille_validee(self, ao):
        groupe = CritereGrille.objects.create(
            appel_offre=ao,
            libelle="Administratif",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            valide=True,
        )
        return CritereGrille.objects.create(
            appel_offre=ao,
            libelle="Piece administrative",
            categorie=CritereGrille.Categorie.ADMINISTRATIF,
            valide=True,
            parent=groupe,
        )

    def _upload(self, ao=None):
        self.client.force_login(self.secretaire)
        return self.client.post(
            reverse("upload_dossier"),
            {
                "appel_offre_id": str((ao or self.ao_valide).id),
                "nom_entreprise": "Depot SARL",
                "fichier": SimpleUploadedFile(
                    "dossier.pdf",
                    b"%PDF-1.4 test",
                    content_type="application/pdf",
                ),
            },
        )

    def test_secretary_hides_unvalidated_ao(self):
        self.client.force_login(self.secretaire)

        response = self.client.get(reverse("upload_dossier"))

        self.assertNotContains(response, self.ao_non_valide.reference)

    def test_secretary_sees_validated_ao(self):
        self.client.force_login(self.secretaire)

        response = self.client.get(reverse("upload_dossier"))

        self.assertContains(response, self.ao_valide.reference)

    def test_secretary_hides_closed_ao(self):
        self.client.force_login(self.secretaire)

        response = self.client.get(reverse("upload_dossier"))

        self.assertNotContains(response, self.ao_cloture.reference)

    @patch("soumissionnaires.views.extraire_texte", return_value="")
    def test_upload_removes_ao_from_queue(self, _extraire_texte):
        self._upload()

        response = self.client.get(reverse("upload_dossier"))

        self.assertNotContains(response, self.ao_valide.reference)

    @patch("soumissionnaires.views.extraire_texte", return_value="")
    def test_upload_appears_in_my_deposits(self, _extraire_texte):
        self._upload()

        response = self.client.get(reverse("mes_depots"))

        self.assertContains(response, self.ao_valide.reference)
        self.assertContains(response, "1")

    @patch("soumissionnaires.views.extraire_texte", return_value="")
    def test_secretary_sees_uploaded_names(self, _extraire_texte):
        self._upload()

        response = self.client.get(reverse("detail_depot", args=[self.ao_valide.id]))

        self.assertContains(response, "Depot SARL")
        self.assertNotContains(response, "Piece administrative")
        self.assertNotContains(response, "Justification")

    @patch("soumissionnaires.views.extraire_texte", return_value="")
    def test_secretary_cannot_view_extracted_data(self, _extraire_texte):
        self._upload()
        soumissionnaire = Soumissionnaire.objects.get(nom_entreprise="Depot SARL")

        response = self.client.get(
            reverse("soumissionnaire_details", args=[soumissionnaire.id])
        )

        self.assertEqual(response.status_code, 403)

    def test_secretary_cannot_access_grid_url(self):
        self.client.force_login(self.secretaire)

        response = self.client.get(reverse("validation_grille", args=[self.ao_valide.id]))

        self.assertEqual(response.status_code, 403)

    def test_secretary_cannot_access_ranking_url(self):
        self.client.force_login(self.secretaire)

        response = self.client.get(reverse("classement", args=[self.ao_valide.id]))

        self.assertEqual(response.status_code, 302)

    def test_direct_upload_refused(self):
        response = self._upload(self.ao_non_valide)

        self.assertRedirects(response, reverse("upload_dossier"))
        self.assertFalse(Soumissionnaire.objects.filter(appel_offre=self.ao_non_valide).exists())

    @patch("soumissionnaires.views.extraire_texte", return_value="")
    def test_confirmation_after_upload(self, _extraire_texte):
        response = self._upload()

        self.assertRedirects(response, reverse("traitement_dossiers", args=[self.ao_valide.id]))
        response = self.client.get(reverse("traitement_dossiers", args=[self.ao_valide.id]))
        self.assertContains(response, "Upload terminé")
        self.assertContains(response, "transmis à l'évaluation")

    def test_evaluator_keeps_evaluation_access(self):
        self.client.force_login(self.evaluateur)

        response = self.client.get(reverse("validation_grille", args=[self.ao_valide.id]))

        self.assertEqual(response.status_code, 200)
        self.assertContains(response, "Piece administrative")


class AccessControlTests(TestCase):
    def setUp(self):
        self.secretaire = Utilisateur.objects.create_user(
            username="sec_access",
            password="secret",
            role=Utilisateur.Role.SECRETAIRE,
        )
        self.evaluateur = Utilisateur.objects.create_user(
            username="eval_access",
            password="secret",
            role=Utilisateur.Role.EVALUATEUR,
        )
        self.president = Utilisateur.objects.create_user(
            username="pres_access",
            password="secret",
            role=Utilisateur.Role.PRESIDENT,
        )
        self.ao = AppelOffre.objects.create(
            reference="AO-ACCESS-001",
            titre="AO access",
            date_publication="2026-01-01",
            statut=AppelOffre.Statut.EN_COURS,
        )
        self.soumissionnaire = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Acces SARL",
        )

    def test_api_details_blocks_secretary(self):
        self.client.force_login(self.secretaire)
        response = self.client.get(reverse("soumissionnaire_details", args=[self.soumissionnaire.id]))
        self.assertEqual(response.status_code, 403)

    def test_api_details_allows_evaluator(self):
        self.client.force_login(self.evaluateur)
        response = self.client.get(reverse("soumissionnaire_details", args=[self.soumissionnaire.id]))
        self.assertEqual(response.status_code, 200)

    def test_delete_soumissionnaire_blocks_secretary(self):
        self.client.force_login(self.secretaire)
        response = self.client.post(reverse("supprimer_soumissionnaire", args=[self.soumissionnaire.id]))
        self.assertEqual(response.status_code, 403)
        self.assertTrue(Soumissionnaire.objects.filter(id=self.soumissionnaire.id).exists())

    def test_delete_soumissionnaire_allows_evaluator(self):
        self.client.force_login(self.evaluateur)
        response = self.client.post(reverse("supprimer_soumissionnaire", args=[self.soumissionnaire.id]))
        self.assertEqual(response.status_code, 200)
        self.assertFalse(Soumissionnaire.objects.filter(id=self.soumissionnaire.id).exists())

    def test_update_attributaire_blocks_evaluator(self):
        self.client.force_login(self.evaluateur)
        response = self.client.post(
            reverse("update_attributaire", args=[self.ao.id]),
            {
                "adresse": "Rue 1",
                "telephone": "90000000",
                "email": "test@example.com",
                "nationalite": "Togolaise",
                "beneficiaires": "X",
            },
        )
        self.assertRedirects(response, reverse("home"))

    def test_update_attributaire_allows_secretary(self):
        retenu = Soumissionnaire.objects.create(
            appel_offre=self.ao,
            nom_entreprise="Retenu SARL",
            statut_final=Soumissionnaire.StatutFinal.RETENU,
        )
        self.client.force_login(self.secretaire)
        response = self.client.post(
            reverse("update_attributaire", args=[self.ao.id]),
            {
                "adresse": "Rue 1",
                "telephone": "90000000",
                "email": "test@example.com",
                "nationalite": "Togolaise",
                "beneficiaires": "X",
            },
        )
        self.assertRedirects(response, reverse("detail_ao", args=[self.ao.id]))
        retenu.refresh_from_db()
        self.assertEqual(retenu.adresse, "Rue 1")
