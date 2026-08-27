from django.db import models
from utilisateurs.models import Utilisateur


class AppelOffre(models.Model):
    class Statut(models.TextChoices):
        BROUILLON = "brouillon", "Brouillon"
        GRILLE_EN_ATTENTE = "grille_en_attente", "Grille en attente de validation"
        EN_COURS = "en_cours", "En cours d'évaluation"
        JUGE = "juge", "Jugé"
        CLOTURE = "cloture", "Clôturé"

    reference = models.CharField(max_length=100, unique=True)
    titre = models.CharField(max_length=255)
    date_publication = models.DateField()
    statut = models.CharField(max_length=30, choices=Statut.choices, default=Statut.BROUILLON)
    document_source = models.FileField(upload_to="cahiers_des_charges/", null=True, blank=True)
    texte_extrait = models.TextField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    def __str__(self):
        return f"{self.reference} - {self.titre}"

    @property
    def grille_evaluee(self):
        criteres = self.criteres.all()
        return criteres.exists() and not criteres.filter(valide=False).exists()


class CritereGrille(models.Model):
    class Categorie(models.TextChoices):
        TECHNIQUE = "technique", "Technique"
        FINANCIER = "financier", "Financier"
        ADMINISTRATIF = "administratif", "Administratif"

    class Source(models.TextChoices):
        IA = "ia", "Généré par l'IA"
        MANUEL = "manuel", "Ajouté manuellement"

    class TypeGroupe(models.TextChoices):
        ELIMINATOIRE = "eliminatoire", "Éliminatoire"
        NOTABLE = "notable", "Notable"

    appel_offre = models.ForeignKey(AppelOffre, on_delete=models.CASCADE, related_name="criteres")
    libelle = models.CharField(max_length=255)
    categorie = models.CharField(max_length=20, choices=Categorie.choices)
    type_groupe = models.CharField(max_length=20, choices=TypeGroupe.choices, default=TypeGroupe.NOTABLE)
    note_max = models.DecimalField(max_digits=6, decimal_places=2, default=0)
    source = models.CharField(max_length=10, choices=Source.choices, default=Source.IA)
    valide = models.BooleanField(default=False)
    parent = models.ForeignKey(
        'self',
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name='sous_criteres'
    )

    def __str__(self):
        return self.libelle

    def est_un_groupe(self):
        return self.parent is None

    def est_un_sous_critere(self):
        return self.parent is not None

    def get_note_totale(self):
        if self.est_un_groupe():
            return sum(sc.note_max for sc in self.sous_criteres.all())
        return self.note_max

    def get_note_totale_validee(self):
        if self.est_un_groupe():
            return sum(sc.note_max for sc in self.sous_criteres.filter(valide=True))
        return self.note_max if self.valide else 0

    class Meta:
        ordering = ['parent__id', 'id']
