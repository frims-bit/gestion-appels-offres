import os

from django.conf import settings


def normaliser_reference(reference):
    return (reference or "rapport").replace("/", "_").replace("\\", "_")


def nom_fichier_rapport(reference):
    return f"rapport_{normaliser_reference(reference)}.docx"


def chemin_rapport(reference):
    return os.path.join(settings.MEDIA_ROOT, "rapports", nom_fichier_rapport(reference))
