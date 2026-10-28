# Dialexis — literature positioning

Scan date: 2026-09-11. Method: arXiv API (`export.arxiv.org/api/query`) via WebFetch.

> **SUPERSEDED IN PART — read `VERIFIED.md` first.** The ⚠️ claims below were cleared on
> 2026-09-11 by fetching full abstracts verbatim. **Three of them turned out to be wrong**,
> including the one about SSGM that was holding back any novelty claim (it is a *conceptual*
> framework with no implementation, so it is a citation rather than a collision). This file is
> kept as the dated record of what the first pass believed; `VERIFIED.md` is authoritative on
> everything it covers. Its §6 (first-person HCI methodology) was verified independently and
> still stands.

**Epistemic status.** Titles, authors, arXiv IDs, and dates below are verified arXiv
metadata. Claims about what a paper *argues* are drawn from abstract summaries returned by
the fetch tool — they are **second-hand until the PDF is read**. Every such claim is marked
⚠️. Nothing here may be cited in a published document until the ⚠️ is cleared by reading
the source. (An OpenAlex relevance query was also run and discarded: it returned LeCun 1998
and nursing-methodology papers, i.e. it did not match the term at all.)

---

## 1. Terminology verdict

**Use "personal knowledge graph" (PKG). Retire "world model".**

"World model" in the ML literature means a *learned, predictive* model of environment
dynamics used for rollout and planning (Ha & Schmidhuber lineage, Dreamer, JEPA). Dialexis
has no dynamics, no prediction, no latent space, and no rollout. Using the term invites the
wrong reviewer and reads as a category error.

"Personal knowledge graph" is an established term of art with a foundational survey, an
identifiable author community, and active 2025–2026 publication.

### Canonical anchor

> **An Ecosystem for Personal Knowledge Graphs: A Survey and Research Roadmap**
> Skjæveland, Balog, Bernard, Łajewska, Linjordet — arXiv:2304.09572 — published in *AI Open* (2024)

Recurring author community: **Krisztian Balog**, **Martin G. Skjæveland**, **Nolwenn
Bernard**, **Oshani Seneviratne**, **Fernando Spadea**.

⚠️ Ilkou (arXiv:2203.08507) reportedly frames PKGs as "small-sized user-centric knowledge
graphs introduced by the semantic web community" — worth reading for a citable definition.

**Implication for our docs:** the field is semantic-web-flavored. The
"why not OWL/SHACL?" objection is therefore live and must be answered in
`ARCHITECTURE.md`, not left implicit.

---

## 2. Intellectual ancestor: LLM-Modulo

> **LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks**
> Kambhampati, Valmeekam, Guan, Verma, Stechly, Bhambri, Saldyt, Murthy — arXiv:2402.01817 (2024)

> **Robust Planning with Compound LLM Architectures: An LLM-Modulo Approach**
> Gundawar, Valmeekam, Verma, Kambhampati — arXiv:2411.14484 (2024)

> **On the Planning Abilities of Large Language Models: A Critical Investigation**
> Valmeekam, Marquez, Sreedharan, Kambhampati — arXiv:2305.15771 (2023)

⚠️ 2411.14484 reportedly describes "an LLM paired with a complete set of sound verifiers
that validate its output, re-prompting it if it fails." ⚠️ 2305.15771 reportedly measures
autonomous LLM planning success at **~12%**.

**This is our framing.** Dialexis is an LLM-Modulo architecture applied to *personal
knowledge graph maintenance* rather than to planning. The one-sentence positioning:

> Dialexis applies the LLM-Modulo pattern — a generative proposer bounded by sound external
> verifiers — to the write path of a personal knowledge graph, where the verifier is a
> transaction manager that applies or halts, never partially succeeds.

This is a *stronger* claim than "we validate LLM output," because it inherits an existing
research vocabulary and cites a group that has already argued the general case. We do not
need to re-argue that LLMs can't guarantee correctness. That argument is made.

---

## 3. The gap — confirmed twice, independently

Two separate sweeps produced converging statements that write-time enforcement is
underdeveloped. This is the contribution-shaped hole.

**Agent memory systems.** Of ten surveyed works on LLM agent memory, exactly one proposes
validation before commit:

> **Stability and Safety-Governed Memory (SSGM)** — Lam et al. — arXiv:2603.11768 (2026-03-12)

⚠️ Reportedly proposes "consistency verification, temporal decay modeling, and dynamic
access control prior to any memory consolidation," and decouples memory evolution from
execution. **This is the closest prior art to our thesis and must be read before we
publish any novelty claim.** Also relevant: ⚠️ Du et al. (arXiv:2603.07670) reportedly
describes a write-manage-read loop with "contradiction handling" and "write-path filtering"
but no transactional guarantees.

Everything else in that sweep (2609.08599, 2602.19320, 2605.06716, 2501.07278) treats
memory writes as best-effort append plus reflection, optimizing retrieval rather than
write correctness.

**LLM-driven KG construction.** The sweep's summary conclusion:

> "Most approaches focus on post-hoc evaluation rather than write-time enforcement. The
> field emphasizes hallucination mitigation and evaluation frameworks, but explicit
> constraint validation mechanisms remain underdeveloped."

Surveyed: AutoSchemaKG (2505.23628), RAKG (2504.09823), TAdaRAG (2511.12520), CooperKGC
(2312.03022), FineREX (2606.19710), ontology-grounded generation (2412.20942). ⚠️ The last
grounds generation in an authored Wikidata-derived ontology — the nearest neighbor to our
registry — but reportedly does not halt on violation.

**So the defensible claim is narrow and real:** not "LLMs need verifiers" (Kambhampati
established that), not "personal knowledge graphs are useful" (Balog et al. established
that), but *transactional, halting, invariant-enforcing write semantics for an
LLM-maintained personal knowledge graph, on a human-auditable substrate, evidenced by
longitudinal single-user deployment.*

---

## 4. Competitive set — read before claiming novelty

| Work | ID | Why it matters |
|---|---|---|
| PKG API: A Tool for PKG Management | 2402.07540 | Bernard, Balog et al. A *system paper* in exactly our space. Proves system papers land here; also the closest published tool. |
| SSGM (stability/safety-governed memory) | 2603.11768 | Closest prior art on pre-commit validation. Highest-priority read. |
| EpisTwin: KG-grounded neuro-symbolic architecture for Personal AI | 2603.06290 | ⚠️ Reportedly argues RAG "fails to capture semantic topology." Same instinct as ours. |
| RUVA: Personalized Transparent On-Device Graph Reasoning | 2602.15553 | ⚠️ Reportedly opens by attacking "black box" RAG and vector DBs for lacking accountability. **This is our argument, already in print.** |
| Named Entity Resolution in Personal Knowledge Graphs | 2307.12173 | Kejriwal. Direct precedent for our §6c person-resolution mechanism. |
| Life-Bench | 2602.19001 | ⚠️ A benchmark for reasoning over multimodal life histories. Possible evaluation vehicle — would answer the "no benchmark" objection. |

**Note the dates.** Six of these are 2026. This area is actively contested right now, which
validates the topic and raises the bar simultaneously. We cannot claim the space; we can
claim the write discipline.

---

## 5. Verified standards citations

Both retrieved directly (HTTP 200, full text on disk):

> **Shapes Constraint Language (SHACL)** — W3C **Recommendation**, 20 July 2017 —
> https://www.w3.org/TR/shacl/

> **PROV-Overview: An Overview of the PROV Family of Documents** — W3C **Working Group
> Note**, 30 April 2013 — https://www.w3.org/TR/prov-overview/

Note the status difference, which is itself useful: SHACL is a Recommendation (a standard
we must justify not using); PROV-Overview is a Working Group Note (weaker normative force,
so alignment is optional and citing it is cheap).

---

## 6. VERIFIED — first-person / n=1 evaluation is legitimate in HCI

Retrieved 2026-09-11 from the Semantic Scholar API. This was the load-bearing open question
and it resolves **in our favour**. Citation counts are Semantic Scholar's.

> **A Sample of One: First-Person Research Methods in HCI** — A. Lucero — **DIS 2019** — 114 citations

> **Longitudinal First-Person HCI Research Methods** — A. Lucero — Human–Computer Interaction
> Series, 2021 — 14 citations

> **Autoethnography in Human-Computer Interaction: Theory and Practice** — A. Rapp — in
> *New Directions in Third Wave Human-Computer Interaction*, 2018 — 57 citations

> **More Samples of One: Weaving First-Person Perspectives into Mainstream HCI** —
> M. Gamboa — **DIS 2024** — 23 citations

The method is named, cited, and **actively extended** — the search returned first-person and
autoethnographic work published at DIS, CHI, NordiCHI, ASSETS and CHI Extended Abstracts
continuously from 2018 through 2026. Lucero 2021 is titled almost exactly what Dialexis
would be: a *longitudinal first-person* study.

**Consequence for venue strategy.** Our n=1 is not a defect to apologise for; it is a
recognised methodology with a canonical citation. **DIS is the concrete target**, with CHI
and NordiCHI as alternates. This displaces the earlier unverified guess that K-CAP was the
right lineage — we no longer need it.

Caveat: an established *method* is not an automatic accept. These are venues where a
first-person contribution must be reflexive about the researcher's own position, not merely
report that the system worked. `EVALUATION.md` should be written with that expectation.

---

## 7. FAILED — do not treat these as checked

The Fetcher `SEARCH` route returned unusable results on 2026-09-11. Mojeek was blocked,
Brave errored, and the Bing fallback matched on single tokens rather than the query:
"K-CAP" returned the letter K, "CIDR conference" returned subnet calculators, "personal
information management research agenda" returned dictionary definitions of *personal*, and
the autoethnography query returned LinkedIn's homepage.

**Consequently the following remain UNVERIFIED RECALL and must not be asserted anywhere:**

- That **K-CAP** (Knowledge Capture) is the appropriate venue lineage.
- That **CIDR** accepts opinionated-architecture systems papers without benchmarks.
- That **ISWC** has an in-use / deployed-systems track and what its criteria are.
- **Paulheim**-lineage KG refinement/quality survey.
- Whether **personal information management** is a distinct field worth citing alongside PKG.

Resolved since: the HCI-methodology question — see §6.

### Route notes for the retry

- **Semantic Scholar API (`api.semanticscholar.org`) — WORKS, and is the best route.**
  Rate-limits (HTTP 429) when called directly from Claude Code; file it through Fetcher
  instead, which retries automatically. This is what resolved §6.
- **DBLP — BLOCKED.** `dblp.org` sits behind Anubis bot-checking. It returns HTTP **200**
  with a "Making sure you're not a bot!" page, so it looks like a success and silently
  delivers nothing. Do not trust a 200 from dblp.org; check the body. Fetcher's browser
  fingerprinting does not get through.
- **Fetcher `SEARCH` — unusable for academic queries** as of this date; see above.
- **arXiv API (`export.arxiv.org/api/query`) — works well** and is the right route for
  anything preprinted. Useless for HCI and venue questions, which are not on arXiv.

---

## 6. Consequences for the release docs

1. `RELATED_WORK.md` anchors on arXiv:2304.09572 for the term, arXiv:2402.01817 for the
   pattern, and cites §3's two gap statements as the motivation.
2. `ARCHITECTURE.md` must contain an explicit "why not OWL/SHACL/RDF" subsection.
3. Retire "world model" everywhere, including the `working_model/` directory name if the
   split is happening anyway — `working_model/` → the term of art is a *personal knowledge
   graph*, so `graph/` or `pkg/` is more legible to an arriving reader. (Rename is nearly
   free during the machinery/content split; expensive later.)
4. The evaluation should be designed to answer the specific objection these papers raise:
   post-hoc evaluation is common, write-time enforcement is not — so measure what
   enforcement caught.
