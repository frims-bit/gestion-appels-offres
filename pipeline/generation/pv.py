import os
from docx import Document
from docx.shared import Pt, Inches, RGBColor
from docx.enum.text import WD_ALIGN_PARAGRAPH
from django.conf import settings
from django.core.files import File
from proces_verbaux.models import ProcesVerbal


def generer_pv(appel_offre):
    """
    Génère le PV d'attribution provisoire avec python-docx
    Cette version utilise python-docx directement, pas de template Word.
    """
    print(f"[PV] Génération pour AO: {appel_offre.reference}")

    # Récupérer les soumissionnaires
    conformes = appel_offre.soumissionnaires.exclude(rang=None).order_by('rang')
    ecartes = appel_offre.soumissionnaires.filter(statut_final='ecarte')
    attributaire = conformes.filter(statut_final='retenu').first()

    # Créer le document
    doc = Document()

    # ========== TITRE ==========
    titre = doc.add_heading('PROCES VERBAL D\'ATTRIBUTION PROVISOIRE', 0)
    titre.alignment = WD_ALIGN_PARAGRAPH.CENTER
    titre.runs[0].underline = True

    # ========== EN-TÊTE ==========
    doc.add_paragraph()

    infos = [
        ('AUTORITE CONTRACTANTE', 'Ministère de l\'Efficacité du Service Public'),
        ('Lomé, Le', '05/08/2026'),
        ('REFERENCE DE LA PROCEDURE', appel_offre.reference or 'Non défini'),
        ('DATE DE PUBLICATION', str(appel_offre.date_publication) if appel_offre.date_publication else 'Non définie'),
        ('OBJET DE LA PROCEDURE', appel_offre.titre or 'Non défini'),
        ('ALLOTISSEMENT', 'Lot unique'),
        ('NOMBRE DE SOUMISSIONNAIRES', str(appel_offre.soumissionnaires.count())),
        ('DELAI D\'EXECUTION', '45 jours'),
    ]

    for label, value in infos:
        p = doc.add_paragraph()
        p.add_run(f'{label} : ').bold = True
        p.add_run(str(value))

    doc.add_paragraph()

    # ========== TABLEAU DES CONFORMES ==========
    if conformes:
        doc.add_heading('SOUMISSIONNAIRES RECONNUS CONFORMES', level=1)

        table = doc.add_table(rows=1, cols=3)
        table.style = 'Table Grid'
        hdr = table.rows[0].cells
        hdr[0].text = 'SOUMISSIONNAIRES'
        hdr[1].text = 'Montants à l\'Ouverture (FCFA)'
        hdr[2].text = 'Montants corrigés (FCFA)'

        # Mettre en gras les en-têtes
        for cell in hdr:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True

        for c in conformes:
            row = table.add_row().cells
            row[0].text = c.nom_entreprise or 'Non défini'
            row[1].text = f"{c.prix_lu_publiquement or 0:,}".replace(',', ' ')
            row[2].text = f"{c.prix_corrige or 0:,}".replace(',', ' ')

        doc.add_paragraph()

    # ========== TABLEAU DES ÉCARTÉS ==========
    if ecartes:
        doc.add_heading('SOUMISSIONNAIRES NON RETENUS', level=1)

        table = doc.add_table(rows=1, cols=2)
        table.style = 'Table Grid'
        hdr = table.rows[0].cells
        hdr[0].text = 'SOUMISSIONNAIRES'
        hdr[1].text = 'MOTIFS DE REJET'

        for cell in hdr:
            for paragraph in cell.paragraphs:
                for run in paragraph.runs:
                    run.bold = True

        for e in ecartes:
            row = table.add_row().cells
            row[0].text = e.nom_entreprise or 'Non défini'
            row[1].text = e.motif_rejet or 'Non conforme'

        doc.add_paragraph()

    # ========== ATTRIBUTAIRE ==========
    doc.add_heading('ATTRIBUTAIRE', level=1)

    if attributaire:
        p = doc.add_paragraph()
        p.add_run('NOM ET ADRESSE DE L\'ATTRIBUTAIRE : ').bold = True
        p.add_run(f"{attributaire.nom_entreprise or 'Non défini'}, {attributaire.adresse or 'à compléter'}")

        p = doc.add_paragraph()
        p.add_run('Tél. : ').bold = True
        p.add_run(attributaire.telephone or 'à compléter')

        p = doc.add_paragraph()
        p.add_run('Email : ').bold = True
        p.add_run(attributaire.email or 'à compléter')

        p = doc.add_paragraph()
        p.add_run('Bénéficiaires effectifs de l\'entreprise : ').bold = True
        p.add_run(f"{attributaire.beneficiaires_effectifs or 'à compléter'} de nationalité {attributaire.nationalite or 'Togolaise'}")

        p = doc.add_paragraph()
        p.add_run('MONTANT D\'ATTRIBUTION DU MARCHE : ').bold = True
        p.add_run(f"{attributaire.prix_corrige or 0:,} F CFA TTC".replace(',', ' '))
    else:
        doc.add_paragraph('Aucun attributaire désigné')

    doc.add_paragraph()

    # ========== AUTRES INFORMATIONS ==========
    autres = [
        ('PART DU MARCHE SOUMISE A LA SOUS TRAITANCE', 'Sans objet'),
        ('PRISE EN COMPTE DE VARIANTES', 'Sans objet'),
        ('PROCEDURE DEROGATOIRE', 'Sans objet'),
    ]

    for label, value in autres:
        p = doc.add_paragraph()
        p.add_run(f'{label} : ').bold = True
        p.add_run(value)

    doc.add_paragraph()

    # ========== SIGNATURE ==========
    p = doc.add_paragraph()
    p.add_run('LA PERSONNE RESPONSABLE DES MARCHES PUBLICS').bold = True
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER

    # Ajouter des lignes vides pour la signature
    for _ in range(4):
        doc.add_paragraph()

    p = doc.add_paragraph()
    p.alignment = WD_ALIGN_PARAGRAPH.CENTER
    run = p.add_run('XXXXXX')
    run.underline = True

    # ========== SAUVEGARDE ==========
    ref_clean = appel_offre.reference.replace('/', '_').replace('\\', '_')
    temp_path = os.path.join(settings.MEDIA_ROOT, f'PV_{ref_clean}.docx')

    if not os.path.exists(settings.MEDIA_ROOT):
        os.makedirs(settings.MEDIA_ROOT)

    doc.save(temp_path)
    print(f"[PV] Fichier sauvegardé: {temp_path}")

    # Sauvegarder dans le modèle ProcesVerbal
    pv, created = ProcesVerbal.objects.get_or_create(appel_offre=appel_offre)
    with open(temp_path, 'rb') as f:
        pv.fichier.save(f'PV_{ref_clean}.docx', File(f))
    pv.statut = ProcesVerbal.Statut.BROUILLON
    pv.save()

    # Nettoyer le fichier temporaire
    if os.path.exists(temp_path):
        os.remove(temp_path)

    print("[PV] PV généré avec succès")
    return pv