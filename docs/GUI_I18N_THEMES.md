# GUI, i18n, And Panel Themes

## GUI Structure

The web panel lives in `nebula_gui_flask`.

Main pieces:

- `app.py`: Flask app, Socket.IO, CSP, error handling, session config
- `routes/`: page routes and GUI-side API proxy routes
- `templates/`: page structure
- `static/js/`: client logic
- `static/locales/`: translation bundles
- `static/panel_themes/`: theme definitions

## i18n System

### Locale source of truth

Locales live in:

- `nebula_gui_flask/static/locales/en.json`
- `nebula_gui_flask/static/locales/ru.json`
- `nebula_gui_flask/static/locales/pl.json`

Each file contains:

- `_meta`
- `messages`
- `aliases`

### Locale catalog

The GUI does not hardcode supported locales.

Instead, Flask scans locale files and exposes:

- `GET /api/i18n/catalog`

Returned catalog fields include:

- `code`
- `name`
- `native_name`
- `crowdin_locale`
- `is_default`
- `path`

This means adding a new locale is mostly file-driven.

### Frontend translation flow

`nebula_gui_flask/static/js/i18n.js` does the following:

1. fetches `/api/i18n/catalog`
2. resolves default locale
3. loads fallback messages, usually English
4. resolves preferred locale from:
   - saved panel settings draft
   - saved locale key in localStorage
   - browser locale
   - fallback locale
5. loads locale bundle
6. walks the DOM and translates:
   - text nodes
   - `placeholder`
   - `title`
   - `aria-label`
7. keeps observing DOM mutations and translates new content

### Why aliases exist

Aliases allow the code to translate either by:

- key lookup, or
- matching known English source text to a translation key

That makes the system more forgiving when templates still contain literal English text.

## Theme System

### Theme catalog

Theme index file:

- `nebula_gui_flask/static/panel_themes/index.json`

Current built-in themes:

- `nebula`
- `ember`
- `tide`
- `graphite`

Each entry contains:

- `id`
- `title`
- `description`
- `file`

### Theme payload

Each theme file defines:

- `id`
- `title`
- `description`
- `theme_color`
- `vars`

`vars` is a dictionary of CSS custom properties such as:

- `--bg`
- `--card`
- `--text`
- `--accent`
- `--header-bg`
- `--success`
- `--warning`
- `--danger`

### Theme application

Theme selection is handled on the frontend.

The settings page:

1. fetches `/static/panel_themes/index.json`
2. loads the selected theme file
3. applies CSS variables at runtime
4. updates `<meta name="theme-color">`
5. stores the theme snapshot in localStorage

The runtime event `nebula:theme-applied` lets layout code react to visual changes.

### Persistence

Themes and personal settings are stored per user using keys derived from `window.NebulaRuntime.userId`.

That means:

- settings are browser-local
- theme choice is per-user in that browser
- there is no server-side theme persistence yet

## Interaction Between Settings, Locale, And Theme

The settings screen acts as the control panel for:

- panel language
- theme selection
- personal layout preferences

Locale and theme are both applied immediately in the browser, which keeps the UI responsive and avoids server-side rendering complexity.

## Things To Keep In Mind

- The locale catalog is dynamic, so malformed locale JSON can silently remove a locale from the catalog.
- The theme system is file-driven and easy to extend, but currently trusts theme JSON shape.
- Since persistence is client-side, users changing browsers or devices will not carry their theme/locale preferences automatically.

## Suggested Extension Workflow

### Add a new locale

1. Copy `en.json`.
2. Update `_meta`.
3. Translate `messages`.
4. Keep useful `aliases`.
5. Reload the GUI and verify it appears in `/api/i18n/catalog`.

### Add a new theme

1. Create `static/panel_themes/<theme>.json`.
2. Add the theme entry to `static/panel_themes/index.json`.
3. Define at least the core CSS variables used by `style.css`.
4. Test settings page preview and full navigation rendering.
