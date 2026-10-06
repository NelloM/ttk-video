#!/usr/bin/env python3
"""
Genera un video verticale (9:16) + titolo/descrizione/hashtag
pronti da caricare a mano su YouTube Shorts e TikTok.

Pipeline:
  Gemini (script + metadati) -> edge-tts (voce) -> Pexels/Pixabay (clip stock)
  -> MoviePy (montaggio + sottotitoli) -> output/<data>/video.mp4 + metadata.txt

Uso:
  python generate.py                       # argomento scelto dall'LLM
  python generate.py --topic "i polpi"     # argomento deciso da te
  python generate.py --text-only           # solo testi, niente video (per tarare il prompt)
"""
import argparse
import asyncio
import json
import os
import random
import re
import sys
import tempfile
import time
from datetime import datetime
from pathlib import Path

import edge_tts
import numpy as np
import requests
from dotenv import load_dotenv
from google import genai
from google.genai import errors as genai_errors
from google.genai import types
from moviepy import (
    AudioFileClip,
    CompositeVideoClip,
    ImageClip,
    VideoFileClip,
    concatenate_videoclips,
    vfx,
)
from PIL import Image, ImageDraw, ImageFont

load_dotenv()

W = int(os.getenv("VIDEO_WIDTH", "1080"))   # su istanze piccole usa 720x1280
H = int(os.getenv("VIDEO_HEIGHT", "1920"))
FPS = 30
NICHE = os.getenv("NICHE", "curiosità scientifiche sorprendenti")
LANG = os.getenv("LANGUAGE", "italiano")
VOICE = os.getenv("TTS_VOICE", "it-IT-IsabellaNeural")
TTS_RATE = os.getenv("TTS_RATE", "+5%")  # per suspense prova "-5%"
FORMAT = os.getenv("FORMAT", "curiosita").lower()  # vedi FORMATS
GEMINI_MODEL = os.getenv("GEMINI_MODEL", "gemini-3.8-flash,gemini-3.7-flash,gemini-3.5-flash-lite,gemini-3.1-flash-lite")


# --------------------------------------------------------------------------
# 1. Contenuto (script, titolo, hashtag) con Gemini
# --------------------------------------------------------------------------
RULES_CURIOSITA = """- Scena 1 = hook: massimo 12 parole, deve creare curiosità o sorpresa nei primi 2 secondi. Niente saluti, niente "ciao a tutti".
- 5-7 scene, narrazione totale 90-110 parole, frasi brevi e parlate.
- Solo fatti verificabili: niente statistiche o citazioni inventate; se non sei sicuro, resta generico.
- Ultima scena: chiusura che invita a rivedere il video o a seguire, senza essere insistente.
- search_query: 2-3 parole IN INGLESE che descrivono un'immagine concreta e filmabile (es. "ocean waves", "city traffic night"), mai concetti astratti."""

RULES_STORIA = """- Racconta UNA storia con tensione crescente, come un narratore che costruisce suspense. Durata 40-50 secondi.
- Scena 1 = hook: massimo 12 parole, apri nel mezzo del mistero con un dettaglio inquietante o una domanda (mai "oggi vi racconto").
- Struttura in 6-8 scene: hook -> dove e quando -> l'enigma si complica -> il dettaglio che non torna -> le teorie principali -> finale che lascia una domanda aperta o un ultimo colpo di scena. Chi guarda deve voler rivedere o commentare.
- Narrazione totale 110-130 parole, frasi brevissime, ritmo da racconto, una sola idea per scena.
- Distingui sempre ciò che è documentato da ciò che è ipotesi ("secondo gli storici", "si racconta che"). Non presentare teorie come fatti e NON inventare nomi, date, dialoghi o dettagli: se non sei sicuro, resta vago.
- Scegli misteri storici, archeologici, leggende o eventi inspiegati di molti decenni fa. Evita casi con vittime o persone coinvolte recenti, crimini recenti e tragedie recenti.
- search_query: 2-3 parole IN INGLESE per immagini atmosferiche e filmabili che sostengono il tono (es. "foggy forest", "old library", "stormy sea night", "abandoned building", "candle dark"), mai concetti astratti."""


RULES_MITI = """- Racconta UN mito o una leggenda (greca, romana, norrena, egizia, mesopotamica, celtica, giapponese, folklore italiano...) con suspense, 40-50 secondi. Cambia cultura rispetto ai titoli già pubblicati.
- Scena 1 = hook: massimo 12 parole, apri nel momento più drammatico o con un dettaglio inquietante (mai "oggi vi racconto").
- Struttura in 6-8 scene: hook -> chi sono i protagonisti e il loro mondo -> il desiderio o la colpa che mette tutto in moto -> l'escalation -> il punto di svolta -> il prezzo da pagare -> finale che lascia un'eco (cosa ci dice ancora oggi, o una domanda aperta).
- Narrazione totale 110-130 parole, frasi brevissime, tono da cantastorie, una sola idea per scena.
- Un mito è un racconto, non un fatto storico: usa formule come "secondo la tradizione", "narra il mito", "in una versione del racconto". Se esistono più versioni scegline una senza inventare dettagli. Non attribuire il racconto a un autore o a una fonte se non sei certo.
- Usa le forme italiane corrette di nomi e luoghi, non mescolare culture, NON inventare episodi, dialoghi o genealogie.
- Rispetta le fedi ancora praticate: niente ironia o toni denigratori.
- search_query: 2-3 parole IN INGLESE per immagini atmosferiche e filmabili (es. "stormy sea", "ancient ruins", "fire night", "marble statue", "misty mountains"), mai nomi di divinità o concetti astratti."""

FORMATS = {
    "curiosita": RULES_CURIOSITA,  # fatti rapidi e sorprendenti
    "storia": RULES_STORIA,        # misteri storici con suspense
    "miti": RULES_MITI,            # miti e leggende con suspense
}


def resolve_format(fmt: str | None) -> str:
    fmt = (fmt or "").strip().lower()
    if fmt not in FORMATS:
        raise ValueError(f"FORMAT '{fmt}' non valido. Valori ammessi: {', '.join(FORMATS)}")
    return fmt


def build_prompt(topic: str | None, past_titles: list[str], niche: str, fmt: str) -> str:
    topic_line = (
        f"Argomento: {topic}"
        if topic
        else "Scegli tu un argomento specifico e poco banale all'interno della nicchia."
    )
    avoid = "; ".join(past_titles[-30:]) or "nessuno"
    rules = FORMATS[resolve_format(fmt)]
    return f"""Sei un autore di contenuti short-form (YouTube Shorts / TikTok).
Nicchia: {niche}
Lingua dei testi: {LANG}
{topic_line}
Titoli già pubblicati (non ripetere argomenti simili): {avoid}

Crea UN video verticale. Regole:
{rules}
- title: massimo 70 caratteri, specifico, niente clickbait falso.
- description: 2 frasi + invito a seguire.
- hashtags: 5, pertinenti e di nicchia, in {LANG}, con il #.
- tiktok_caption: massimo 120 caratteri, senza hashtag.

Rispondi SOLO con JSON valido in questo formato:
{{"title": "...", "description": "...", "tiktok_caption": "...", "hashtags": ["#..."], "scenes": [{{"narration": "...", "search_query": "..."}}]}}"""


def validate_content(data: dict) -> dict:
    for key in ("title", "description", "tiktok_caption", "hashtags", "scenes"):
        if key not in data:
            raise ValueError(f"Risposta LLM incompleta: manca '{key}'")
    if not data["scenes"]:
        raise ValueError("Risposta LLM senza scene")
    for s in data["scenes"]:
        if not s.get("narration") or not s.get("search_query"):
            raise ValueError("Scena senza narration o search_query")
    data["hashtags"] = [
        "#" + re.sub(r"[^\w]", "", h.lstrip("#")) for h in data["hashtags"] if h.strip("# ")
    ][:5]
    data["title"] = data["title"].strip()[:100]
    return data


RETRYABLE = {500, 502, 503, 504}  # errori temporanei: vale la pena riprovare


def generate_content(topic: str | None, past_titles: list[str], niche: str, fmt: str) -> dict:
    client = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
    prompt = build_prompt(topic, past_titles, niche, fmt)
    models = [m.strip() for m in GEMINI_MODEL.split(",") if m.strip()]
    last_err = None
    for model in models:
        for attempt in range(1, 3):
            try:
                resp = client.models.generate_content(
                    model=model,
                    contents=prompt,
                    config=types.GenerateContentConfig(
                        response_mime_type="application/json", temperature=1.0
                    ),
                )
                return validate_content(json.loads(resp.text))
            except (ValueError, TypeError) as e:  # JSON malformato o vuoto: riprova subito
                last_err = e
            except genai_errors.APIError as e:
                last_err = e
                code = getattr(e, "code", None)
                if code in (404, 429):  # modello inesistente o quota di quel modello esaurita
                    print(f"    '{model}': errore {code}, provo il modello successivo")
                    break
                if code not in RETRYABLE:
                    raise  # chiave errata, quota esaurita, ecc.: inutile riprovare
                wait = 5 * 2 ** (attempt - 1) + random.random()
                print(f"    {model}: errore {code}, riprovo tra {wait:.0f}s ({attempt}/2)")
                time.sleep(wait)
        else:
            print(f"    '{model}' non risponde, passo al modello successivo")
    raise RuntimeError(f"Gemini non disponibile o risposta non valida: {last_err}")


# --------------------------------------------------------------------------
# 2. Voce (edge-tts)
# --------------------------------------------------------------------------
async def synth(text: str, path: Path) -> None:
    await edge_tts.Communicate(text, VOICE, rate=TTS_RATE).save(str(path))


# --------------------------------------------------------------------------
# 3. Clip stock da Pexels
# --------------------------------------------------------------------------
def fetch_clip_pexels(query: str, dest: Path, used_ids: set) -> Path:
    headers = {"Authorization": os.environ["PEXELS_API_KEY"]}
    fallbacks = [query, query.split()[0], "nature landscape"]
    for q in fallbacks:
        r = requests.get(
            "https://api.pexels.com/videos/search",
            headers=headers,
            params={"query": q, "orientation": "portrait", "per_page": 15},
            timeout=30,
        )
        r.raise_for_status()
        videos = [v for v in r.json().get("videos", []) if v["id"] not in used_ids]
        if not videos:
            continue
        video = random.choice(videos[:8])
        files = [
            f
            for f in video["video_files"]
            if f.get("file_type") == "video/mp4" and f.get("height", 0) >= f.get("width", 0)
        ]
        if not files:
            continue
        best = min(files, key=lambda f: abs(f["width"] - W))
        used_ids.add(video["id"])
        with requests.get(best["link"], stream=True, timeout=60) as dl:
            dl.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in dl.iter_content(1 << 20):
                    fh.write(chunk)
        return dest
    raise RuntimeError(f"Nessuna clip trovata per '{query}'")


def fetch_clip_pixabay(query: str, dest: Path, used_ids: set) -> Path:
    """Pixabay ha soprattutto clip orizzontali: vengono ritagliate al centro in 9:16."""
    for q in [query, query.split()[0], "nature landscape"]:
        r = requests.get(
            "https://pixabay.com/api/videos/",
            params={
                "key": os.environ["PIXABAY_API_KEY"],
                "q": q,
                "per_page": 20,
                "safesearch": "true",
            },
            timeout=30,
        )
        r.raise_for_status()
        hits = [h for h in r.json().get("hits", []) if ("pixabay", h["id"]) not in used_ids]
        if not hits:
            continue
        hit = random.choice(hits[:10])
        url = next(
            (hit["videos"][n]["url"] for n in ("medium", "small", "large", "tiny")
             if hit["videos"].get(n, {}).get("url")),
            None,
        )
        if not url:
            continue
        used_ids.add(("pixabay", hit["id"]))
        with requests.get(url, stream=True, timeout=60) as dl:
            dl.raise_for_status()
            with open(dest, "wb") as fh:
                for chunk in dl.iter_content(1 << 20):
                    fh.write(chunk)
        return dest
    raise RuntimeError(f"Nessuna clip Pixabay per '{query}'")


def fetch_clip(query: str, dest: Path, used_ids: set) -> Path:
    providers = []
    if os.getenv("PEXELS_API_KEY"):
        providers.append(fetch_clip_pexels)
    if os.getenv("PIXABAY_API_KEY"):
        providers.append(fetch_clip_pixabay)
    for fn in providers:
        try:
            return fn(query, dest, used_ids)
        except (RuntimeError, requests.RequestException) as e:
            print(f"    {fn.__name__} fallito: {e}")
    raise RuntimeError(f"Nessuna clip trovata per '{query}' (controlla le chiavi stock nel .env)")


# --------------------------------------------------------------------------
# 4. Montaggio
# --------------------------------------------------------------------------
def load_font(size: int):
    candidates = [
        os.getenv("FONT_PATH"),
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ]
    for p in candidates:
        if p and Path(p).exists():
            return ImageFont.truetype(p, size)
    return ImageFont.load_default(size=size)


def subtitle_array(text: str) -> np.ndarray:
    """Disegna il testo (bianco con bordo nero) su un'immagine RGBA trasparente."""
    font = load_font(int(72 * W / 1080))
    canvas = Image.new("RGBA", (W, int(420 * W / 1080)), (0, 0, 0, 0))
    draw = ImageDraw.Draw(canvas)
    max_w = W - int(160 * W / 1080)
    lines, line = [], ""
    for word in text.upper().split():
        test = f"{line} {word}".strip()
        if draw.textlength(test, font=font) <= max_w:
            line = test
        else:
            lines.append(line)
            line = word
    lines.append(line)
    line_h = int(90 * W / 1080)
    y = (canvas.height - line_h * len(lines)) // 2
    for ln in lines:
        x = (W - draw.textlength(ln, font=font)) // 2
        draw.text((x, y), ln, font=font, fill="white", stroke_width=max(2, int(7 * W / 1080)), stroke_fill="black")
        y += line_h
    return np.array(canvas)


def fill_vertical(clip):
    scale = max(W / clip.w, H / clip.h)
    clip = clip.resized(scale)
    return clip.cropped(x_center=clip.w / 2, y_center=clip.h / 2, width=W, height=H)


_OPEN_CLIPS: list = []  # tutte le clip sorgente aperte: su Windows vanno chiuse prima di cancellare i file


def close_all_clips() -> None:
    while _OPEN_CLIPS:
        try:
            _OPEN_CLIPS.pop().close()
        except Exception:
            pass


def build_scene(video_path: Path, audio_path: Path, narration: str):
    audio = AudioFileClip(str(audio_path))
    _OPEN_CLIPS.append(audio)
    dur = audio.duration + 0.25
    source = VideoFileClip(str(video_path))
    _OPEN_CLIPS.append(source)
    clip = source.without_audio()
    if clip.duration < dur:
        clip = clip.with_effects([vfx.Loop(duration=dur)])
    else:
        clip = clip.subclipped(0, dur)
    clip = fill_vertical(clip)

    # sottotitoli a blocchi di ~4 parole, tempo proporzionale ai caratteri
    words = narration.split()
    chunks = [" ".join(words[i : i + 4]) for i in range(0, len(words), 4)]
    total_chars = sum(len(c) for c in chunks) or 1
    layers, t = [clip], 0.0
    for c in chunks:
        d = dur * len(c) / total_chars
        layers.append(
            ImageClip(subtitle_array(c))
            .with_start(t)
            .with_duration(d)
            .with_position(("center", int(H * 0.62)))
        )
        t += d
    return CompositeVideoClip(layers, size=(W, H)).with_duration(dur).with_audio(audio)


# --------------------------------------------------------------------------
# 5. Output e storico
# --------------------------------------------------------------------------
def write_metadata(content: dict, out_dir: Path) -> None:
    (out_dir / "metadata.json").write_text(
        json.dumps(content, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    tags = " ".join(content["hashtags"])
    (out_dir / "metadata.txt").write_text(
        f"=== YOUTUBE SHORTS ===\n"
        f"Titolo:\n{content['title']}\n\n"
        f"Descrizione:\n{content['description']}\n\n{tags} #shorts\n\n"
        f"=== TIKTOK ===\n"
        f"Caption:\n{content['tiktok_caption']} {tags}\n",
        encoding="utf-8",
    )


def load_history(path: Path) -> list[str]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else []


def check_keys(need_stock: bool = True) -> None:
    if not os.getenv("GEMINI_API_KEY"):
        raise RuntimeError("Manca GEMINI_API_KEY nel file .env")
    if need_stock and not (os.getenv("PEXELS_API_KEY") or os.getenv("PIXABAY_API_KEY")):
        raise RuntimeError("Serve almeno una tra PEXELS_API_KEY e PIXABAY_API_KEY nel file .env")


def produce_video(niche: str, fmt: str, out_dir: Path, topic: str | None = None,
                  history_path: Path | None = None) -> dict:
    """Pipeline completa (usata da CLI e GUI). Ritorna i contenuti + 'video_path'."""
    check_keys()
    out_dir.mkdir(parents=True, exist_ok=True)
    history = load_history(history_path) if history_path else []

    print("1/4 Genero script e metadati con Gemini...")
    content = generate_content(topic, history, niche, fmt)
    write_metadata(content, out_dir)
    print(f"Titolo: {content['title']}")

    with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
        tmp = Path(tmp)
        used_ids: set = set()
        scenes = []
        try:
            n = len(content["scenes"])
            for i, sc in enumerate(content["scenes"], 1):
                print(f"2-3/4 Scena {i}/{n}: voce + clip '{sc['search_query']}'")
                audio_path = tmp / f"a{i}.mp3"
                asyncio.run(synth(sc["narration"], audio_path))
                video_path = fetch_clip(sc["search_query"], tmp / f"v{i}.mp4", used_ids)
                scenes.append(build_scene(video_path, audio_path, sc["narration"]))

            print("4/4 Montaggio finale (può richiedere qualche minuto)...")
            final = concatenate_videoclips(scenes, method="compose")
            final.write_videofile(
                str(out_dir / "video.mp4"),
                fps=FPS,
                codec="libx264",
                audio_codec="aac",
                preset=os.getenv("FFMPEG_PRESET", "medium"),
                threads=int(os.getenv("FFMPEG_THREADS", "2")),
                ffmpeg_params=["-pix_fmt", "yuv420p", "-movflags", "+faststart"],
                logger=None,
            )
        finally:
            for sc in scenes:
                sc.close()
            close_all_clips()  # libera i file temporanei (necessario su Windows)

    if history_path:
        history.append(content["title"])
        history_path.write_text(json.dumps(history, ensure_ascii=False, indent=2), encoding="utf-8")
    content["video_path"] = str(out_dir / "video.mp4")
    return content


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--topic", help="argomento specifico (default: lo sceglie l'LLM)")
    ap.add_argument("--niche", default=NICHE)
    ap.add_argument("--format", default=FORMAT, dest="fmt", help="curiosita | storia | miti")
    ap.add_argument("--out", default="output", help="cartella di output")
    ap.add_argument("--text-only", action="store_true", help="genera solo i testi")
    args = ap.parse_args()

    root = Path(args.out)
    out_dir = root / datetime.now().strftime("%Y-%m-%d_%H%M%S")
    try:
        if args.text_only:
            check_keys(need_stock=False)
            out_dir.mkdir(parents=True, exist_ok=True)
            content = generate_content(args.topic, load_history(root / "history.json"), args.niche, args.fmt)
            write_metadata(content, out_dir)
            print(f"Titolo: {content['title']}\nTesti salvati in {out_dir}/metadata.txt")
            return 0
        produce_video(args.niche, args.fmt, out_dir, args.topic, root / "history.json")
    except (RuntimeError, ValueError) as e:
        sys.exit(str(e))
    print(f"\nFatto. Video e testi in: {out_dir}/")
    return 0


if __name__ == "__main__":
    sys.exit(main())
