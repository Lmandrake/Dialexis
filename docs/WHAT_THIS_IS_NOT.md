# What Dialexis is, and what it is not

This page exists so you can find out in five minutes whether Dialexis is the wrong tool for you,
instead of three hours in. It is deliberately unflattering. If something below rules it out,
that is the page working.

---

## What it is

**A personal knowledge graph you can read with your eyes.** Markdown files — one per person,
organisation, project, event, system, concept — with typed relationships between them. The files
*are* the database; there is no separate store they get loaded into.

**A discipline for letting a language model write to it without corrupting it.** The model never
touches files. It emits a transaction manifest describing intended changes; deterministic Python
validates that manifest against a registry, applies it atomically, writes the reciprocal edges
itself, and refuses the whole transaction if any part of it is illegal. The refusal is the
feature.

**Built for one person's professional world.** Who works on what, which organisation owns which
programme, who was in that meeting, what a system is for, how two projects relate. It is for
remembering and reasoning about a working life at a scale past what you can hold in your head.

---

## What it is not

### Not a RAG pipeline, and not a vector store

There is no embedding-based retrieval over document chunks. Nothing is chunked. Lookup is by
entity name and typed relationship traversal, against an explicit graph. The `viz/` directory
computes embeddings, but only to lay out a picture — never to answer a question.

If your problem is "answer questions over 10,000 PDFs," this is the wrong shape entirely.

### Not a server, an app, or a daemon

There is nothing to deploy and no process to start. The runtime is a Claude Code session: you
talk to it, it reads and proposes, Python enforces. `python3 run.py system_status` reports state;
it does not launch anything. The `viz/` visualiser is a separate optional tool and is never the
answer to "run Dialexis."

### Not multi-user, and not collaborative

**One writer at a time**, serialised by a single human being, with git as the only fence. There
is no authentication, no per-entity access control, no merge strategy for two people editing the
same entity, and no notion of "whose" a fact is. Two humans sharing one graph will corrupt each
other's work in ways the enforcer cannot see, because the enforcer checks structure, not
authority.

### Not automatic, and not passive

Nothing watches your mail and quietly learns. Ingestion is a deliberate act: you put a source in
front of it and work through the result. Every ingestion involves semantic judgment — is this the
same person, is that relationship real, does this contradict what we already believed — and a
good fraction of them involve you adjudicating something the model got wrong or could not decide.

Budget for that. The system is designed around the assumption that judgment is the expensive
part and should be spent deliberately, not amortised into a background job.

### Not free, and not fast

Every ingestion costs model tokens, and a substantial source can cost a lot of them. A single
ingestion is minutes of wall clock, not seconds. There is no batch mode that makes this cheap,
because the expensive step is the judgment, not the I/O.

### Not a note-taking app

No editor, no mobile client, no quick-capture, no daily note, no backlink pane. It does not
compete with Obsidian, Logseq or Roam and it will disappoint you if you expect it to. Those tools
are for writing; this is for *structuring what already exists elsewhere* — transcripts, emails,
documents, calendars — into entities and relationships you can query.

You can perfectly well keep using one of those for prose and Dialexis for structure. They are not
substitutes.

### Not a general-purpose graph database

The ontology is small, fixed, and deliberately opinionated: a registry defines the legal entity
types, the legal relationship types, and their inverses. Adding a type is an explicit change to a
checked-in registry file, not a runtime operation. If you need arbitrary schemas, arbitrary
predicates, or SPARQL, use a triple store.

### Not schema-flexible at ingestion time

The enforcer will halt rather than accept a relationship type it does not recognise. That is the
point, and it will feel obstructive on your first day. The intended response is to extend the
registry deliberately, not to loosen the check.

### Not zero-maintenance

There is a health loop: reciprocity audits, duplicate adjudication, semantic-relationship gap
review, index rebuilds. A graph left un-audited for months accumulates duplicate people and
one-directional relationships. The tooling finds them; a human still has to decide.

### Not private by magic

Your graph is local markdown, and that is a genuine privacy property. But **web expansion sends
queries to the open internet**, and running the system at all means sending source material to a
model provider. "Local files" is not the same as "nothing leaves the machine." Decide what you
are comfortable ingesting before you ingest it.

### Not the right shape for three of its own ten types

This is the most useful admission on the page, and it comes from a comparison that exercised all
ten registry types against six other representation families.

Dialexis is an **entity** model. It is strong where the unit of truth is a *durable noun* that
persists across documents — people, organisations, projects, programmes, concepts, places. It is
weak in three places, structurally rather than by omission:

- **Documents and their claims.** An entity is a noun; a claim is a sentence someone is
  accountable for. Claims-with-provenance are the entity model's **worst case by design**. A
  text→graph summarisation system or an attributable-assertion model fits better.
- **Events with validity intervals.** "Scheduled, then cancelled" is a fact that was true for a
  period. Dialexis records it as a property of an entity, which is strictly weaker than a temporal
  knowledge graph that models the interval natively.
- **Intentions and aspirations.** Something you hope to build has no source document and no truth
  value beyond "this person holds this intention." An entity graph has nowhere natural to put it.
  Belief and episodic-memory models are the natural home.

If your knowledge is mostly of those three shapes, Dialexis is the wrong spine. See
[`RELATED_WORK.md`](RELATED_WORK.md) for which families fit which shapes, and the honest caveat
that those comparators were rendered to their published schemas rather than executed.

### Not able to read what it cannot extract

A source that is images — a scanned document, a poster deck exported as pictures, handwriting —
yields **nothing**. In one tested case a five-slide deck contained zero extractable characters and
no text-based system, this one included, recovered a single fact from it.

There is no OCR or vision stage in the pipeline today. "Could not read it" and "could not
represent it" are different failures, and only the second is a design question. Budget for the
first.

### Not a finished argument

The central claim — that the enforcement layer prevents errors a model would otherwise
commit — is currently a design conviction with a plausible mechanism, **not a measured result**.
The evaluation that would settle it has been designed and pre-registered but not run. See
[`RELATED_WORK.md`](RELATED_WORK.md) for what is and is not claimed.

### Not harness-portable, yet

It runs in Claude Code. The agent-facing contract in `agents/` is written to be
harness-neutral, and porting it is documented as a path — but no second implementation exists,
so treat the neutrality as an intention rather than a tested property.

---

## You probably want something else if…

| If you want | Use |
|---|---|
| Question-answering over a document corpus | A RAG framework with a vector store |
| Arbitrary schemas, SPARQL, OWL reasoning | An RDF triple store — and see PKG API, cited in `RELATED_WORK.md` |
| Team or organisational knowledge, many editors | A wiki, or a graph database with real access control |
| Fast prose capture and linking | Obsidian, Logseq, or Roam |
| Something that learns from your data in the background | An agent-memory framework |
| A finished product with support | Not this. This is a working system published as a demonstration. |

---

## You might want Dialexis if…

- You already have a pile of transcripts, emails and documents about a professional world, and
  the thing you lack is *structure* over them rather than search.
- You care that you can open any fact in a text editor, see where it came from, and correct it by
  hand.
- You would rather a write be **refused loudly** than accepted approximately.
- You are willing to spend judgment — yours and a model's — to get a graph you actually trust.
- You find the architectural question interesting: what does it take to let a stochastic system
  write to a store you depend on?

That last one is honestly the best reason. Dialexis is published because the *pattern* seems
worth arguing about, not because it is ready to be your daily tool.
