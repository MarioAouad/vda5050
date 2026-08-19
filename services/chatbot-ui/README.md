# chatbot-ui

The dark-themed chat frontend (session sidebar, per-conversation document
upload/deletion), served standalone via nginx rather than bundled into
`agent-system-a` via `StaticFiles`.

## Run standalone (outside Docker)

Any static file server works, e.g.:

```bash
cd services/chatbot-ui
python -m http.server 8080
```

## Run via Docker

From the repo root: `docker compose up chatbot-ui` (brings up `agent-system-a`
too). Open `http://localhost:8080`.

## Configuration

`index.html` calls `agent-system-a` directly at a hardcoded `API` constant
(`http://localhost:8000` — see the top of the `<script>` block), not
through nginx. If you change `agent-system-a`'s published port in
`docker-compose.yml`, update this constant to match.
