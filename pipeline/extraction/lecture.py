"""
Extraction de texte depuis PDF et DOCX.
Simple et robuste : texte natif d'abord, OCR en secours si besoin.
"""

import os
import warnings

# On masque les warnings inutiles de transformers/tensorflow au démarrage
warnings.filterwarnings("ignore")

# === Imports optionnels (on ne plante pas si un module manque) ===
try:
    import pdfplumber
except ImportError:
    pdfplumber = None

try:
    from docx import Document
except ImportError:
    Document = None

# docTR : optionnel, uniquement si le PDF est scanné (pas de texte natif)
try:
    from doctr.io import DocumentFile
    from doctr.models import ocr_predictor
    DOCTR_OK = True
except ImportError:
    DOCTR_OK = False


# Le modèle OCR est chargé UNIQUEMENT quand on en a besoin.
# Comme ça, Django démarre vite. Le premier PDF scanné prendra 10-15s
# pour charger le modèle, mais les suivants seront instantanés.
_ocr_model = None

def _get_ocr_model():
    """Charge le modèle OCR docTR la première fois qu'on l'utilise."""
    global _ocr_model
    if _ocr_model is None and DOCTR_OK:
        print("[OCR] Chargement du modèle (première utilisation, patience...)")
        _ocr_model = ocr_predictor(
            det_arch='db_mobilenet_v3_large',
            reco_arch='crnn_mobilenet_v3_small',
            pretrained=True
        )
    return _ocr_model


def extraire_texte(chemin):
    """
    Point d'entrée principal.
    Détecte l'extension et appelle la bonne fonction.
    """
    if not os.path.exists(chemin):
        return f"[Erreur] Fichier introuvable : {chemin}"

    extension = os.path.splitext(chemin)[1].lower()

    if extension == '.pdf':
        return _extraire_pdf(chemin)
    elif extension == '.docx':
        return _extraire_docx(chemin)
    else:
        return f"[Erreur] Format non supporté : {extension}"


def _extraire_pdf(chemin):
    """
    Extrait le texte d'un PDF.
    Étape 1 : on essaie de lire le texte natif (rapide et précis).
    Étape 2 : si le PDF est scanné/image, on passe par l'OCR.
    """
    # Vérifie que pdfplumber est installé
    if pdfplumber is None:
        return "[Erreur] pdfplumber non installé. Faites : pip install pdfplumber"

    texte = ""

    # === ÉTAPE 1 : texte natif (la plupart des PDF) ===
    try:
        with pdfplumber.open(chemin) as pdf:
            for page in pdf.pages:
                page_text = page.extract_text() or ""
                texte += page_text + "\n"

        # Si on a trouvé du texte, on le retourne tout de suite
        if texte.strip():
            return texte

    except Exception as e:
        print(f"[PDF] Problème lecture texte natif : {e}")

    # === ÉTAPE 2 : OCR (pour les PDF scannés / images) ===
    if not DOCTR_OK:
        return (
            "[Erreur] Ce PDF semble être une image scannée (pas de texte natif). "
            "Installez docTR pour lire ces fichiers : pip install python-doctr"
        )

    print("[PDF] Pas de texte natif détecté, lancement de l'OCR...")
    return _extraire_par_ocr(chemin)


def _extraire_par_ocr(chemin):
    """Lit un PDF scanné avec docTR (directement, sans pdf2image)."""
    try:
        model = _get_ocr_model()
        doc = DocumentFile.from_pdf(chemin)
        result = model(doc)

        texte = ""
        for page in result.pages:
            for block in page.blocks:
                for line in block.lines:
                    for word in line.words:
                        texte += word.value + " "
                    texte += "\n"      # Fin de ligne
                texte += "\n"          # Fin de bloc
            texte += "\n"              # Fin de page

        return texte if texte.strip() else "[OCR] Aucun texte détecté sur ce document."

    except Exception as e:
        return f"[Erreur OCR] Impossible de lire le PDF : {e}"


def _extraire_docx(chemin):
    """Extrait le texte d'un DOCX (paragraphes + tableaux)."""
    if Document is None:
        return "[Erreur] python-docx non installé. Faites : pip install python-docx"

    try:
        doc = Document(chemin)
        lignes = []

        # --- Paragraphes ---
        for para in doc.paragraphs:
            if para.text.strip():
                lignes.append(para.text.strip())

        # --- Tableaux (y compris imbriqués) ---
        for idx, table in enumerate(doc.tables, start=1):
            lignes.append(f"\n--- Tableau {idx} ---")
            for row in table.rows:
                # On récupère le texte de chaque cellule
                cells = [cell.text.strip() for cell in row.cells]
                # On ignore les lignes totalement vides
                if any(cells):
                    lignes.append(" | ".join(cells))

        texte = "\n".join(lignes)
        return texte if texte.strip() else "[DOCX] Document vide."

    except Exception as e:
        return f"[Erreur DOCX] Impossible de lire le fichier : {e}"