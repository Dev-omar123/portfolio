#!/usr/bin/env python3
"""Carnet d'erreurs — révision pour l'entretien de naturalisation.

Tu colles la capture d'écran d'une question ratée ; le script lit l'image par
OCR (Tesseract, sans IA et sans internet) et en tire une fiche de révision :
question, propositions, bonne réponse, ta réponse et explication.

Installation : voir README.md (Tesseract + pip install -r requirements.txt)
Lancement    : python3 app.py        (puis http://127.0.0.1:8000)

Tout reste en local dans le dossier data/ (SQLite + captures).
"""

import base64
import json
import re
import sqlite3
import sys
import threading
import uuid
import webbrowser
from datetime import datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from lecture import LectureError, lire_capture

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMG_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "erreurs.db"
HTML_PATH = BASE_DIR / "index.html"

MAX_IMAGE = 15 * 1024 * 1024
IMG_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
TEXT_FIELDS = ("question", "options", "answer", "my_answer", "explanation", "key_point", "theme")
DATA_URL = re.compile(r"^data:(image/[a-z]+);base64,(.+)$", re.DOTALL)
IMG_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp|gif)$")


# ------------------------------------------------------------ base de données

def db():
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def init_db():
    IMG_DIR.mkdir(parents=True, exist_ok=True)
    with db() as conn:
        conn.execute(
            """CREATE TABLE IF NOT EXISTS erreurs (
                   id          INTEGER PRIMARY KEY AUTOINCREMENT,
                   question    TEXT NOT NULL DEFAULT '',
                   answer      TEXT NOT NULL DEFAULT '',
                   explanation TEXT NOT NULL DEFAULT '',
                   theme       TEXT NOT NULL DEFAULT '',
                   image       TEXT,
                   mastered    INTEGER NOT NULL DEFAULT 0,
                   created_at  TEXT NOT NULL
               )"""
        )
        # Colonnes ajoutées après la première version : on met à niveau une base existante.
        existing = {r["name"] for r in conn.execute("PRAGMA table_info(erreurs)")}
        for column in ("my_answer", "key_point", "options"):
            if column not in existing:
                conn.execute(f"ALTER TABLE erreurs ADD COLUMN {column} TEXT NOT NULL DEFAULT ''")


def delete_image(name):
    if name and IMG_NAME.match(name):
        (IMG_DIR / name).unlink(missing_ok=True)


# -------------------------------------------------------------- serveur local

class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # terminal silencieux
        pass

    def send(self, status, body, ctype="application/json; charset=utf-8", cache=None):
        if isinstance(body, (dict, list)):
            body = json.dumps(body, ensure_ascii=False).encode()
        self.send_response(status)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(body)))
        if cache:
            self.send_header("Cache-Control", cache)
        self.end_headers()
        self.wfile.write(body)

    def error(self, status, message):
        self.send(status, {"error": message})

    def read_json(self):
        length = int(self.headers.get("Content-Length") or 0)
        if length > MAX_IMAGE * 2:
            raise ValueError("Image trop lourde (15 Mo maximum).")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Requête invalide.")
        return data

    def item_id(self):
        m = re.fullmatch(r"/api/erreurs/(\d+)", self.path)
        return int(m.group(1)) if m else None

    def do_GET(self):
        path = self.path.split("?")[0]
        if path == "/":
            self.send(200, HTML_PATH.read_bytes(), "text/html; charset=utf-8")
        elif path == "/api/erreurs":
            with db() as conn:
                rows = conn.execute("SELECT * FROM erreurs ORDER BY id DESC").fetchall()
            self.send(200, [dict(r) for r in rows])
        elif path.startswith("/images/") and IMG_NAME.match(path[8:]):
            file = IMG_DIR / path[8:]
            if not file.exists():
                return self.error(404, "Image introuvable.")
            ctype = next(t for t, e in IMG_TYPES.items() if e == file.suffix[1:])
            self.send(200, file.read_bytes(), ctype, cache="max-age=31536000, immutable")
        else:
            self.error(404, "Page introuvable.")

    def do_POST(self):
        """Reçoit une capture, la lit par OCR, enregistre la fiche."""
        if self.path != "/api/erreurs":
            return self.error(404, "Page introuvable.")
        try:
            data = self.read_json()
            m = DATA_URL.match(data.get("image") or "")
            if not m or m.group(1) not in IMG_TYPES:
                raise ValueError("Format d'image non pris en charge (PNG, JPEG, WebP ou GIF).")
            media_type, b64_data = m.group(1), m.group(2)
            raw = base64.b64decode(b64_data)
            if len(raw) > MAX_IMAGE:
                raise ValueError("Image trop lourde (15 Mo maximum).")
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(400, str(e))
        try:
            fields = lire_capture(raw)
        except LectureError as e:
            return self.error(422, str(e))
        except Exception as e:  # ne jamais laisser la page sans réponse
            return self.error(500, f"Erreur inattendue pendant la lecture : {e}")

        image = f"{uuid.uuid4().hex}.{IMG_TYPES[media_type]}"
        (IMG_DIR / image).write_bytes(raw)
        with db() as conn:
            cur = conn.execute(
                f"INSERT INTO erreurs ({', '.join(TEXT_FIELDS)}, image, created_at)"
                f" VALUES ({', '.join('?' * len(TEXT_FIELDS))}, ?, ?)",
                (*(fields.get(f, "") for f in TEXT_FIELDS), image,
                 datetime.now().isoformat(timespec="seconds")),
            )
            row = conn.execute("SELECT * FROM erreurs WHERE id = ?", (cur.lastrowid,)).fetchone()
        self.send(201, dict(row))

    def do_PUT(self):
        """Corrige le texte d'une fiche ou change son état « maîtrisée »."""
        item_id = self.item_id()
        if item_id is None:
            return self.error(404, "Page introuvable.")
        try:
            data = self.read_json()
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(400, str(e))
        updates = {f: str(data[f] or "").strip() for f in TEXT_FIELDS if f in data}
        if "mastered" in data:
            updates["mastered"] = 1 if data["mastered"] else 0
        with db() as conn:
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE erreurs SET {sets} WHERE id = ?", (*updates.values(), item_id))
            row = conn.execute("SELECT * FROM erreurs WHERE id = ?", (item_id,)).fetchone()
        if not row:
            return self.error(404, "Fiche introuvable.")
        self.send(200, dict(row))

    def do_DELETE(self):
        item_id = self.item_id()
        if item_id is None:
            return self.error(404, "Page introuvable.")
        with db() as conn:
            row = conn.execute("SELECT image FROM erreurs WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return self.error(404, "Fiche introuvable.")
            conn.execute("DELETE FROM erreurs WHERE id = ?", (item_id,))
        delete_image(row["image"])
        self.send(200, {"ok": True})


def main():
    init_db()
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    url = f"http://{HOST}:{PORT}"
    print(f"Carnet d'erreurs ouvert sur {url}")
    print(f"Données enregistrées dans {DATA_DIR}")
    print("Ctrl+C pour arrêter.")
    threading.Timer(0.6, lambda: webbrowser.open(url)).start()
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nÀ bientôt !")


if __name__ == "__main__":
    main()
