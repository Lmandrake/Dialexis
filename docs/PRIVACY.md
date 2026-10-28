# Privacy: what leaves your machine

Dialexis stores your graph as markdown on local disk. That is a real privacy property and it is
easy to over-read. "Local files" is not "nothing leaves the machine." This page lists every
egress path in the system so you can decide what to feed it *before* you feed it.

---

## Read this part first

**A personal knowledge graph about your professional world is mostly other people's data.**

Names, employers, roles, reporting lines, who attended what, who is working on which project,
occasionally your assessment of someone's work. You did not consent on their behalf, and they
generally do not know the file exists.

That has three consequences worth sitting with:

- **You are the data controller.** Depending on where you and your colleagues live, that may
  carry actual legal obligations. This repository gives you no compliance story and does not
  pretend to.
- **Every egress path below carries third-party data, not just yours.** A web search for a
  colleague's name discloses your interest in that person to a search provider. It is a small
  disclosure. It is not zero.
- **Notes about people read differently to the person described.** Write entity prose you would
  be willing to show its subject. This is not a legal point; it is the one that actually bites.

Nothing in the software enforces any of this. It is a judgment the operator makes on every
ingestion, and the reason the system is deliberately manual.

---

## Egress paths

### 1. The model provider — unavoidable, and the largest

Dialexis's runtime *is* a language model. Every ingestion sends the source material — the
transcript, the email thread, the document — plus whatever existing entity content is needed for
context, to the model provider. Every lookup that reasons over entities sends those entities.

There is no local-model path today. If you cannot send a source to a third-party model, you
cannot ingest it with Dialexis. That is the single most important sentence on this page.

Your provider's data-handling terms govern what happens to it. Read them; they are not
Dialexis's to describe, and they change.

### 2. Web search — deliberate, per-invocation

`scripts/web_search.py` queries `https://html.duckduckgo.com/html/` by default, or Google if
asked. The query text is typically a person's or organisation's name, because that is what web
expansion is for.

Never automatic. It runs when you or the agent explicitly invoke it.

### 3. Web fetch — deliberate, per-URL

`scripts/web_fetch.py` retrieves a page. The target site sees your IP address and whatever your
network reveals. Same as visiting the page in a browser, without the browser's protections.

### 4. Drive mirror — opt-in, off by default

`scripts/publish.py` writes a **one-way** copy of your graph markdown into a cloud-synced folder,
so other devices can read it. It never reads the mirror back into the model.

Off unless you set it up, and additionally **host-gated**: it is a no-op unless the machine's
hostname matches `DIALEXIS_PUBLISH_HOST`, so a second machine cannot overwrite the mirror by
accident. If you enable it, your entire graph is in your cloud provider's storage, subject to
their terms and their breach history.

### 5. Slack relay — opt-in, off by default, the widest surface

`slack/bot.py` accepts direct messages, runs the agent against your graph, and replies. If you
deploy it:

- Your message text goes to Slack's servers.
- The agent's reply — which may quote graph content about real people — goes back through Slack
  and lands in a workspace whose administrators may be able to read it.
- Anyone who can DM the bot can query your graph. There is no per-user authorisation.

Deploy this only into a workspace you control, and only if you are comfortable with that last
point.

---

## What stays local

- **The graph itself.** `graph/` markdown, on disk, in a git repository you own.
- **The derived index.** `index.sqlite` is machine-local and keyed by a hash of the model path;
  it is never committed and two checkouts never share one. It is rebuildable from the markdown,
  so deleting it loses nothing.
- **The HALT log.** `output/halt_log.jsonl` records refused transactions. Gitignored,
  machine-local.
- **Archived sources.** `archive/raw_inbox/` keeps the original of anything ingested, pruned
  after 60 days by `prune_archive.py`.

---

## Keeping content out of a public checkout

If you publish the machinery, or fork this repository, your graph must not come with it. Two
mechanisms:

- **Relocation.** `DIALEXIS_MODEL_DIR`, `DIALEXIS_AGENDA_DIR` and `DIALEXIS_ARCHIVE_DIR` move
  your content out of the checkout entirely — a private repository, or a directory that is not a
  repository at all. See `.env.example`.
- **The leak gate.** `python3 run.py sanitize_check` scans tracked files for secrets, email
  addresses, personal filesystem paths, and — when pointed at your private graph — the names of
  real people and organisations from it. Two tiers, because only one can see private data:
  Tier 1 needs none and can run in CI; Tier 2 needs your graph and therefore runs locally.

**The gate protects the repository, not the graph.** It tells you whether *machinery you are
about to publish* mentions your private world. It does nothing to protect the graph itself, and
it cannot tell you whether ingesting something was wise.

A gate is also not a vault: a pre-push hook is bypassable, and pattern matching misses what it
does not have a pattern for. Treat a clean run as evidence, not proof, and read the diff of any
first public commit yourself.

---

## Secrets

Configuration lives in `.env`, which is gitignored. `.env.example` is the tracked template and
ships with **every value commented out**, so copying it verbatim changes nothing.

Never put a credential in an entity file. Nothing scrubs them, the graph may get mirrored to
cloud storage, and the leak gate will find the shape of a token but will not find your password.
