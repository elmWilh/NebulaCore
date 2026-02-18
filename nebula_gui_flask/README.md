# nebula_gui_flask

`nebula_gui_flask` is the Flask web interface for Nebula Panel.

## What This Component Does

- Handles administrator and user authentication.
- Provides Dashboard / Users / Containers / Logs pages.
- Manages containers by proxying requests to the Core API.
- Includes deployment UI with progress and logs.

## Entry Point

```bash
cd nebula_gui_flask
python app.py
```

By default, GUI listens on `127.0.0.1:5000`.

## Features

- Role-aware interface behavior (staff/user).
- Staff users get advanced operations and administrative logs.
- Regular users only see assigned containers and related metrics.

## Recommendations

- Use HTTPS and a reverse proxy in production.
- Keep secrets and tokens in `.env`, not in source files.

## License & Copyright

- Copyright (c) 2026 Monolink Systems
- Nebula Open Source Edition (non-corporate)
- Licensed under AGPLv3
