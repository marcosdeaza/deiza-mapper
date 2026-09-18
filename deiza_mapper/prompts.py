"""
System-prompt fragments that teach a language model the artifact protocol and
the design vocabulary this renderer understands. Drop `protocol('es')` or
`protocol('en')` into your system prompt; add `deck_instructions(...)` when you
ask for a slide deck as HTML.
"""
from .themes import THEME_NAMES

_THEMES = ', '.join(THEME_NAMES)

_ES = """**Entregables (bloques artifact):** cuando el usuario pida un archivo, escribe un mensaje breve y despues UN bloque:
```artifact
{"name": "nombre.ext", "type": "pdf|docx|pptx|html|zip|code", "content": "..."}
```
- PDF y DOCX: `content` es markdown. La primera linea es un comentario de tema: `<!-- theme: X -->` con X en {%(themes)s}. Opciones: `accent: #RRGGBB`, `cover: true` (portada a pagina completa), `numbers: true` (numeros de pagina), `size: letter`, `orientation: landscape`.
  Elige el tema por el tono: editorial (historia, cultura, instituciones), swiss (negocio, consultoria), ocean (ciencia, salud, tecnica), forest (naturaleza, sostenibilidad), sunset (marketing, eventos), noir/midnight (tecnologia, startups, lujo), minimal (apuntes, legal), paper (calido, personal), brutal (manifiesto, cartel).
  Estructura: `# Titulo` en la primera linea y una frase de subtitulo debajo; secciones `##` y `###`; tablas markdown para datos; citas `>`; listas; **negritas**. Vocabulario extra que se renderiza de verdad:
  - `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]` al inicio de una cita -> caja destacada con titulo.
  - Formulas LaTeX: `$inline$` y `$$display$$` (matrices con `\\begin{pmatrix}`, integrales, sumatorios, alineaciones). Se renderizan tal cual, tambien en Word.
  - Graficos: un bloque ```chart con un JSON de Chart.js `{"type": "bar|line|pie|doughnut|radar", "data": {"labels": [...], "datasets": [{"label": "...", "data": [...]}]}}` -> grafico real con la paleta del tema.
  - Diagramas: un bloque ```mermaid (flowchart, sequenceDiagram, gantt, pie...).
  - `[[PAGEBREAK]]` en su propia linea para saltar de pagina; `::: columns` ... `:::` para texto a dos columnas; `::: box` ... `:::` para un panel enmarcado.
  - Fotos: `![pie de foto](URL)` con URLs reales (adjuntas o verificadas), nunca inventadas; dos o mas imagenes seguidas forman una galeria.
  - Diseño totalmente a medida (poster, curriculum, menu, factura, invitacion, revista): en vez de markdown escribe en `content` un documento HTML completo (`<!doctype html>` con `<style>`, Google Fonts, `@page { size: A4; margin: ... }`, colores, columnas, tarjetas, `<img>`). Se imprime tal cual con un motor de navegador.
- PPTX: `content` es HTML de diapositivas: una `<section class="slide">` por diapositiva (1280x720). Ver las instrucciones de decks.
- HTML: `content` es una pagina completa y responsive. ZIP: `content` es una lista JSON de ficheros `[{"name": "index.html", "content": "..."}, {"name": "style.css", ...}, {"name": "game.js", ...}]` con TODO el codigo real; index.html enlaza los demas con rutas relativas.
- Nunca firmes ni menciones la herramienta dentro del entregable. Sin emojis."""

_EN = """**Deliverables (artifact blocks):** when the user asks for a file, write a short message and then ONE block:
```artifact
{"name": "file.ext", "type": "pdf|docx|pptx|html|zip|code", "content": "..."}
```
- PDF and DOCX: `content` is markdown. Its first line is a theme comment: `<!-- theme: X -->` with X in {%(themes)s}. Options: `accent: #RRGGBB`, `cover: true` (full-page cover), `numbers: true` (page numbers), `size: letter`, `orientation: landscape`.
  Pick the theme by tone: editorial (history, culture, institutions), swiss (business, consulting), ocean (science, health, technical), forest (nature, sustainability), sunset (marketing, events), noir/midnight (tech, startups, luxury), minimal (notes, legal), paper (warm, personal), brutal (manifesto, poster).
  Structure: `# Title` on the first line and a one-sentence subtitle below; `##`/`###` sections; markdown tables for data; `>` quotes; lists; **bold**. Extra vocabulary that really renders:
  - `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]` at the start of a quote -> a titled callout box.
  - LaTeX: `$inline$` and `$$display$$` (matrices with `\\begin{pmatrix}`, integrals, sums, aligned). Rendered as real equations, also in Word.
  - Charts: a ```chart block holding a Chart.js JSON `{"type": "bar|line|pie|doughnut|radar", "data": {"labels": [...], "datasets": [{"label": "...", "data": [...]}]}}` -> a real chart in the theme palette.
  - Diagrams: a ```mermaid block (flowchart, sequenceDiagram, gantt, pie...).
  - `[[PAGEBREAK]]` on its own line; `::: columns` ... `:::` for two-column text; `::: box` ... `:::` for a framed panel.
  - Photos: `![caption](URL)` with real URLs only (attached or verified), never invented; two or more images in a row form a gallery.
  - Fully custom design (poster, resume, menu, invoice, invitation, magazine): instead of markdown, put a complete HTML document in `content` (`<!doctype html>` with `<style>`, Google Fonts, `@page { size: A4; margin: ... }`, colours, columns, cards, `<img>`). It prints as-is through a real browser engine.
- PPTX: `content` is slide HTML: one `<section class="slide">` per slide (1280x720). See the deck instructions.
- HTML: `content` is a complete responsive page. ZIP: `content` is a JSON file list `[{"name": "index.html", "content": "..."}, {"name": "style.css", ...}, {"name": "game.js", ...}]` with ALL the real code; index.html links the others with relative paths.
- Never sign or mention the tool inside a deliverable. No emojis."""

_DECK_ES = """**Presentacion en HTML (se convierte en PowerPoint editable):** devuelve SOLO el HTML de las diapositivas, sin explicaciones ni markdown:
- Una `<section class="slide">` por diapositiva, tamaño fijo 1280x720 px. Entre %(min)d y %(max)d diapositivas: portada, 1-2 separadores de seccion, contenido variado y cierre.
- Puedes incluir UN `<style>` al principio con tus reglas. Ya existe una base con variables `--bg --ink --muted --accent --accent2 --surface --line --on-accent --font-head --font-body` y clases utiles: `.pad` (area segura), `.kicker`, `.rule`, `.sub`, `.grid-2 .grid-3 .grid-4`, `.card`, `.stat-num .stat-label`, `.split-img` (foto a la derecha 44%%), `.full-img` + `.overlay` (foto a sangre), `.pill`, `.timeline .step`, `table`, `ul.b` (viñetas con guion), `.foot`, `.bg-accent`, `.bg-surface`.
- Diseño: una direccion de arte coherente, jerarquia tipografica fuerte (titulos 44-72 px, texto 20-28 px), mucho aire, maximo 60 palabras por diapositiva, nada de parrafos largos. Alterna layouts: titulo + tres tarjetas, cifra enorme, foto partida, tabla, cita, linea temporal, dos columnas.
- Fotos SOLO desde estas URLs (si no hay, usa formas y color): %(images)s
- Prohibido: `::before/::after` decorativos, iconos emoji, texto que se salga del cuadro, gradientes radiales, imagenes inventadas.
- Notas del ponente: atributo `data-notes="..."` en la section.
- Tema: %(theme)s."""

_DECK_EN = """**HTML presentation (converted into an editable PowerPoint):** return ONLY the slide HTML, no explanations, no markdown:
- One `<section class="slide">` per slide, fixed 1280x720 px. Between %(min)d and %(max)d slides: cover, 1-2 section dividers, varied content and a closing slide.
- You may add ONE `<style>` at the top. A base already provides the variables `--bg --ink --muted --accent --accent2 --surface --line --on-accent --font-head --font-body` and helpers: `.pad` (safe area), `.kicker`, `.rule`, `.sub`, `.grid-2 .grid-3 .grid-4`, `.card`, `.stat-num .stat-label`, `.split-img` (photo on the right, 44%%), `.full-img` + `.overlay` (full-bleed photo), `.pill`, `.timeline .step`, `table`, `ul.b` (dash bullets), `.foot`, `.bg-accent`, `.bg-surface`.
- Design: one coherent art direction, strong type hierarchy (titles 44-72 px, body 20-28 px), generous whitespace, at most 60 words per slide, no long paragraphs. Alternate layouts: title + three cards, giant number, split photo, table, quote, timeline, two columns.
- Photos ONLY from these URLs (none available -> use shapes and colour): %(images)s
- Forbidden: decorative `::before/::after`, emoji icons, text overflowing its box, radial gradients, invented image URLs.
- Speaker notes: a `data-notes="..."` attribute on the section.
- Theme: %(theme)s."""


def protocol(language: str = 'es') -> str:
    tpl = _ES if (language or 'es').startswith('es') else _EN
    return tpl % {'themes': _THEMES}


def deck_instructions(language: str = 'es', theme: str = 'editorial', image_urls: list = None,
                      min_slides: int = 8, max_slides: int = 12) -> str:
    tpl = _DECK_ES if (language or 'es').startswith('es') else _DECK_EN
    imgs = '\n' + '\n'.join(f'  - {u}' for u in (image_urls or [])) if image_urls else '(ninguna)' if language.startswith('es') else '(none)'
    return tpl % {'min': min_slides, 'max': max_slides, 'images': imgs, 'theme': theme}
