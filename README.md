# Tesla Rain Guard — Never Find a Wet Interior Again (Free Tesla Fleet API Rain-Closing Windows)

**Free, open-source rain-guard for your Tesla.** Watches the weather where each parked car is; if rain is falling or about to start and your windows are vented or open, it **closes them automatically** via the official **Tesla Fleet API** (`window_control`). Bonus: on scorching dry days it cracks the windows to vent cabin heat. Every decision is explained in plain English — ask *"why did you close the windows at 3pm?"* and get the answer from its decision log. Part of an AI-architect's Tesla app series: every app embeds OpenAI-compatible endpoints with a pluggable LLM backend.

Works with Model 3, Model Y, Model S, Model X, and Cybertruck.

▶ **[Try the live demo](https://rtmalikian.github.io/tesla-rain-guard/)** — run a pretend car through storms and heatwaves in your browser, no install, no car needed.

## Why?

VW and BMW have closed windows automatically when rain starts **for decades**. Tesla's close-on-lock only works when you lock — park with the windows vented on a hot day, a shower rolls in, and you come back to wet seats. Owners vent in summer; nobody sits by the window watching clouds. This app does the watching:

- **Rain-closing windows** — rain imminent at the car's parked location (or falling right now) + windows not shut → closes them, once, then backs off with a cooldown
- **Hot-day vent crack** — cabin 40°C+ (you pick the number), no rain for 3 hours → cracks the windows to bleed heat; skipped if rain is forecast
- **Never touches a driving car** — decisions only apply to parked cars; shift ≠ P means hands off, always
- **Explains itself** — every check round is written to a decision log; the chat endpoint answers *"why did you close the windows?"* from it, no AI needed
- **Pluggable AI brain** — deterministic rules do everything; connect any OpenAI-compatible LLM (local **Ollama** default) and it answers free-form questions with the fleet + decision history as context. LLM-proposed commands are validated against a strict allowlist (`close_windows`, `vent_windows`) before anything touches your car
- **API-budget friendly** — no-wake reads on a schedule (default every 20 min), weather from free Open-Meteo (no key), commands only on state change

## AI endpoints

```bash
# run it
train serve            # listens on http://127.0.0.1:8643

# use it like the OpenAI API (any client works — set base URL + any key)
curl http://127.0.0.1:8643/v1/chat/completions \
  -H "Content-Type: application/json" \
  -d '{"model": "tesla-rain-guard",
       "messages": [{"role": "user", "content": "why did you close the windows at 3pm?"}]}'

curl http://127.0.0.1:8643/v1/models
curl http://127.0.0.1:8643/v1/embeddings \
  -H "Content-Type: application/json" \
  -d '{"input": "rain close decision history"}'
```

- `POST /v1/chat/completions` — answers from the decision log + live window states (streaming not supported); can also run `close_windows` / `vent_windows` via allowlisted commands
- `GET /v1/models` — advertises the guard as a model
- `POST /v1/embeddings` — deterministic local feature-hashing embeddings (dim 384, zero external calls) over decision-log text
- Optional bearer auth (`TRG_SERVER_API_KEY`) — clients authenticate exactly like OpenAI

## Quick start

```bash
git clone https://github.com/rtmalikian/tesla-rain-guard && cd tesla-rain-guard
python3 -m venv .venv && .venv/bin/pip install -e .
.venv/bin/train setup   # guided setup — plain-language questions, writes .env for you
.venv/bin/train auth    # one-time Tesla authorization + virtual-key pairing
.venv/bin/train preview # see what the guard would do right now (no commands sent)
.venv/bin/train serve   # start the OpenAI-compatible server
# or: .venv/bin/train ask "why did you close the windows?"
# or: .venv/bin/train simulate   # full demo day with pretend cars — no car, no keys
```

Put one round on a schedule (every 20 minutes works well):

```bash
*/20 * * * * cd /path/to/tesla-rain-guard && .venv/bin/train check >> guard.log 2>&1
```

Optional LLM backend (default: local Ollama):

```bash
ollama serve && ollama pull llama3.1   # or set TRG_AI_BASE_URL to any OpenAI-compatible API
```

## How it works

1. Each round takes a **no-wake snapshot** of every configured car (window state, parked/driving, cabin temp, parked GPS).
2. The **weather service** (free Open-Meteo, no key) is read at each parked car's location — rain probability + WMO codes for the next hours.
3. The **guard rules** decide:
   - **Close** — rain falling or expected within your lead time (default 60 min) and windows not confirmed shut. Cooldown prevents re-closing.
   - **Vent** — cabin above your threshold (default 40°C), no rain in the next 3 hours, hot-day venting enabled.
   - **Nothing** — driving cars are never touched; already-closed cars stay as they are; unknown window state is treated as "not confirmed closed" (a close command on shut windows is a harmless no-op, safer than a wet seat).
4. The decision + reason is **logged**; commands go out over the Fleet API `window_control` endpoint.

### Example timeline

```
[09:00] Storm forecast: rain from ~10:30, Model Y windows cracked
        → would CLOSE the windows (rain expected in ~60 min)
[10:05] Rain about to start → CLOSED the Model Y windows
[10:12] Raining now → already closed, nothing to do
[14:00] Hot dry afternoon, cabin 45°C → CRACKED the Model 3 windows (no rain 3h)
[18:00] Owner driving with windows open → NOT TOUCHED (driving cars are off-limits)
```

### Expected API cost

~$0.29/month at the default schedule: ~144 no-wake data reads/day (72 rounds × 2 cars) + a couple of commands on weather days. Commands are ~$1/1000, data reads ~$1/500, wakes ~$1/50 — and the guard **never wakes a sleeping car** to check; it works from the last known parked state instead. Well inside Tesla's free developer credit.

## Tesla developer setup

1. Create a free app at [developer.tesla.com](https://developer.tesla.com) → copy the **Client ID** and **Client Secret** (the `setup` wizard asks for them in plain words).
2. Register your redirect URI (`http://localhost:8080/callback`).
3. Run `train auth`, open the URL, authorize, then [pair the virtual key](https://developer.tesla.com/docs/fleet-api#pairing) with your car (Bluetooth, car in the app).
4. Required scopes: `vehicle_device_data`, `vehicle_location`, `vehicle_cmds`.

## Configuration

Everything lives in `.env` (git-ignored — secrets never leave your machine). Highlights from `.env.example`:

| Key | What it means | Default |
|---|---|---|
| `TRG_VEHICLES` | `Nickname=VIN` pairs | — (wizard asks) |
| `TRG_CHECK_INTERVAL_MINUTES` | How often to check the weather | 20 |
| `TRG_RAIN_LEAD_MINUTES` | "Rain is imminent" look-ahead | 60 |
| `TRG_RAIN_PROB_THRESHOLD` | Forecast probability that counts as rain | 0.5 |
| `TRG_COOLDOWN_MINUTES` | Don't re-send the same command inside this window | 30 |
| `TRG_VENT_ENABLE` | Crack windows on hot dry days | 1 |
| `TRG_VENT_CABIN_TEMP_C` | Cabin temp that triggers the vent crack | 40 |
| `TRG_AI_BASE_URL` / `TRG_AI_MODEL` / `TRG_AI_MODE` | Pluggable LLM backend (`auto`/`always`/`never`) | local Ollama |
| `TRG_SERVER_PORT` / `TRG_SERVER_API_KEY` | The chat server | 8643 |
| `TRG_DRY_RUN` | Report decisions instead of sending commands | 0 |

## FAQ

**Does Tesla already do this?** Tesla closes windows when you lock, and Dog Mode/Camp Mode have their own logic — but vent the windows on a parked car on a hot day and a pop-up shower will soak the seats. There's no built-in rain watch.

**Will it drain my car or cost a lot?** No. The guard reads on a schedule and never wakes a sleeping car; ~$0.29/month at defaults.

**What if the weather forecast is wrong?** The guard also reacts to rain *observed right now* (current weather code), not just forecasts. If the service is down, the round is skipped with a logged reason — it never guesses.

**Does it work with other cars' data or my home?** No — this app talks to the Tesla Fleet API and Open-Meteo only. Nothing else.

**Is my data sent anywhere?** Car data goes to Tesla's API and the weather coordinates (parked location) to Open-Meteo. With `TRG_AI_MODE=never` (default-off AI), nothing else leaves your machine. With a backend configured, data goes **only** to the backend you chose — never a third party.

**Can I ask it to close the windows from anywhere?** Yes: `train ask "close the windows on the Model Y"`, or point any OpenAI client at `/v1/chat/completions`. Driving cars refuse the command.

## Part of a series

One Tesla pain point per day, each with embedded OpenAI-compatible AI: per-location charge limits (`tesla-location-charge-limits`), car↔home continuity (`tesla-home-link`), an IRS-grade mileage log (`tesla-mileage-ledger`), battery degradation estimates, phantom-drain prediction, trip readiness, calendar-aware climate, luxury scenes, and more — all on this GitHub profile.

## License

MIT © Raphael Malikian — free to use, fork, and ship.
