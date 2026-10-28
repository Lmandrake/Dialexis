# Related work

Dialexis sits between three literatures that rarely cite each other: personal knowledge
graphs, verifier-bounded LLM architectures, and first-person systems research. This page says
what each contributes, what Dialexis takes from it, and — the part these pages usually omit —
where someone else got there first.

**Sourcing.** Every claim below is drawn from an abstract read in the authors' own words. Where
a claim would require reading a paper's body, it is not made. Working notes, including three
claims an earlier pass got wrong and had to retract, are in
[`literature/VERIFIED.md`](literature/VERIFIED.md).

---

## The object: personal knowledge graphs

> Skjæveland, Balog, Bernard, Łajewska & Linjordet.
> **An Ecosystem for Personal Knowledge Graphs: A Survey and Research Roadmap.**
> *AI Open* 5:55–69 (2024). [doi:10.1016/j.aiopen.2024.01.003](https://doi.org/10.1016/j.aiopen.2024.01.003) · [arXiv:2304.09572](https://arxiv.org/abs/2304.09572)

The defining survey. It gives the term its meaning and decomposes the problem into
**population**, **representation and management**, and **utilization**. Dialexis is a
contribution to the first two; it does almost nothing novel in the third.

**Where Dialexis does not fit, stated plainly.** Their definition emphasises "(1) data
ownership by a single individual and (2) the delivery of personalized services as the primary
purpose." Dialexis satisfies (1) completely and (2) barely at all — it exists so that a person
can remember and reason about their own professional world, not so that downstream services can
personalise anything. A reader who takes the survey's definition strictly will find Dialexis is
a PKG by ownership and something else by purpose. That is a real boundary, not a quibble, and it
is why the first-person HCI literature below matters as much as the PKG literature does.

> Bernard, Kostric, Łajewska, Balog, Galuščáková, Setty & Skjæveland.
> **PKG API: A Tool for Personal Knowledge Graph Management.** [arXiv:2402.07540](https://arxiv.org/abs/2402.07540)

From the same group, and the closest thing the field has to a reference implementation: an
"RDF-based PKG vocabulary" with "properties for access rights and provenance." Anyone asking
why Dialexis is not RDF should read this first — the alternative is concrete, not hypothetical.
The answer is in [`../DIALEXIS_SYSTEM.md` §9](../DIALEXIS_SYSTEM.md); the short version is that Dialexis
optimises for a substrate a human can read and correct in a text editor at 2am, and accepts a
weaker formal story to get it.

> Kejriwal. **Named Entity Resolution in Personal Knowledge Graphs.** [arXiv:2307.12173](https://arxiv.org/abs/2307.12173)

A survey chapter establishing that entity resolution in PKGs is a recognised problem with a
fifty-year lineage behind it. Cited here to place Dialexis's person-resolution machinery in that
lineage, not as a technique Dialexis improves on.

---

## The pattern: a generative proposer bounded by a sound verifier

> Kambhampati, Valmeekam, Guan, Verma, Stechly, Bhambri, Saldyt & Murthy.
> **LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks.**
> *ICML 2024*, PMLR 235. [arXiv:2402.01817](https://arxiv.org/abs/2402.01817)

The position Dialexis is an instance of: "auto-regressive LLMs cannot, by themselves, do
planning or self-verification," so they should be paired with "external model-based verifiers in
a tighter bi-directional interaction regime." Dialexis's guiding principle — *never ask an LLM to
guarantee a deterministic postcondition* — is this argument applied to a database write path.

**Two honest differences.** Their verifiers check *plan validity*, which is hard. Dialexis's
enforcer checks *structural soundness* — registry legality, reciprocity, atomicity — which is
much easier, and that makes Dialexis's guarantee more credible than the general case rather than
less. And they explicitly reject "simply pipelining LLMs and symbolic components" in favour of
tight bidirectional integration; Dialexis's manifest hand-off is closer to a pipeline than to
what they advocate. It borrows the principle, not the architecture.

---

## The nearest neighbour: governed memory before consolidation

> Lam, Li, Zhang & Zhao.
> **Governing Evolving Memory in LLM Agents: Risks, Mechanisms, and the Stability and Safety
> Governed Memory (SSGM) Framework.** [arXiv:2603.11768](https://arxiv.org/abs/2603.11768)

The closest published statement of what Dialexis does. SSGM "decouples memory evolution from
execution by enforcing consistency verification, temporal decay modeling, and dynamic access
control **prior to any memory consolidation**," and offers a taxonomy of memory-corruption risks
including semantic drift and topology-induced leakage.

It is also, in its own words, "a conceptual governance architecture" developed "through formal
analysis and architectural decomposition." It specifies; it does not build.

**So the relationship is instantiation, not competition.** Where SSGM says consistency must be
verified before consolidation, Dialexis supplies a mechanism — an atomic transaction that
applies a manifest whole or halts — and the operational evidence that living inside such a
constraint is practical rather than merely desirable. If you want the theory, read SSGM. If you
want to know what it costs to actually work this way, read this repository.

---

## Adjacent: personal AI grounded in a graph

Two papers from one group (overlapping author lists — treat them as a single research programme,
not independent confirmation):

> Conte, Mattiace, Carmosino, Aghilar, Servedio, Musicco, Anelli, Di Noia & Donini.
> **RUVA: Personalized Transparent On-Device Graph Reasoning.** [arXiv:2602.15553](https://arxiv.org/abs/2602.15553)

> Servedio, Aghilar, Mattiace, Carmosino, Musicco, Conte, Anelli, Di Noia & Donini.
> **The EpisTwin: A Knowledge Graph-Grounded Neuro-Symbolic Architecture for Personal AI.** [arXiv:2603.06290](https://arxiv.org/abs/2603.06290)

RUVA makes the case against black-box retrieval better than this README would: vector databases
"suffer from a fundamental lack of accountability," and "'deleting' a concept from a vector space
is mathematically imprecise, leaving behind probabilistic 'ghosts'." It proposes "the first
'Glass Box' architecture designed for Human-in-the-Loop Memory Curation."

**Dialexis makes no priority claim on human-in-the-loop curation — RUVA does, and it is in
print.** The two differ in axis: RUVA's contribution is inspectability and precise redaction —
the right to be forgotten. Dialexis's is write-time correctness. A system could want both; they
are not in tension.

EpisTwin lifts multimodal cross-application data into semantic triples with visual re-grounding.
Dialexis is text-only, so the overlap is thinner than the title suggests.

> Hu, Zhuang, Potetz, Fathi, Hu, Samari & Zhou.
> **Life-Bench: A Benchmark and Knowledge Graph Framework for Multimodal Personalization.** [arXiv:2602.19001](https://arxiv.org/abs/2602.19001)

Listed because it is the obvious benchmark to reach for and it does not fit: Life-Bench is
"photo-centric," and Dialexis has no images. Its finding is worth carrying anyway — retrieval
accuracy "falls below 0.40 on aggregated tasks" across all four paradigms tested, so multi-hop
reasoning over personal history is unsolved generally, not just here.

---

## The method: why a study of one is not an apology

Dialexis has one user, one graph, and months of daily use. In machine learning that is not a
result. In human–computer interaction it is a recognised methodology with its own literature:

> Lucero. **A Sample of One: First-Person Research Methods in HCI.** *DIS 2019.*

> Lucero. **Longitudinal First-Person HCI Research Methods.** Human–Computer Interaction Series, 2021.

> Rapp. **Autoethnography in Human-Computer Interaction: Theory and Practice.**
> In *New Directions in Third Wave HCI*, 2018.

> Gamboa. **More Samples of One: Weaving First-Person Perspectives into Mainstream HCI.** *DIS 2024.*

**Two different n's, which must not be conflated.** The *deployment* is n=1: one user, one graph.
The *representation comparison* above is n=10: ten knowledge types, each with a real entity and a
real source file. They support different claims. The comparison can say which representation fits
which knowledge shape; only the deployment can say what it is like to live under a write
discipline for months, and only the unrun evaluation could say whether that discipline prevents
anything.

First-person and autoethnographic system studies are published continuously at DIS, CHI and
NordiCHI. What those venues expect, and what Dialexis therefore owes, is reflexivity about the
researcher's own position — not merely a report that the system worked. Being both the author and
the sole user is a fact to reason about, not a caveat to bury.

---

## Standards Dialexis declines, and why the declining is a choice

> **Shapes Constraint Language (SHACL).** W3C Recommendation, 20 July 2017. https://www.w3.org/TR/shacl/

> **PROV-Overview.** W3C Working Group Note, 30 April 2013. https://www.w3.org/TR/prov-overview/

SHACL is a W3C Recommendation for validating graph shapes. `validate.py` is a hand-rolled,
narrower special case of it. That is a decision with costs, and pretending otherwise would be
the fastest way to lose a semantic-web reader; the reasoning is in
[`../DIALEXIS_SYSTEM.md` §9](../DIALEXIS_SYSTEM.md). PROV is the provenance vocabulary Dialexis's confidence
levels and source ledger informally approximate.

---

## Representation families, and where this one loses

A separate comparison exercised all ten of Dialexis's registry types against six other
documented representation families. Every test case was chosen to be a thing that exists **both**
as an entity in the graph *and* as a real source file on disk, so there is no synthetic data and
no circularity. The per-type spread, not an overall score, was the finding.

**Dialexis wins or ties on six of ten types — and those six are all the same kind of thing:**
durable nouns that persist across many documents and accrete typed, inverse-linked relations.
People, organisations, projects, programmes, concepts, places.

**It loses on three, and the losses are structural rather than incidental:**

| Type | Better family | Why |
|---|---|---|
| `document` | Text→graph summarisation, or attributable-assertion models | Claims with their own provenance are the entity model's **worst case, by design**. An entity is a noun; a claim is a sentence someone is accountable for. |
| `event` | Temporal knowledge graph | "Scheduled, then cancelled" is a *validity interval*, not a naming problem. Dialexis models it as a fact about an entity and that is strictly weaker. |
| `dream` | Belief / episodic-memory models | An aspiration has no source document and no truth value beyond "the person holds this intention." An entity graph has nowhere natural to put that. |

The families:

> Edge, Trinh, Cheng et al. **From Local to Global: A Graph RAG Approach to Query-Focused
> Summarization.** [arXiv:2404.16130](https://arxiv.org/abs/2404.16130)

"A graph-based approach to question answering over private text corpora." Home turf is exactly
Dialexis's worst type: turning a *document corpus* into entities plus covariate claims and
readable community summaries.

> Rasmussen, Paliychuk, Beauvais et al. **Zep: A Temporal Knowledge Graph Architecture for Agent
> Memory.** [arXiv:2501.13956](https://arxiv.org/abs/2501.13956)

"A memory layer service for AI agents" handling "dynamic knowledge integration from diverse
sources." The only family in the comparison that natively models validity intervals — a fact true
only for a period — rather than encoding cancellation as a naming hack.

> Park, O'Brien, Cai et al. **Generative Agents: Interactive Simulacra of Human Behavior.**
> [arXiv:2304.03442](https://arxiv.org/abs/2304.03442)

"Computational software agents that simulate believable human behavior," via a memory stream with
reflection. The natural home for evolving, first-person, unverifiable intentions.

> Li, Gao, Meng et al. **A Survey on Truth Discovery.** [arXiv:1505.02463](https://arxiv.org/abs/1505.02463)

"Integrates multi-source noisy information by estimating the reliability of each source." The
conflict-overlay family: what you reach for when two sources disagree and you want a weighted
resolution rather than a halt.

Also compared: **nanopublications** (single attributable assertions carrying their own provenance
and publication metadata), **labelled property graphs** (cheap and strong for hierarchy and
membership, with no native provenance, confidence, or identity resolution), and **RDF/OWL+SHACL**
— the true peer of a closed registry, discussed in
[`../DIALEXIS_SYSTEM.md`](../DIALEXIS_SYSTEM.md) §9.

⚠️ Two foundational references were **not obtained**: the nanopublication anatomy paper (Groth,
Gibson & Velterop) and the PSL paper (Bach, Broecheler, Huang & Getoor, JMLR 2017). The
comparison used the canonical software and specifications instead, and flags the foundational
papers as unverified rather than asserting locators nobody checked.

### The comparison's own load-bearing weakness

**Dialexis was executed; the six comparators were emulated.** Dialexis results came from a real
run against a live graph — commit, reconcile, validate, exit 0. The comparators were rendered
faithfully to their *published schemas* and never executed on a server.

That is disclosed rather than buried, and it is the first thing a referee should attack. Read the
marks as **analytical fit against documented schemas**, not as measured performance. Anyone
wanting a stronger claim should stand up at least one comparator for real.

### Extraction fidelity is a separate axis

The sharpest result in the comparison was not about representation at all. One source — a
five-slide poster deck — was entirely rasterised images with **zero extractable characters**, and
**no text-based system extracted a single fact from it**, Dialexis included.

**"Could not read the source" must be scored separately from "could not represent the facts,"**
or a vision-gated failure gets misattributed to the data model. Every ingestion pipeline needs a
named, budgeted extraction front-end, and every evaluation of one needs a gate that excludes
unreadable sources rather than scoring them as clean. This finding forced an amendment to
Dialexis's own
[evaluation protocol](superpowers/specs/2026-09-11-evaluation-protocol.md), which had a
false-refutation path through exactly that hole.

---

## What Dialexis claims, in one paragraph

An LLM proposes a transaction manifest; a deterministic enforcer applies it atomically or halts;
the substrate is markdown a human can audit and repair by hand. SSGM specifies that consistency
must be verified before consolidation but supplies no implementation. LLM-Modulo establishes that
generative models need external verifiers but targets plan validity rather than structural
soundness. The PKG ecosystem survey defines the object but centres service personalisation rather
than sensemaking. Dialexis contributes the mechanism, and the record of what it is like to live
with it.

**And a narrower claim than that suggests.** The comparison above places Dialexis as a **spine**,
not a universal representation: it is the right shape for durable nouns and the wrong shape for
claims, for time-bounded validity, and for beliefs. The interesting architecture is not this system
alone but *spine + composable overlays + a budgeted extraction front-end* — and no single family in
the comparison covers that space by itself. A reader looking for "which representation should I
use" gets a better answer from the per-type map than from any verdict about Dialexis.

**What Dialexis does not yet claim.** That the enforcer measurably prevents errors an LLM would
otherwise commit. That is the load-bearing claim and it is currently an argument, not a
measurement. The protocol that would settle it — including its pre-registered refutation
condition, and the reasons the obvious retrospective version of it cannot work — is at
[`superpowers/specs/2026-09-11-evaluation-protocol.md`](superpowers/specs/2026-09-11-evaluation-protocol.md).
Until that runs, treat "halting is success" as a design conviction with a plausible mechanism
behind it, and nothing stronger.
