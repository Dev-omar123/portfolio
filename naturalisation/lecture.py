"""Lecture d'une capture de question par OCR (Tesseract), sans IA.

Le texte est lu par Tesseract, puis rangé grâce aux couleurs de la page :
  - encadré vert        -> la bonne réponse
  - encadré rouge       -> la réponse choisie (fausse)
  - encadré bleu pâle   -> l'explication
  - texte du haut       -> la question
  - autres encadrés     -> les autres propositions

Utilisation directe pour tester une image :  python3 lecture.py capture.png
"""

import io
import re
import shutil
import sys
from pathlib import Path

import pytesseract
from PIL import Image

# Sous Windows, l'installeur ne met pas toujours Tesseract dans le PATH.
if not shutil.which("tesseract"):
    for candidate in (r"C:\Program Files\Tesseract-OCR\tesseract.exe",
                      r"C:\Program Files (x86)\Tesseract-OCR\tesseract.exe"):
        if Path(candidate).exists():
            pytesseract.pytesseract.tesseract_cmd = candidate

ZOOM = 2  # les petites polices d'écran se lisent mieux agrandies


class LectureError(Exception):
    """Erreur à montrer telle quelle à l'utilisateur."""


# --------------------------------------------------------------- les couleurs

def colour_of(r, g, b):
    """Classe un pixel de fond : 'green', 'red', 'blue' ou None."""
    if g >= 200 and g - r >= 20 and g - b >= 12:
        return "green"
    if r >= 200 and r - g >= 18 and r - b >= 18:
        return "red"
    if b >= 245 and b - r >= 10 and g - r >= 4:
        return "blue"
    return None


def find_bands(img):
    """Renvoie les bandes horizontales colorées : {'green': [(haut, bas), ...], ...}."""
    width, height = img.size
    step = max(1, width // 200)  # un pixel sur `step` suffit pour reconnaître un fond
    pixels = img.load()
    row_class = []
    for y in range(height):
        counts = {}
        samples = 0
        for x in range(0, width, step):
            samples += 1
            c = colour_of(*pixels[x, y])
            if c:
                counts[c] = counts.get(c, 0) + 1
        best = max(counts, key=counts.get) if counts else None
        row_class.append(best if best and counts[best] >= samples * 0.25 else None)

    # Les lignes de texte dense cassent un encadré en plusieurs morceaux :
    # on recolle les morceaux de même couleur séparés par un petit écart.
    bands = {"green": [], "red": [], "blue": []}
    max_gap = max(20, height // 30)
    start = 0
    for y in range(1, height + 1):
        if y == height or row_class[y] != row_class[start]:
            colour = row_class[start]
            if colour:
                same = bands[colour]
                between = row_class[same[-1][1]:start] if same else []
                if same and start - same[-1][1] <= max_gap and all(c is None for c in between):
                    same[-1] = (same[-1][0], y)
                else:
                    same.append((start, y))
            start = y
    return {c: [b for b in found if b[1] - b[0] >= 12] for c, found in bands.items()}


def crop_band(img, band):
    """Découpe un encadré coloré en rognant ses bords : la bordure serait lue comme un « | »."""
    top, bottom = band
    pixels = img.load()
    y = (top + bottom) // 2
    colour = max(("green", "red", "blue"),
                 key=lambda c: sum(colour_of(*pixels[x, y]) == c for x in range(0, img.width, 3)))
    xs = [x for x in range(img.width) if colour_of(*pixels[x, y]) == colour]
    left, right = (min(xs), max(xs)) if xs else (0, img.width)
    pad = max(4, img.width // 120)
    if right - left < pad * 6 or bottom - top < pad * 4:
        return img.crop((0, top, img.width, bottom)), top
    return img.crop((left + pad, top + pad // 2, right - pad, bottom - pad // 2)), top + pad // 2


# -------------------------------------------------------------------- le texte

def read_lines(img, offset=0):
    """Renvoie les lignes lues : [{'text', 'top', 'bottom', 'left'}], de haut en bas.
    `offset` est la position verticale de `img` dans la capture entière."""
    big = img.resize((img.width * ZOOM, img.height * ZOOM), Image.LANCZOS)
    try:
        data = pytesseract.image_to_data(big, lang="fra", config="--psm 6",
                                         output_type=pytesseract.Output.DICT)
    except pytesseract.TesseractNotFoundError:
        raise LectureError("Tesseract n'est pas installé (ou introuvable). Voir le README.")
    except pytesseract.TesseractError as e:
        if "fra" in str(e):
            raise LectureError("Le pack de langue français de Tesseract est manquant. Voir le README.")
        raise LectureError(f"Tesseract a échoué : {e}")

    lines = {}
    for i, word in enumerate(data["text"]):
        if not word.strip() or float(data["conf"][i]) < 0:
            continue
        key = (data["block_num"][i], data["par_num"][i], data["line_num"][i])
        line = lines.setdefault(key, {"words": [], "top": 10**9, "bottom": 0, "left": 10**9})
        line["words"].append(word)
        line["top"] = min(line["top"], data["top"][i] // ZOOM)
        line["bottom"] = max(line["bottom"], (data["top"][i] + data["height"][i]) // ZOOM)
        line["left"] = min(line["left"], data["left"][i] // ZOOM)
    result = [{"text": " ".join(l["words"]), "top": l["top"] + offset,
               "bottom": l["bottom"] + offset, "left": l["left"]}
              for l in lines.values()]
    return sorted(result, key=lambda l: l["top"])


def clean(text):
    text = re.sub(r"\s+", " ", text.replace("|", " ")).strip()
    text = re.sub(r"\s+([,.)])", r"\1", text)          # « mot , » -> « mot, »
    text = re.sub(r"([(])\s+", r"\1", text)
    return text.replace("’", "'")


def in_band(line, band):
    middle = (line["top"] + line["bottom"]) / 2
    return band[0] <= middle < band[1]


def group_paragraphs(lines):
    """Regroupe les lignes proches : une proposition peut tenir sur deux lignes."""
    groups = []
    for line in lines:
        height = line["bottom"] - line["top"]
        if groups and line["top"] - groups[-1][-1]["bottom"] < max(height, 14) * 0.9:
            groups[-1].append(line)
        else:
            groups.append([line])
    return [clean(" ".join(l["text"] for l in g)) for g in groups]


VERDICT = re.compile(r"(bonne|mauvaise)\s+r[ée]ponse|incorrect|correct\s*!", re.IGNORECASE)


def lire_capture(image_bytes):
    """Lit une capture et renvoie les champs de la fiche."""
    try:
        img = Image.open(io.BytesIO(image_bytes)).convert("RGB")
    except Exception:
        raise LectureError("Impossible d'ouvrir cette image.")

    bands = find_bands(img)
    # L'explication est le grand encadré bleu ; la question et les réponses sont au-dessus.
    # Les deux parties sont lues séparément : chacune a sa taille de police.
    blue = max(bands["blue"], key=lambda b: b[1] - b[0]) if bands["blue"] else None
    top_part = img.crop((0, 0, img.width, blue[0])) if blue else img
    upper = read_lines(top_part) if top_part.height > 10 else []

    # Les encadrés vert et rouge sont relus un par un : sur fond uni, le texte
    # coloré ressort mieux que dans la lecture d'ensemble.
    answer_bands = [b for b in bands["green"] + bands["red"] if not blue or b[1] <= blue[0]]
    upper = [l for l in upper if not any(in_band(l, b) for b in answer_bands)]
    for band in answer_bands:
        crop, offset = crop_band(img, band)
        upper += read_lines(crop, offset)
    upper.sort(key=lambda l: l["top"])
    if not upper:
        raise LectureError("Je n'ai pas trouvé de question lisible sur cette image.")

    def text_in(band_list):
        return group_paragraphs([l for l in upper if any(in_band(l, b) for b in band_list)])

    answer = " / ".join(text_in(bands["green"]))
    my_answer = " / ".join(text_in(bands["red"]))

    # Explication : on retire la ligne de verdict (« Bonne réponse ! ») et les
    # caractères parasites que Tesseract lit à la place des pictogrammes (✅ 💡 ❌).
    explanation = ""
    if blue:
        box = read_lines(*crop_band(img, blue))
        kept = [l["text"] for l in box if not (VERDICT.search(l["text"]) and len(l["text"]) < 40)]
        explanation = clean(" ".join(kept))
        explanation = re.sub(r"^(?:[^A-ZÀ-Ý\s]\S?\s+){1,2}(?=[A-ZÀ-Ý«(])", "", explanation)
        explanation = re.sub(r"(?<=[.!?»)])\s+\S{1,2}$", "", explanation)
        explanation = re.sub(r"^[^\wÀ-ÿ«(]+", "", explanation)

    # La question est collée à gauche ; les propositions sont en retrait dans leur encadré.
    margin = min(l["left"] for l in upper)
    question_lines = []
    for line in upper:
        coloured = any(in_band(line, b) for b in bands["green"] + bands["red"])
        if coloured or line["left"] > margin + img.width * 0.02:
            break
        question_lines.append(line)
    if not question_lines:  # mise en page inattendue : on prend la première ligne
        question_lines = upper[:1]
    question = clean(" ".join(l["text"] for l in question_lines))
    options = group_paragraphs(upper[len(question_lines):])

    return {
        "question": question,
        "answer": answer,
        "my_answer": my_answer,
        "options": "\n".join(options),
        "explanation": explanation,
    }


if __name__ == "__main__":
    for path in sys.argv[1:]:
        fiche = lire_capture(Path(path).read_bytes())
        for key, value in fiche.items():
            print(f"{key:12}: {value!r}")
