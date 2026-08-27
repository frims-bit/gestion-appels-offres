from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("soumissionnaires", "0008_soumissionnaire_date_depot_dossier_and_more"),
    ]

    operations = [
        migrations.AddField(
            model_name="soumissionnaire",
            name="prix_financier_brut",
            field=models.CharField(blank=True, default="", max_length=255),
        ),
        migrations.AddField(
            model_name="soumissionnaire",
            name="prix_financier_devise",
            field=models.CharField(blank=True, default="", max_length=20),
        ),
        migrations.AddField(
            model_name="soumissionnaire",
            name="prix_financier_source",
            field=models.TextField(blank=True, default=""),
        ),
        migrations.AddField(
            model_name="soumissionnaire",
            name="prix_financier_statut",
            field=models.CharField(blank=True, default="absent", max_length=20),
        ),
        migrations.AddField(
            model_name="soumissionnaire",
            name="prix_financier_validation_humaine",
            field=models.BooleanField(default=False),
        ),
    ]
