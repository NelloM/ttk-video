from django.shortcuts import render
import core.generate as g
import os
import secrets
import re
from datetime import datetime, timedelta
from pathlib import Path
from core.sf_utitlities import notify_salesforce


ON_RENDER = bool(os.getenv("PORT"))  # Render (come altre PaaS) imposta PORT
HOST = os.getenv("HOST", "0.0.0.0" if ON_RENDER else "127.0.0.1")
PORT = int(os.getenv("PORT", "8000"))
PUBLIC_BASE_URL = (os.getenv("PUBLIC_BASE_URL") or os.getenv("RENDER_EXTERNAL_URL")
                   or f"http://localhost:{PORT}").rstrip("/")
APP_USER = os.getenv("APP_USER", "admin")
APP_PASSWORD = os.getenv("APP_PASSWORD", "")
TTL_HOURS = int(os.getenv("DOWNLOAD_TTL_HOURS", "72"))
OUT_ROOT = Path(os.getenv("OUTPUT_DIR", "output"))  # su Render: cartella del disco persistente
TOKENS_FILE = OUT_ROOT / "tokens.json"

JOBS: dict[str, dict] = {}
TOKENS: dict[str, dict] = {}  # token -> {video, expires, filename}; su disco, sopravvive ai riavvii

def stampa_valori(campo1, campo2):
    print(f"Campo 1: {campo1} | Campo 2: {campo2}", flush=True)


def index(request):
    messaggio = None
    if request.method == "POST":
        fmt = request.POST.get("campo1", "")
        niche = request.POST.get("campo2", "")
        stampa_valori(niche, fmt)
        content = g.produce_video(niche, fmt, OUT_ROOT, None,
                                  None)
        token = secrets.token_urlsafe(24)
        expires = datetime.now() + timedelta(hours=TTL_HOURS)
        slug = re.sub(r"[^a-z0-9]+", "-", content["title"].lower()).strip("-")[:60] or "video"
        TOKENS[token] = {"video": content["video_path"], "expires": expires.isoformat(),
                         "filename": f"{slug}.mp4"}
        
        url = f"{PUBLIC_BASE_URL}/d/{token}"

        print("Video pronto. Notifico Salesforce...", flush=True)
        try:
            notify_salesforce({
                "title": content["title"], "downloadUrl": url,
                "expiresAt": expires.strftime("%d/%m/%Y %H:%M"), "format": fmt, "niche": niche,
            })
            print("Notifica Salesforce inviata con successo.", flush=True)
        except Exception as e:  # il video resta comunque scaricabile dal link mostrato qui
            print(f"ERRORE: {e}", flush=True)

        messaggio = f"Video pronto: {content['video_path']}."
        print(messaggio, flush=True)
    return render(request, "core/index.html", {"messaggio": messaggio})
