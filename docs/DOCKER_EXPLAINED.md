# Docker, explained (for the live review)

Written to actually answer questions on the spot, not to recite. Read
`docs/DOCKER_MANUAL_METHOD.md` for the commands; this is the "why," in
plain language, building up from nothing.

---

## 1. Image vs. container — the one distinction everything else builds on

An **image** is a frozen, read-only snapshot of a filesystem plus
metadata (what command to run, what port it expects, etc.) — think of it
like a `.zip` of "here's a whole operating system's worth of installed
software, ready to run." `docker build` produces one, following the
recipe in a `Dockerfile`.

A **container** is a *running instance* of an image — one image can
produce many containers, the same way one Python class can produce many
objects. `docker run` takes an image and starts a container from it.

**Why this project has 4 `Dockerfile`s but you build "the same way"
every time:** each service (`agent-system-a`, `agent-system-b`,
`mcp-server`, `chatbot-ui`) is different software with different
dependencies, so each needs its own image. `vector-db` doesn't have a
`Dockerfile` in this repo at all — it uses `qdrant/qdrant:latest`, a
public, pre-built image pulled straight from Docker Hub, because we
didn't write Qdrant ourselves.

**What's actually in each `Dockerfile`, and why:**
```dockerfile
FROM python:3.10-slim      # start from a small Debian + Python 3.10 image, not from scratch
WORKDIR /app                # everything from here on happens inside /app in the image
COPY requirements.txt .     # copy just this file first...
RUN pip install -r requirements.txt   # ...and install before copying the rest of the code
COPY . .                    # now copy the actual source code
EXPOSE 8000                 # documentation only — see §4, this does NOT publish the port
CMD ["uvicorn", "api.main:app", "--host", "0.0.0.0", "--port", "8000"]  # what runs when the container starts
```
**Why `requirements.txt` is copied and installed *before* the rest of
the code, as two separate steps:** Docker caches each layer. If you
change your Python code but not your dependencies, Docker reuses the
cached "pip install" layer instead of re-running it — this is the
single biggest thing that makes rebuilds fast instead of taking 3
minutes every time. If `COPY . .` came first, changing any file would
invalidate the cache for everything after it, including the slow
`pip install` step.

`mcp-server`'s Dockerfile has one extra wrinkle worth knowing:
```dockerfile
RUN pip install --extra-index-url https://download.pytorch.org/whl/cpu torch==2.13.0
```
This installs the **CPU-only** build of PyTorch specifically, not the
default one. The default PyTorch install pulls in CUDA/GPU libraries
that add gigabytes to the image for a capability (GPU acceleration) this
service never uses — it does embedding + reranking inference, not
training, so CPU is genuinely sufficient and the smaller image is a real
win, not just a size-golfing exercise.

---

## 2. Networks — the actual point of "Method 2"

By default, every container is isolated — it can't reach any other
container by name. Docker's networking model is what changes that:

- **`docker network create vda5050-net`** creates a private, virtual
  network — think of it like a virtual switch that only these
  containers can plug into.
- Every container started with **`--network vda5050-net`** joins that
  switch and gets an internal IP address on it (separate from anything
  on your actual host machine).
- Crucially, a **user-defined** network like this one (as opposed to
  Docker's old default `bridge` network) comes with **automatic DNS**:
  every container can resolve every other container's `--name` as a
  hostname. That's not a Docker Compose feature — it's a Docker
  networking feature that Compose happens to set up for you
  automatically by creating its own user-defined network under the
  hood. Doing it by hand with `docker network create` is just making
  that same mechanism explicit.

**Why this actually matters for this codebase, concretely:**
`agent-system-a`'s code has `MCP_SERVER_URL=http://mcp-server:8001/mcp`
baked into its environment. `mcp-server` is not a real domain name — it
resolves *only* because both containers are on `vda5050-net` and the
`mcp-server` container was started with `--name mcp-server`. If you
started that same container with `--name banana` instead, that
environment variable would need to say `http://banana:8001/mcp` or the
connection would fail with a DNS resolution error, not a "wrong
password" error — a good thing to know the *failure mode* of, since
that's a natural "what if" question.

**A question you might get: "what's the difference between this and
just using `localhost`?"** `localhost` inside a container refers to
*that container's own network namespace*, not the host machine and not
other containers. `agent-system-a` cannot reach `mcp-server` via
`localhost:8001` — only via the container name over the shared Docker
network, or via the host's published port if it were calling from
outside Docker entirely.

---

## 3. Volumes — why some data survives `docker rm` and some doesn't

A container's own filesystem is **ephemeral** — anything written inside
it disappears the moment you `docker rm` that container. Volumes are the
escape hatch: storage that lives *outside* any one container's
lifecycle, that you explicitly mount in.

This project uses two different kinds, on purpose:

- **`-v "$(pwd)/data:/app/data"` — a bind mount.** Maps a real folder on
  your host machine (`./data`, containing the spec + schemas already
  committed to the repo) directly into the container. Used here because
  that data needs to be visible and editable from *outside* Docker too
  (you can look at `./data/raw_docs` in your editor right now).
- **`-v qdrant_storage:/qdrant/storage` and `-v
  hf_cache:/root/.cache/huggingface` — named volumes.** Docker manages
  where these actually live on disk; you don't need to know or care.
  Used here instead of bind mounts because this data (the embedded
  vectors, the downloaded model weights) isn't something you ever
  need to browse directly — you just need it to persist across
  `docker compose down && up` or container recreation, and named
  volumes are the simpler, more portable way to get that.

**Why this matters concretely:** if `mcp-server`'s container is removed
and recreated without `hf_cache` mounted, it re-downloads ~2GB of
embedding + reranker model weights from Hugging Face on next startup —
correct behavior, just slow. The volume isn't there to make the *code*
work; it's there to make *re-running it* fast.

---

## 4. `EXPOSE` vs. `-p` — a common point of confusion, worth having ready

`EXPOSE 8000` in a `Dockerfile` is **documentation only** — it tells a
human (or `docker inspect`) "this container listens on 8000," but does
**nothing** to actually make that port reachable from outside the
container, not even from the host machine.

`-p 8000:8000` on `docker run` (or `ports:` in Compose) is what actually
**publishes** a port — it creates a real mapping from a port on your
host machine to a port inside the container. The syntax is
`HOST_PORT:CONTAINER_PORT` — they don't have to match (`-p 9999:8000`
would let you reach the container's port 8000 via `localhost:9999` on
your host), though in this project they're kept identical for
simplicity.

**Important distinction for the network question above:** containers
talking to *each other* over `vda5050-net` never go through the
published host ports at all — `agent-system-a` reaching `mcp-server` at
`http://mcp-server:8001` uses the container's internal network directly.
The `-p` flags exist purely so **you**, on your host machine, can reach
these services from a browser or `curl` — remove every `-p` flag and the
system would still work internally, you just couldn't open
`localhost:8080` to look at it.

---

## 5. Comparing the two methods honestly (a fair "why does this project have both" answer)

| | Manual (`docker network` + `docker run`) | Docker Compose |
|---|---|---|
| What it teaches | Every individual primitive (image, container, network, volume) explicitly, one command at a time | The same primitives, but declared once in YAML and orchestrated for you |
| Startup order | You enforce it by running things in the right sequence | `depends_on` handles start order (though not readiness — see below) |
| Repeatability | Every command must be re-typed/re-scripted correctly, in order | `docker compose up` — one command, same result every time |
| Where you'd actually use each | Debugging one container in isolation, understanding what Compose does under the hood, environments without Compose available | Any real multi-container project — this is why almost nobody hand-runs 5 `docker run` commands in production |

**One nuance worth knowing for a "gotcha" question:** neither method
actually waits for a service to be *ready* — only for its container
process to have *started*. `depends_on` in Compose (and the manual
ordering above) controls start order, not readiness — if `mcp-server`'s
Python process takes a few seconds to load the embedding model,
`agent-system-a` could still start before `mcp-server` is actually
able to answer requests, and its first request might fail. This project
doesn't implement health-check-gated startup (Compose supports it via
`depends_on: condition: service_healthy`, with a `healthcheck:` block)
— a legitimate, honest "known limitation" if asked, not something to
pretend isn't a gap.

---

## 6. Likely questions and how to answer them

- **"Why not just run all 5 in one container?"** — Defeats the
  assignment's actual architecture requirement (independent, separately
  deployable services reachable over a real network — see the
  `docker-compose.yml`/manual-run distinction itself is evidence they
  *are* independent), and in practice: different base images, different
  dependency sets, different scaling needs (you might want 3 replicas of
  `agent-system-a` and 1 of `vector-db`, which is impossible if they're
  fused into one container).
- **"What happens if `mcp-server` crashes?"** — `--restart
  unless-stopped` tells Docker's daemon to automatically restart the
  container if its process exits, unless a human explicitly stopped it.
  `agent-system-a`'s calls to it would fail (with a timeout — see
  `docs/PROJECT_GUIDE.md` on the guardrail/timeout design) during the
  restart window, then succeed again once it's back — the DNS name
  keeps resolving to the same container name automatically, no
  reconfiguration needed.
- **"Could you scale `agent-system-a` to multiple replicas with just
  `docker run`?"** — Not cleanly — you'd need distinct `--name`s (names
  must be unique per container) and something in front doing load
  balancing; this is exactly the kind of thing orchestration tools
  (Docker Swarm, Kubernetes) exist for, beyond both methods shown here.
  Honest answer: neither Method 1 nor Method 2 solves this; both are
  single-replica-per-service by design.
- **"Why does `agent-system-a` need both `GROQ_API_KEY` and
  `GOOGLE_API_KEY`?"** — Groq is the primary LLM provider; Gemini is the
  fallback when Groq's free-tier rate limit is hit (see
  `docs/PROJECT_GUIDE.md` Part 4.7's fallback-chain discussion) —
  `agent-system-b` also needs `GOOGLE_API_KEY` since Gemini is used
  directly there, not as a fallback.
