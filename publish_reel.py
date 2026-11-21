#!/usr/bin/env python3
"""
Alfa Digital — Auto-Publisher de Reels (Google Drive -> ElevenLabs -> FFmpeg -> R2 -> Instagram Graph API)

Lee la carpeta raíz en Google Drive ("Alfa Digital — Reels Automáticos"), busca subcarpetas
que contengan:
  - video.mp4
  - portada.png
  - guion.txt
  - caption.txt
y que NO tengan un archivo "PUBLISHED.txt".

Por cada subcarpeta pendiente:
  1. Genera el audio con ElevenLabs API a partir de guion.txt.
  2. Mezcla la voz (-14 LUFS) sobre el video.mp4 con ffmpeg.
  3. Sube el video final y la portada a Cloudflare R2 (URL pública requerida por Meta).
  4. Publica el Reel en Instagram vía Instagram Graph API.
  5. Crea "PUBLISHED.txt" en la subcarpeta de Drive para evitar duplicados.
"""

import io
import json
import os
import subprocess
import tempfile
import time
from pathlib import Path

import boto3
import requests
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.http import MediaIoBaseDownload, MediaIoBaseUpload

# ==============================================================================
# CONFIGURACIÓN DESDE VARIABLES DE ENTORNO (GitHub Secrets)
# ==============================================================================
DRIVE_FOLDER_ID     = os.environ["DRIVE_FOLDER_ID"]
GOOGLE_SA_JSON      = os.environ["GOOGLE_SERVICE_ACCOUNT_JSON"]

ELEVENLABS_API_KEY  = os.environ["ELEVENLABS_API_KEY"]
ELEVENLABS_VOICE_ID = os.environ["ELEVENLABS_VOICE_ID"]
ELEVENLABS_MODEL_ID = os.environ.get("ELEVENLABS_MODEL_ID", "eleven_multilingual_v2")

R2_ENDPOINT_URL     = os.environ["R2_ENDPOINT_URL"]       # ej: https://<account_id>.r2.cloudflarestorage.com
R2_ACCESS_KEY_ID    = os.environ["R2_ACCESS_KEY_ID"]
R2_SECRET_ACCESS_KEY = os.environ["R2_SECRET_ACCESS_KEY"]
R2_BUCKET_NAME      = os.environ["R2_BUCKET_NAME"]
R2_PUBLIC_BASE_URL  = os.environ["R2_PUBLIC_BASE_URL"].rstrip("/")  # ej: https://pub-xxxx.r2.dev

IG_USER_ID          = os.environ["IG_USER_ID"]
IG_ACCESS_TOKEN     = os.environ["IG_ACCESS_TOKEN"]
GRAPH_API_VERSION   = os.environ.get("GRAPH_API_VERSION", "v21.0")

DRY_RUN             = os.environ.get("DRY_RUN", "false").lower() == "true"


def get_drive_service():
    creds_info = json.loads(GOOGLE_SA_JSON)
    creds = service_account.Credentials.from_service_account_info(
        creds_info, scopes=["https://www.googleapis.com/auth/drive"]
    )
    return build("drive", "v3", credentials=creds)


def list_children(service, folder_id):
    q = f"'{folder_id}' in parents and trashed = false"
    res = service.files().list(
        q=q,
        fields="files(id, name, mimeType, createdTime)",
        orderBy="createdTime asc",
        pageSize=50,
    ).execute()
    return res.get("files", [])


def download_file(service, file_id, dest_path: Path):
    request = service.files().get_media(fileId=file_id)
    with open(dest_path, "wb") as fh:
        downloader = MediaIoBaseDownload(fh, request)
        done = False
        while not done:
            _, done = downloader.next_chunk()


def mark_published(service, folder_id, ig_media_id: str):
    content = f"Published to Instagram\nmedia_id={ig_media_id}\ntimestamp={time.strftime('%Y-%m-%dT%H:%M:%SZ', time.gmtime())}\n"
    media = MediaIoBaseUpload(io.BytesIO(content.encode("utf-8")), mimetype="text/plain")
    service.files().create(
        body={"name": "PUBLISHED.txt", "parents": [folder_id]},
        media_body=media,
        fields="id",
    ).execute()


def generate_voiceover(script_text: str, out_mp3: Path):
    url = f"https://api.elevenlabs.io/v1/text-to-speech/{ELEVENLABS_VOICE_ID}"
    headers = {
        "xi-api-key": ELEVENLABS_API_KEY,
        "Content-Type": "application/json",
        "Accept": "audio/mpeg",
    }
    payload = {
        "text": script_text.strip(),
        "model_id": ELEVENLABS_MODEL_ID,
        "voice_settings": {"stability": 0.45, "similarity_boost": 0.78},
    }
    resp = requests.post(url, headers=headers, json=payload, timeout=120)
    resp.raise_for_status()
    out_mp3.write_bytes(resp.content)


def mix_audio_and_video(video_in: Path, voice_mp3: Path, video_out: Path):
    """Mezcla la voz normalizada (-14 LUFS) con el audio SFX original del MP4 (ducked al 30%)."""
    cmd = [
        "ffmpeg", "-y",
        "-i", str(video_in),
        "-i", str(voice_mp3),
        "-filter_complex",
        "[1:a]aresample=48000,aformat=channel_layouts=stereo,loudnorm=I=-14:TP=-1.5:LRA=11[v];"
        "[0:a]aresample=48000,volume=0.30[s];"
        "[v][s]amix=inputs=2:duration=longest:dropout_transition=0:normalize=0,alimiter=limit=0.95[aout]",
        "-map", "0:v:0",
        "-map", "[aout]",
        "-c:v", "copy",
        "-c:a", "aac",
        "-b:a", "192k",
        "-ar", "48000",
        "-movflags", "+faststart",
        "-shortest",
        str(video_out),
    ]
    subprocess.run(cmd, check=True)


def upload_to_r2(local_path: Path, object_key: str, content_type: str) -> str:
    s3 = boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT_URL,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        region_name="auto",
    )
    s3.upload_file(
        str(local_path),
        R2_BUCKET_NAME,
        object_key,
        ExtraArgs={"ContentType": content_type},
    )
    return f"{R2_PUBLIC_BASE_URL}/{object_key}"


def publish_reel_to_instagram(video_url: str, cover_url: str, caption: str) -> str:
    base = f"https://graph.facebook.com/{GRAPH_API_VERSION}"

    # 1. Crear contenedor de Reel
    create_resp = requests.post(
        f"{base}/{IG_USER_ID}/media",
        data={
            "media_type": "REELS",
            "video_url": video_url,
            "cover_url": cover_url,
            "caption": caption.strip(),
            "share_to_feed": "true",
            "access_token": IG_ACCESS_TOKEN,
        },
        timeout=60,
    )
    create_resp.raise_for_status()
    creation_id = create_resp.json()["id"]
    print(f"Contenedor IG creado: {creation_id}. Esperando procesamiento...")

    # 2. Esperar a que Meta termine de procesar el video
    for attempt in range(30):
        time.sleep(10)
        st_resp = requests.get(
            f"{base}/{creation_id}",
            params={"fields": "status_code,status", "access_token": IG_ACCESS_TOKEN},
            timeout=30,
        )
        st_resp.raise_for_status()
        status_code = st_resp.json().get("status_code")
        print(f"  Intento {attempt + 1}: status_code={status_code}")
        if status_code == "FINISHED":
            break
        if status_code == "ERROR":
            raise RuntimeError(f"Error procesando Reel en Instagram: {st_resp.json()}")
    else:
        raise TimeoutError("Tiempo de espera agotado esperando procesamiento de Instagram.")

    # 3. Publicar el contenedor
    pub_resp = requests.post(
        f"{base}/{IG_USER_ID}/media_publish",
        data={"creation_id": creation_id, "access_token": IG_ACCESS_TOKEN},
        timeout=60,
    )
    pub_resp.raise_for_status()
    media_id = pub_resp.json()["id"]
    print(f"Reel publicado en Instagram! media_id={media_id}")
    return media_id


def main():
    drive = get_drive_service()
    items = list_children(drive, DRIVE_FOLDER_ID)
    subfolders = [f for f in items if f["mimeType"] == "application/vnd.google-apps.folder"]

    for folder in subfolders:
        children = {c["name"]: c for c in list_children(drive, folder["id"])}
        if "PUBLISHED.txt" in children:
            print(f"[SKIP] Ya publicado: {folder['name']}")
            continue

        required = {"video.mp4", "portada.png", "guion.txt", "caption.txt"}
        missing = required - set(children.keys())
        if missing:
            print(f"[SKIP] Carpeta incompleta ({folder['name']}), faltan: {missing}")
            continue

        print(f"[PROCESANDO] {folder['name']} ...")
        with tempfile.TemporaryDirectory() as tmp:
            tmp_dir = Path(tmp)
            vid_raw  = tmp_dir / "video.mp4"
            cover    = tmp_dir / "portada.png"
            guion_f  = tmp_dir / "guion.txt"
            cap_f    = tmp_dir / "caption.txt"
            voice_f  = tmp_dir / "voice.mp3"
            vid_out  = tmp_dir / "reel_final.mp4"

            download_file(drive, children["video.mp4"]["id"], vid_raw)
            download_file(drive, children["portada.png"]["id"], cover)
            download_file(drive, children["guion.txt"]["id"], guion_f)
            download_file(drive, children["caption.txt"]["id"], cap_f)

            script_text = guion_f.read_text(encoding="utf-8")
            caption_text = cap_f.read_text(encoding="utf-8")

            print("  -> Generando voz en ElevenLabs...")
            generate_voiceover(script_text, voice_f)

            print("  -> Mezclando audio y video con ffmpeg...")
            mix_audio_and_video(vid_raw, voice_f, vid_out)

            if DRY_RUN:
                print(f"  -> DRY_RUN activo. Mezcla exitosa en {vid_out} ({vid_out.stat().st_size} bytes).")
                break

            slug = folder["id"]
            print("  -> Subiendo archivos finales a Cloudflare R2...")
            video_url = upload_to_r2(vid_out, f"reels/{slug}/reel_final.mp4", "video/mp4")
            cover_url = upload_to_r2(cover, f"reels/{slug}/portada.png", "image/png")

            print("  -> Publicando en Instagram...")
            media_id = publish_reel_to_instagram(video_url, cover_url, caption_text)

            mark_published(drive, folder["id"], media_id)
            print(f"[OK] Publicación completada para {folder['name']}")
            # Publica solo 1 reel pendiente por ejecución (lunes, miércoles y viernes)
            break
    else:
        print("No hay carpetas pendientes para publicar hoy.")


if __name__ == "__main__":
    main()
