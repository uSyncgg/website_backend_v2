# Chat messages reach browsers through Supabase Realtime Broadcast, sent by the backend

The backend is the only thing that writes chat messages: it filters and saves each one, then sends it to the private Realtime Broadcast channel `chat:{chat_id}`. Browsers receive messages from that channel and fetch anything they missed over REST (`?after=<last message id>`). They never read the chat tables directly. A `realtime.messages` RLS policy lets only open chat members listen, and no policy lets a browser broadcast. This differs from notifications, which use Postgres Changes. Chat needs a membership check, and its rows hold the unmasked text that players must never see. Broadcast keeps the chat tables backend-only and sends just the payload we build.

## Considered Options

- **Postgres Changes (as notifications do)**: no backend changes, but it needs a SELECT policy on the chat tables, which opens them to the browser. It sends whole rows, including `original_body`, and runs one RLS check per subscriber for every message.
- **Polling the REST API**: the simplest option, but up to a few seconds of lag and constant requests from every open chat.
- **FastAPI WebSockets**: no vendor lock-in, but it means long-lived connections, a pub/sub layer before we can run more than one process (Postgres `LISTEN` doesn't work through the transaction pooler), and reconnect handling on every deploy. This is the planned next step once chat grows beyond matches.

## Consequences

- Saving and broadcasting are two steps. A failed broadcast leaves the message saved but not delivered until the browser refetches, so the frontend always refetches after opening a chat or reconnecting.
- Chat and notifications share the Supabase Realtime connection and message quota.
- A browser that already joined a channel can keep receiving until it reconnects, even after the chat closes. Nothing but the final "closed" event is broadcast after closing.
