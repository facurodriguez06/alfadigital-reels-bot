# Alfa Digital — Auto-Publicador de Reels (Python + GitHub Actions)

Este kit conecta tu carpeta de Google Drive (**Alfa Digital — Reels Automáticos**) con **ElevenLabs**, **FFmpeg**, **Cloudflare R2** y la **Instagram Graph API** para publicar automáticamente un Reel cada **lunes, miércoles y viernes**.

## Cómo funciona el flujo completo
1. **Rutina de generación (Gemini):** Lunes, miércoles y viernes crea una subcarpeta nueva en tu Google Drive con `video.mp4`, `portada.png`, `guion.txt` y `caption.txt`.
2. **Puente automático (este repo en GitHub Actions):** Corre 1 hora después, genera la voz en ElevenLabs, une el audio al video con `ffmpeg`, sube el resultado a Cloudflare R2, publica el Reel en `@alfadigital.ar` y marca la carpeta con `PUBLISHED.txt`.

## Configuración en 4 pasos
1. Creá un repositorio privado en GitHub y subí estos archivos.
2. En Google Cloud, creá una **Service Account**, descargá su JSON y compartí la carpeta de Drive (`Alfa Digital — Reels Automáticos`) con el email de esa Service Account (permiso de **Editor**).
3. En Cloudflare R2, creá un bucket público (`Public Development URL` habilitada) y generá un API Token con permiso de escritura.
4. En GitHub (`Settings -> Secrets and variables -> Actions`), cargá estos secrets:
   - `DRIVE_FOLDER_ID`: `1cuXCocXpwJYus8Pa1Q6l8mOdE1IbBEwG`
   - `GOOGLE_SERVICE_ACCOUNT_JSON`: contenido completo del JSON de la Service Account
   - `ELEVENLABS_API_KEY`: tu clave de ElevenLabs
   - `ELEVENLABS_VOICE_ID`: el ID de la voz que usás en ElevenLabs
   - `R2_ENDPOINT_URL`: `https://<tu_account_id>.r2.cloudflarestorage.com`
   - `R2_ACCESS_KEY_ID` y `R2_SECRET_ACCESS_KEY`: credenciales de R2
   - `R2_BUCKET_NAME`: nombre de tu bucket
   - `R2_PUBLIC_BASE_URL`: URL pública de tu bucket R2 (`https://pub-xxxx.r2.dev`)
   - `IG_USER_ID`: ID numérico de tu cuenta profesional de Instagram
   - `IG_ACCESS_TOKEN`: token de acceso de larga duración con permisos `instagram_basic` e `instagram_content_publish`

## Prueba segura antes de activar
En la pestaña **Actions** de GitHub, ejecutá **Publicar Reel de Alfa Digital** manualmente con `dry_run = true` para validar Drive + ElevenLabs + FFmpeg sin publicar todavía en Instagram.
