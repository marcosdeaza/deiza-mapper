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
- PDF y DOCX: `content` es markdown. La primera linea es un comentario de tema: `<!-- theme: X -->` con X en {%(themes)s}. Opciones: `accent: #RRGGBB`, `cover: true` (portada a pagina completa con campo de color), `numbers: true` (numeros de pagina), `size: letter`, `orientation: landscape`, `kicker: "Informe anual"`, `author: "Quien firma"`, `date: "Septiembre 2026"` (los tres salen en la portada). Una imagen `![](URL)` justo debajo del subtitulo se convierte en la foto de portada.
  Elige el tema por el tono: editorial (historia, cultura, instituciones), swiss (negocio, consultoria), ocean (ciencia, salud, tecnica), forest (naturaleza, sostenibilidad), sunset (marketing, eventos), noir/midnight (tecnologia, startups, lujo), minimal (apuntes, legal), paper (calido, personal), brutal (manifiesto, cartel).
  Estructura: `# Titulo` en la primera linea y una frase de subtitulo debajo; secciones `##` y `###`; tablas markdown para datos; citas `>`; listas; **negritas**. Vocabulario extra que se renderiza de verdad:
  - `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]` al inicio de una cita -> caja destacada con titulo.
  - Formulas LaTeX: `$inline$` y `$$display$$` (matrices con `\\begin{pmatrix}`, integrales, sumatorios, alineaciones). Se renderizan tal cual, tambien en Word.
  - Graficos: un bloque ```chart con un JSON de Chart.js `{"type": "bar|line|pie|doughnut|radar", "data": {"labels": [...], "datasets": [{"label": "...", "data": [...]}]}}` -> grafico real con la paleta del tema.
  - Diagramas: un bloque ```mermaid (flowchart, sequenceDiagram, gantt, pie...).
  - `[[PAGEBREAK]]` en su propia linea para saltar de pagina: solo antes de una parte principal, nunca antes de cada seccion (deja paginas medio vacias); `::: columns` ... `:::` para texto a dos columnas; `::: box` ... `:::` para un panel enmarcado.
  - Fotos: `![pie de foto](URL)` con URLs reales (adjuntas o verificadas), nunca inventadas; dos o mas imagenes seguidas forman una galeria.
  - Diseño totalmente a medida (poster, curriculum, menu, factura, invitacion, revista): en vez de markdown escribe en `content` un documento HTML completo (`<!doctype html>` con `<style>`, Google Fonts, `@page { size: A4; margin: ... }`, colores, columnas, tarjetas, `<img>`). Se imprime tal cual con un motor de navegador.
- PPTX: `content` es HTML de diapositivas: una `<section class="slide">` por diapositiva (1280x720). Ver las instrucciones de decks.
- HTML: `content` es una pagina completa y responsive. ZIP: `content` es una lista JSON de ficheros `[{"name": "index.html", "content": "..."}, {"name": "style.css", ...}, {"name": "game.js", ...}]` con TODO el codigo real; index.html enlaza los demas con rutas relativas.
- Nunca firmes ni menciones la herramienta dentro del entregable. Sin emojis."""

_EN = """**Deliverables (artifact blocks):** when the user asks for a file, write a short message and then ONE block:
```artifact
{"name": "file.ext", "type": "pdf|docx|pptx|html|zip|code", "content": "..."}
```
- PDF and DOCX: `content` is markdown. Its first line is a theme comment: `<!-- theme: X -->` with X in {%(themes)s}. Options: `accent: #RRGGBB`, `cover: true` (full-page cover with a colour field), `numbers: true` (page numbers), `size: letter`, `orientation: landscape`, `kicker: "Annual report"`, `author: "Who signs"`, `date: "September 2026"` (all three print on the cover). An image `![](URL)` right under the subtitle becomes the cover picture.
  Pick the theme by tone: editorial (history, culture, institutions), swiss (business, consulting), ocean (science, health, technical), forest (nature, sustainability), sunset (marketing, events), noir/midnight (tech, startups, luxury), minimal (notes, legal), paper (warm, personal), brutal (manifesto, poster).
  Structure: `# Title` on the first line and a one-sentence subtitle below; `##`/`###` sections; markdown tables for data; `>` quotes; lists; **bold**. Extra vocabulary that really renders:
  - `> [!NOTE]`, `> [!TIP]`, `> [!WARNING]`, `> [!IMPORTANT]` at the start of a quote -> a titled callout box.
  - LaTeX: `$inline$` and `$$display$$` (matrices with `\\begin{pmatrix}`, integrals, sums, aligned). Rendered as real equations, also in Word.
  - Charts: a ```chart block holding a Chart.js JSON `{"type": "bar|line|pie|doughnut|radar", "data": {"labels": [...], "datasets": [{"label": "...", "data": [...]}]}}` -> a real chart in the theme palette.
  - Diagrams: a ```mermaid block (flowchart, sequenceDiagram, gantt, pie...).
  - `[[PAGEBREAK]]` on its own line: only before a major part, never before every section (it leaves half-empty pages); `::: columns` ... `:::` for two-column text; `::: box` ... `:::` for a framed panel.
  - Photos: `![caption](URL)` with real URLs only (attached or verified), never invented; two or more images in a row form a gallery.
  - Fully custom design (poster, resume, menu, invoice, invitation, magazine): instead of markdown, put a complete HTML document in `content` (`<!doctype html>` with `<style>`, Google Fonts, `@page { size: A4; margin: ... }`, colours, columns, cards, `<img>`). It prints as-is through a real browser engine.
- PPTX: `content` is slide HTML: one `<section class="slide">` per slide (1280x720). See the deck instructions.
- HTML: `content` is a complete responsive page. ZIP: `content` is a JSON file list `[{"name": "index.html", "content": "..."}, {"name": "style.css", ...}, {"name": "game.js", ...}]` with ALL the real code; index.html links the others with relative paths.
- Never sign or mention the tool inside a deliverable. No emojis."""

_DECK_ES = """**Presentacion en HTML (se convierte en PowerPoint editable):** devuelve SOLO el HTML de las diapositivas, sin explicaciones ni markdown.
REGLAS DURAS (el sistema borra todo lo que no cumpla):
- Primera linea obligatoria: `<!-- theme: X -->` con X en {editorial, noir, swiss, ocean, forest, minimal, sunset, midnight, paper, brutal}. Elige por el tono: editorial (historia, cultura), swiss (negocio, consultoria), ocean (ciencia, salud, tecnica), forest (naturaleza), sunset (marketing, eventos), noir/midnight (tecnologia, startups, lujo), minimal (academico, legal), paper (personal, calido), brutal (manifiesto, creativo).
- NADA de `<style>`, ni atributos style, ni clases fuera de las listadas, ni emojis, ni iconos. La geometria, colores y tipografias los pone el sistema de diseño.
- Cada diapositiva: `<section class="slide RECETA" data-notes="notas del ponente">` y dentro UN `<div class="pad">` con el contenido. Las fotos van como hijo directo de la section, ANTES del pad: `<img class="split-img" src="URL">` (foto a la derecha; `split-img left` para la izquierda) o `<img class="full-img" src="URL">` (foto a sangre con texto encima).
- Entre %(min)d y %(max)d diapositivas. Secuencia: cover, luego bloques de [section + 2-4 diapositivas de contenido], y closing al final. Nunca dos recetas iguales seguidas; usa al menos 5 recetas distintas.
RECETAS (usa exactamente esta estructura):
- cover: `<div class="pad"><p class="kicker">contexto</p><h1>Titulo (max 8 palabras)</h1><p class="sub">subtitulo de una frase</p><p class="meta">fecha · autor</p></div>` (+ opcional split-img o full-img).
- section: `<div class="pad"><h2>Nombre del bloque</h2><p class="sub">una frase</p></div>` (el sistema añade el numero).
- text: `<div class="pad"><p class="kicker">tema</p><h2>Titulo</h2><ul><li><b>Idea clave:</b> desarrollo breve</li>...</ul></div>` — 3 a 5 li, max 14 palabras cada uno. Tambien vale `<p>` en vez de lista (max 2 parrafos de 35 palabras).
- split: como text pero con `<img class="split-img">`; max 4 li o 2 parrafos cortos. `<div class="foot">Pie de foto o fuente</div>` opcional fuera del pad.
- full: `<img class="full-img">` + `<div class="pad"><p class="kicker">tema</p><h2>Titulo</h2><p class="sub">una frase</p></div>` — solo texto breve.
- cards: `<div class="pad"><h2>Titulo</h2><div class="grid-3"><div class="card"><div class="idx">01</div><h4>Nombre (max 4 palabras)</h4><p>max 22 palabras</p></div>...</div></div>` — 3 cards (grid-2 con 2, grid-4 con 4). Una card puede llevar `<div class="stat-num">45 %%</div><div class="stat-label">texto</div>` en vez de h4/p.
- stat: `<div class="pad"><p class="kicker">tema</p><div class="stat-num">72 %%</div><p class="stat-label">que significa la cifra (max 18 palabras)</p><p>una frase de contexto</p></div>` O una fila de cifras: `<div class="pad"><h2>Titulo</h2><div class="stats"><div class="stat"><div class="stat-num">56 %%</div><div class="stat-label">etiqueta</div></div>x3</div></div>` (stat-num max 7 caracteres).
- two: `<div class="pad"><h2>Titulo</h2><div class="cols vs"><div class="col"><h4>Opcion A</h4><ul><li>...</li></ul></div><div class="col"><h4>Opcion B</h4><ul><li>...</li></ul></div></div></div>` — 3-4 li por columna.
- table: `<div class="pad"><h2>Titulo</h2><table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table></div>` — max 5 columnas y 7 filas, celdas cortas.
- timeline: `<div class="pad"><h2>Titulo</h2><div class="timeline"><div class="step"><div class="when">2024</div><b>Hito</b><p>max 14 palabras</p></div>x3-5</div></div>`.
- quote: `<div class="pad"><p class="q">Cita literal o idea fuerza (max 35 palabras)</p><p class="author">— Quien</p></div>`.
- closing: `<div class="pad"><h1>Cierre (max 5 palabras)</h1><p class="sub">siguiente paso o mensaje final</p><p class="contact">contacto o web</p></div>`.
FOTOS: usa EXCLUSIVAMENTE estas URLs, cada una como maximo una vez, solo en cover/split/full: %(images)s
Si no hay fotos, no inventes URLs: apoya el ritmo visual en stat, cards, table, timeline y quote.
CONTENIDO: hechos concretos, cifras, nombres y fechas reales del contexto; nada de relleno ("Introduccion", "Conclusion" como unico texto). Todo el texto en el idioma del usuario. Notas del ponente utiles en cada section (data-notes).
Tema sugerido: %(theme)s."""

_DECK_EN = """**HTML presentation (converted into an editable PowerPoint):** return ONLY the slide HTML, no explanations, no markdown.
HARD RULES (the system strips anything else):
- Mandatory first line: `<!-- theme: X -->` with X in {editorial, noir, swiss, ocean, forest, minimal, sunset, midnight, paper, brutal}. Pick by tone: editorial (history, culture), swiss (business, consulting), ocean (science, health, technical), forest (nature), sunset (marketing, events), noir/midnight (tech, startups, luxury), minimal (academic, legal), paper (personal, warm), brutal (manifesto, creative).
- NO `<style>`, no style attributes, no classes outside the list, no emojis, no icons. Geometry, colours and type come from the design system.
- Each slide: `<section class="slide RECIPE" data-notes="speaker notes">` holding ONE `<div class="pad">` with the content. Pictures are direct children of the section, BEFORE the pad: `<img class="split-img" src="URL">` (photo on the right; `split-img left` for the left) or `<img class="full-img" src="URL">` (full-bleed photo with text on top).
- Between %(min)d and %(max)d slides. Sequence: cover, then blocks of [section + 2-4 content slides], closing at the end. Never the same recipe twice in a row; use at least 5 different recipes.
RECIPES (use exactly this structure):
- cover: `<div class="pad"><p class="kicker">context</p><h1>Title (max 8 words)</h1><p class="sub">one-sentence subtitle</p><p class="meta">date · author</p></div>` (+ optional split-img or full-img).
- section: `<div class="pad"><h2>Block name</h2><p class="sub">one sentence</p></div>` (the system adds the number).
- text: `<div class="pad"><p class="kicker">topic</p><h2>Title</h2><ul><li><b>Key idea:</b> short development</li>...</ul></div>` — 3 to 5 li, max 14 words each. `<p>` instead of a list is fine (max 2 paragraphs of 35 words).
- split: like text plus `<img class="split-img">`; max 4 li or 2 short paragraphs. Optional `<div class="foot">Caption or source</div>` outside the pad.
- full: `<img class="full-img">` + `<div class="pad"><p class="kicker">topic</p><h2>Title</h2><p class="sub">one sentence</p></div>` — short text only.
- cards: `<div class="pad"><h2>Title</h2><div class="grid-3"><div class="card"><div class="idx">01</div><h4>Name (max 4 words)</h4><p>max 22 words</p></div>...</div></div>` — 3 cards (grid-2 for 2, grid-4 for 4). A card may hold `<div class="stat-num">45%%</div><div class="stat-label">text</div>` instead of h4/p.
- stat: `<div class="pad"><p class="kicker">topic</p><div class="stat-num">72%%</div><p class="stat-label">what the number means (max 18 words)</p><p>one sentence of context</p></div>` OR a row of numbers: `<div class="pad"><h2>Title</h2><div class="stats"><div class="stat"><div class="stat-num">56%%</div><div class="stat-label">label</div></div>x3</div></div>` (stat-num max 7 characters).
- two: `<div class="pad"><h2>Title</h2><div class="cols vs"><div class="col"><h4>Option A</h4><ul><li>...</li></ul></div><div class="col"><h4>Option B</h4><ul><li>...</li></ul></div></div></div>` — 3-4 li per column.
- table: `<div class="pad"><h2>Title</h2><table><thead><tr><th>...</th></tr></thead><tbody><tr><td>...</td></tr></tbody></table></div>` — max 5 columns and 7 rows, short cells.
- timeline: `<div class="pad"><h2>Title</h2><div class="timeline"><div class="step"><div class="when">2024</div><b>Milestone</b><p>max 14 words</p></div>x3-5</div></div>`.
- quote: `<div class="pad"><p class="q">Verbatim quote or key idea (max 35 words)</p><p class="author">— Who</p></div>`.
- closing: `<div class="pad"><h1>Closing (max 5 words)</h1><p class="sub">next step or final message</p><p class="contact">contact or website</p></div>`.
PHOTOS: use ONLY these URLs, each at most once, only in cover/split/full: %(images)s
No photos available -> do not invent URLs: build the visual rhythm with stat, cards, table, timeline and quote.
CONTENT: concrete facts, figures, names and dates from the context; no filler ("Introduction", "Conclusion" as the only text). All text in the user's language. Useful speaker notes on every section (data-notes).
Suggested theme: %(theme)s."""


def protocol(language: str = 'es') -> str:
    tpl = _ES if (language or 'es').startswith('es') else _EN
    return tpl % {'themes': _THEMES}


def deck_instructions(language: str = 'es', theme: str = 'editorial', image_urls: list = None,
                      min_slides: int = 8, max_slides: int = 12) -> str:
    tpl = _DECK_ES if (language or 'es').startswith('es') else _DECK_EN
    imgs = '\n' + '\n'.join(f'  - {u}' for u in (image_urls or [])) if image_urls else '(ninguna)' if language.startswith('es') else '(none)'
    return tpl % {'min': min_slides, 'max': max_slides, 'images': imgs, 'theme': theme}
