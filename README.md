# Cristian Clips API

Servicio de recorte de videos de YouTube en cortes verticales u horizontales
con subtítulos karaoke, avatar de reacción y porcentaje de potencial viral.

Desplegado en Render (plan gratis). El servicio se duerme tras 15 minutos
sin uso y tarda ~1 minuto en despertar; la página sondea el avance, así
que solo hay que esperar.

## Endpoints

- `POST /api/jobs` `{youtube_url, format, num_clips, min_seconds, max_seconds, subtitles, subtitle_style, avatar}` → `{job_id, status}`
- `GET /api/jobs/{id}` → estado, avance y cortes
- `GET /api/jobs/{id}/clips/{filename}` → descarga del mp4
- `GET /` → salud del servicio

## Optimización para el plan gratis de Render

Render gratis tiene 0.1 CPU y 512 MB de RAM. Cuando el video trae el
heatmap de YouTube con suficientes picos, los momentos se eligen directo
de ahí y NO se transcribe todo el audio; cada corte transcribe solo sus
propios segundos para los subtítulos y el título. Sin heatmap, el
servicio transcribe el audio completo como antes (más lento).

## Desarrollo local

```bash
python3 server.py   # escucha en 0.0.0.0:8765 (o $PORT)
```
