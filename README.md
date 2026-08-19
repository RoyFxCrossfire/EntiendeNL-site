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
1. Descarga entradas de las fuentes RSS definidas en `RSS_FEEDS`.
2. Genera un resumen breve en español con la API de Anthropic (nunca copia el artículo completo).
3. Guarda solo artículos nuevos (evita duplicados por URL) en `assets/news_data.json`, con enlace a la fuente original.

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
Igual que el bot de EntiendeNL, este script puede correr en Render como un
"Cron Job" o "Scheduled Job" (por ejemplo, una vez al día), con la variable
de entorno `ANTHROPIC_API_KEY` configurada. Tras cada ejecución, el
`news_data.json` actualizado debe volver a subirse/desplegarse junto al
resto del sitio (o servirse desde un pequeño endpoint si prefieres separar
datos de contenido estático).

**Nota:** los feeds de `RSS_FEEDS` son ejemplos — revisa las URLs reales de
RSS de IND.nl, Rijksoverheid.nl, NU.nl, etc. antes de usarlo en producción,
y añade o quita fuentes según lo que quieras cubrir.

## Antes de enviar a AdSense
- Sustituye todos los textos de ejemplo (privacidad, contacto) por los
  datos reales del negocio.
- Publica varios artículos de guía y noticias reales antes de solicitar
  la revisión — un sitio con contenido mínimo suele ser rechazado.
- El formulario de contacto usa `mailto:` como solución simple; si
  quieres un formulario real (sin abrir el cliente de correo), habría
  que conectarlo a un servicio como Formspree o un backend propio.
