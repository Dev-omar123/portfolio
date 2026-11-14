# Carnet d'erreurs — Naturalisation

Colle la capture d'écran d'une question ratée : le script lit l'image et en
fait une fiche de révision (question, propositions, bonne réponse, ta réponse,
explication). Tout tourne sur ton PC, sans IA, sans compte et sans internet.

## Installation (une seule fois)

1. **Tesseract**, le moteur de lecture, avec le français :
   - Windows : installeur sur <https://github.com/UB-Mannheim/tesseract/wiki>.
     Pendant l'installation, ouvre « Additional language data » et coche **French**.
   - macOS : `brew install tesseract tesseract-lang`
   - Linux : `sudo apt install tesseract-ocr tesseract-ocr-fra`
2. Les deux modules Python :

   ```bash
   pip install -r requirements.txt
   ```

## Utilisation

```bash
python3 app.py
```

Le navigateur s'ouvre sur <http://127.0.0.1:8000>. Fais ta capture, puis
`Ctrl+V` sur la page (ou glisse une ou plusieurs images).

Pour vérifier la lecture d'une image sans lancer l'appli :

```bash
python3 lecture.py capture.png
```

## Comment la capture est lue

Le texte est lu par Tesseract, puis rangé d'après les couleurs de la page :

| Sur la capture        | Dans la fiche              |
|-----------------------|----------------------------|
| texte tout en haut    | la question                |
| encadré vert          | la bonne réponse           |
| encadré rouge         | ta réponse (fausse)        |
| autres encadrés       | les autres propositions    |
| encadré bleu pâle     | l'explication              |

Pour une bonne lecture, cadre la capture sur la question entière : de la
question jusqu'à la fin de l'explication, sans autre élément de la page.
Si un mot est mal lu, le bouton « Corriger » de la fiche permet de le reprendre.

Les fiches sont dans `data/erreurs.db` et les captures dans `data/images/` :
copie le dossier `data/` pour les sauvegarder ou changer de PC.
