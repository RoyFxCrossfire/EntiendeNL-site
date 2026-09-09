# EntiendeNL — sitio web

## Estructura
- `index.html`, `noticias.html`, `guias.html`, `precios.html`, `sobre.html`, `contacto.html`, `privacidad.html`
- `assets/style.css`, `assets/main.js` — estilos y lógica compartidos
- `assets/news_data.json` — datos de noticias que consume `noticias.html` e `index.html`
- `news_automation/fetch_news.py` — script que genera `news_data.json`

## Publicar el sitio (hosting)
Es un sitio estático puro (HTML/CSS/JS), así que sirve cualquier hosting simple:
1. Compra un dominio (ej. entiendenl.com/.nl) — Vimexx, TransIP, Namecheap.
2. Hosting: Netlify, Vercel o GitHub Pages (gratis) son las opciones más simples para un sitio estático — arrastra la carpeta y listo. Cloudflare Pages es otra opción.
3. Apunta el dominio al hosting elegido (registros DNS que te da el propio proveedor).

## Automatizar las noticias
`news_automation/fetch_news.py`:
1. Descarga entradas de las fuentes RSS definidas en `RSS_FEEDS` (actualmente
   NOS.nl para Países Bajos/regulación/trabajo, y la sala de prensa de la
   Comisión Europea para Europa — IND.nl y Rijksoverheid.nl no publican un
   feed RSS general utilizable, se verificó el 2026-09-09).
2. Como son feeds de noticias generales (no solo migración), cada artículo
   pasa primero por un filtro de relevancia con la API de Anthropic: si no
   toca temas de migración/trabajo/regulación relevantes para el público del
   sitio, se descarta. Si es relevante, se genera un resumen breve en español
   (nunca copia el artículo completo).
3. Guarda solo artículos nuevos y relevantes (evita duplicados por URL) en
   `assets/news_data.json`, con enlace a la fuente original.

Para ejecutarlo:
```
pip install feedparser anthropic --break-system-packages
export ANTHROPIC_API_KEY=sk-...
python fetch_news.py
```

Modo de prueba sin red ni API (usa datos de ejemplo):
```
python fetch_news.py --dry-run
```

### Automatización recurrente
`.github/workflows/fetch-news.yml` corre este script automáticamente todos
los días a las 06:00 UTC vía GitHub Actions, y si hay artículos nuevos hace
commit y push de `assets/news_data.json` — Vercel vuelve a desplegar el
sitio automáticamente al detectar el push. También se puede lanzar a mano
desde la pestaña "Actions" del repo ("Run workflow").

**Para activarlo hace falta un secret en el repo:**
Settings → Secrets and variables → Actions → New repository secret:
- `ANTHROPIC_API_KEY` (obligatorio)
- `TELEGRAM_TOKEN` (opcional, solo si quieres que también publique en el
  canal de Telegram)

**Nota:** si más adelante encuentras URLs de RSS reales de IND.nl o
Rijksoverheid.nl (por ejemplo un feed específico de un tema en
rijksoverheid.nl con botón "Abonneren"), puedes añadirlas a `RSS_FEEDS` en
`news_automation/fetch_news.py` — el filtro de relevancia seguirá
funcionando igual.

## Antes de enviar a AdSense
- Sustituye todos los textos de ejemplo (privacidad, contacto) por los
  datos reales del negocio.
- Publica varios artículos de guía y noticias reales antes de solicitar
  la revisión — un sitio con contenido mínimo suele ser rechazado.
- El formulario de contacto usa `mailto:` como solución simple; si
  quieres un formulario real (sin abrir el cliente de correo), habría
  que conectarlo a un servicio como Formspree o un backend propio.
