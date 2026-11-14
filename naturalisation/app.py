#!/usr/bin/env python3
"""Carnet d'erreurs — révision pour l'entretien de naturalisation.

Lancement :  python3 app.py        (puis http://127.0.0.1:8000)
Aucune dépendance : uniquement la bibliothèque standard de Python.
Les données restent en local dans le dossier data/ (SQLite + images).
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

HOST = "127.0.0.1"
PORT = int(sys.argv[1]) if len(sys.argv) > 1 else 8000

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
IMG_DIR = DATA_DIR / "images"
DB_PATH = DATA_DIR / "erreurs.db"
HTML_PATH = BASE_DIR / "index.html"

MAX_BODY = 25 * 1024 * 1024  # 25 Mo par requête
IMG_TYPES = {"image/png": "png", "image/jpeg": "jpg", "image/webp": "webp", "image/gif": "gif"}
TEXT_FIELDS = ("question", "answer", "explanation", "theme")
DATA_URL = re.compile(r"^data:(image/[a-z]+);base64,(.+)$", re.DOTALL)
IMG_NAME = re.compile(r"^[0-9a-f]{32}\.(png|jpg|webp|gif)$")


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


def save_image(data_url):
    """Enregistre une image reçue en data-URL et renvoie son nom de fichier."""
    m = DATA_URL.match(data_url or "")
    if not m or m.group(1) not in IMG_TYPES:
        raise ValueError("Format d'image non pris en charge (PNG, JPEG, WebP ou GIF).")
    name = f"{uuid.uuid4().hex}.{IMG_TYPES[m.group(1)]}"
    (IMG_DIR / name).write_bytes(base64.b64decode(m.group(2)))
    return name


def delete_image(name):
    if name and IMG_NAME.match(name):
        (IMG_DIR / name).unlink(missing_ok=True)


class Handler(BaseHTTPRequestHandler):
    def log_message(self, *args):  # terminal silencieux
        pass

    # ---------- utilitaires ----------
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
        if length > MAX_BODY:
            raise ValueError("Image trop lourde (25 Mo maximum).")
        data = json.loads(self.rfile.read(length) or b"{}")
        if not isinstance(data, dict):
            raise ValueError("Requête invalide.")
        return data

    def item_id(self):
        m = re.fullmatch(r"/api/erreurs/(\d+)", self.path)
        return int(m.group(1)) if m else None

    # ---------- routes ----------
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
            ext = file.suffix[1:]
            ctype = next(t for t, e in IMG_TYPES.items() if e == ext)
            self.send(200, file.read_bytes(), ctype, cache="max-age=31536000, immutable")
        else:
            self.error(404, "Page introuvable.")

    def do_POST(self):
        if self.path != "/api/erreurs":
            return self.error(404, "Page introuvable.")
        try:
            data = self.read_json()
            fields = {f: str(data.get(f) or "").strip() for f in TEXT_FIELDS}
            if not data.get("image") and not fields["question"]:
                raise ValueError("Ajoute au moins une capture d'écran ou le texte de la question.")
            image = save_image(data["image"]) if data.get("image") else None
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(400, str(e))
        with db() as conn:
            cur = conn.execute(
                "INSERT INTO erreurs (question, answer, explanation, theme, image, created_at)"
                " VALUES (?, ?, ?, ?, ?, ?)",
                (*fields.values(), image, datetime.now().isoformat(timespec="seconds")),
            )
            row = conn.execute("SELECT * FROM erreurs WHERE id = ?", (cur.lastrowid,)).fetchone()
        self.send(201, dict(row))

    def do_PUT(self):
        item_id = self.item_id()
        if item_id is None:
            return self.error(404, "Page introuvable.")
        try:
            data = self.read_json()
            updates = {f: str(data[f] or "").strip() for f in TEXT_FIELDS if f in data}
            if "mastered" in data:
                updates["mastered"] = 1 if data["mastered"] else 0
            new_image = save_image(data["image"]) if data.get("image") else None
        except (ValueError, json.JSONDecodeError) as e:
            return self.error(400, str(e))
        with db() as conn:
            row = conn.execute("SELECT * FROM erreurs WHERE id = ?", (item_id,)).fetchone()
            if not row:
                delete_image(new_image)
                return self.error(404, "Question introuvable.")
            if new_image:
                updates["image"] = new_image
            elif data.get("remove_image"):
                updates["image"] = None
            if updates:
                sets = ", ".join(f"{k} = ?" for k in updates)
                conn.execute(f"UPDATE erreurs SET {sets} WHERE id = ?", (*updates.values(), item_id))
            if "image" in updates:
                delete_image(row["image"])
            row = conn.execute("SELECT * FROM erreurs WHERE id = ?", (item_id,)).fetchone()
        self.send(200, dict(row))

    def do_DELETE(self):
        item_id = self.item_id()
        if item_id is None:
            return self.error(404, "Page introuvable.")
        with db() as conn:
            row = conn.execute("SELECT image FROM erreurs WHERE id = ?", (item_id,)).fetchone()
            if not row:
                return self.error(404, "Question introuvable.")
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
