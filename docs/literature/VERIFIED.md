# Dialexis — verified literature positioning

Verified 2026-09-11 by fetching **full abstracts verbatim** from the arXiv API
(`export.arxiv.org/api/query?id_list=…`), which returns the authors' own words rather than a
tool's summary of them.

**What this file supersedes.** `README.md` in this directory was built from abstract
*summaries* and marked every content claim ⚠️. The claims below are cleared to abstract
level: the wording is the authors' own. Claims about method *internals* (anything only the
body of a paper could establish) are still marked ⚠️ PDF.

**Three of the earlier claims were wrong.** They are corrected in §5. One of them —
about SSGM — was the reason for holding back any novelty claim, and its correction is
favourable.

---

## 1. Terminology — confirmed, with a caveat that matters

> **An Ecosystem for Personal Knowledge Graphs: A Survey and Research Roadmap**
> Skjæveland, Balog, Bernard, Łajewska & Linjordet
> arXiv:2304.09572 · **AI Open 5:55-69 (2024)** · DOI 10.1016/j.aiopen.2024.01.003

Verified journal reference and DOI. This is the anchor, and "personal knowledge graph" is the
right term.

**The caveat.** Their definition is explicit about two criteria, quoted:

> "We propose our own definition of a PKG, emphasizing the aspects of (1) **data ownership by
> a single individual** and (2) the **delivery of personalized services as the primary
> purpose**."

Dialexis satisfies (1) unambiguously. It does **not** obviously satisfy (2): its purpose is
human sensemaking and recall, not feeding personalization to downstream services. A reviewer
in this community will notice. Two honest options, and the choice should be made deliberately
rather than by omission:

- Argue Dialexis is a PKG under (1) and *extends* the ecosystem's scope to a purpose the
  survey did not centre — sensemaking rather than service personalization. This is defensible
  and is probably the stronger paper.
- Or adopt a different framing (personal information management, first-person HCI) and cite
  the PKG survey as adjacent rather than as the home literature.

Their lifecycle decomposition is useful for locating the contribution: **population**,
**representation and management**, **utilization**. Dialexis contributes to the first two.

---

## 2. The pattern ancestor — confirmed, and it is peer-reviewed

> **LLMs Can't Plan, But Can Help Planning in LLM-Modulo Frameworks**
> Kambhampati, Valmeekam, Guan, Verma, Stechly, Bhambri, Saldyt & Murthy
> arXiv:2402.01817 · **ICML 2024, PMLR 235**

Verified venue — this is an ICML **position paper**, not a preprint. Quoting:

> "We argue that auto-regressive LLMs cannot, by themselves, do planning or self-verification
> (which is after all a form of reasoning)… We present a vision of LLM-Modulo Frameworks that
> combine the strengths of LLMs with external model-based verifiers in a tighter
> bi-directional interaction regime."

Two nuances the earlier note missed:

- Their verifiers are **model-based verifiers** for planning and reasoning. Dialexis's
  enforcer is narrower and easier: a transaction manager checking *structural invariants*
  (registry legality, reciprocity, atomicity). That is a **soundness check, not a plan
  validity check** — worth saying plainly, because it makes Dialexis's guarantee more
  tractable than the general LLM-Modulo case, not less.
- They explicitly reject "simply pipelining LLMs and symbolic components" in favour of
  "tighter integration." Dialexis's manifest hand-off is closer to a pipeline than to their
  bi-directional regime. Do not overclaim kinship on that axis.

⚠️ PDF: the "~12% autonomous planning success" figure attributed to Valmeekam et al.
(arXiv:2305.15771) is **still unverified** — that paper was not re-fetched.

---

## 3. The closest prior art — and it is conceptual, not built

> **Governing Evolving Memory in LLM Agents: Risks, Mechanisms, and the Stability and Safety
> Governed Memory (SSGM) Framework**
> Chingkwun Lam, Jiaxin Li, Lingfei Zhang & Kuo Zhao — arXiv:2603.11768v2 (2026-03-12)

This was the paper being treated as a possible collision. The abstract settles it, quoted:

> "we propose the Stability and Safety-Governed Memory (SSGM) framework, **a conceptual
> governance architecture**. SSGM decouples memory evolution from execution by enforcing
> consistency verification, temporal decay modeling, and dynamic access control **prior to any
> memory consolidation**. **Through formal analysis and architectural decomposition**, we show
> how SSGM can mitigate…"

**Verdict: not a collision — a citation.** SSGM proposes pre-consolidation governance as a
*conceptual architecture*, argued through formal analysis. It reports no implementation and no
deployment. Dialexis is a working system doing a specific version of exactly that, in daily
use for months on ~4,742 entities.

That reframes the contribution favourably, and more honestly than claiming independent
novelty:

> Dialexis is an implemented, deployed instance of pre-consolidation memory governance. Where
> SSGM specifies that consistency must be verified before consolidation, Dialexis supplies the
> mechanism — an atomic transaction that either applies a manifest whole or halts — and the
> operational evidence that living with such a constraint is practical.

Also note what SSGM does **not** claim, which is where Dialexis adds: no transactional
semantics, no atomicity, no halting, and its subject is autonomous *agent memory* rather than
a human-curated knowledge graph. Its risk taxonomy (memory corruption, semantic drift,
topology-induced leakage) is nonetheless a ready-made vocabulary worth borrowing.

⚠️ PDF: whether SSGM's "consistency verification" resembles registry-legality checking, and
whether its taxonomy names Dialexis's failure modes, needs the body.

---

## 4. The adjacent programme, and the field's reference implementation

**One lab owns two of the nearest neighbours.** RUVA and EpisTwin share most of their author
list (Servedio, Aghilar, Mattiace, Carmosino, Musicco, Conte, Anelli, Di Noia, Donini) — treat
them as one research programme, not two independent confirmations.

> **RUVA: Personalized Transparent On-Device Graph Reasoning** — Conte et al. —
> arXiv:2602.15553 (2026-02-17)

> "The Personal AI landscape is currently dominated by 'Black Box' Retrieval-Augmented
> Generation… they suffer from a fundamental lack of accountability… We propose Ruva, the
> **first 'Glass Box' architecture designed for Human-in-the-Loop Memory Curation**… enabling
> users to inspect what the AI knows and to perform precise redaction of specific facts."

The anti-RAG argument *is* in print, as feared — but its contribution axis is **inspectability
and the right to be forgotten**, not write-time correctness. Dialexis's axis is orthogonal.
Note they claim a "first," so any Dialexis priority claim about human-in-the-loop curation
should be dropped; the write-discipline claim is unaffected.

> **The EpisTwin: A Knowledge Graph-Grounded Neuro-Symbolic Architecture for Personal AI** —
> Servedio et al. — arXiv:2603.06290 (2026-03-06)

Uses "Multimodal Language Models to lift heterogeneous, cross-application data into semantic
triples," with visual re-grounding and a synthetic benchmark (PersonalQA-71-100). Heavily
multimodal — further from Dialexis than the title suggests.

> **PKG API: A Tool for Personal Knowledge Graph Management** — Bernard, Kostric, Łajewska,
> Balog, Galuščáková, Setty & Skjæveland — arXiv:2402.07540 (2024-02-12)

Same group as the anchor survey. Quoting: "we present an **RDF-based PKG vocabulary**… along
with properties for **access rights and provenance**."

**This raises the bar on the RDF question.** It is not hypothetical that one *could* use RDF —
the field's own reference implementation, from the group that wrote the defining survey, *is*
RDF. `ARCHITECTURE.md`'s "why not RDF/OWL/SHACL" section must therefore engage with PKG API
specifically, not with RDF in the abstract. Note also that they cover provenance and access
rights, which Dialexis has in the form of confidence levels and a source ledger — a
comparison, not a gap.

> **Named Entity Resolution in Personal Knowledge Graphs** — Mayank Kejriwal —
> arXiv:2307.12173 (2023-07-22)

Correction: this is a **book chapter and literature review**, not a competing technique —
"This chapter will discuss… We also discuss some challenges… Next, we provide a brief
literature review." Cite it to establish that ER-in-PKGs is a recognised problem, not as prior
art against the §6c resolution mechanism.

---

## 5. Corrections to the earlier record

| Earlier claim | Status | Correction |
|---|---|---|
| SSGM is "the closest prior art and must be read before any novelty claim" | **Corrected — favourably** | It is a *conceptual* framework with no implementation. Cite it as the specification Dialexis instantiates. The novelty claim survives, narrowed to implementation + deployment evidence. |
| Life-Bench "might serve as an external benchmark, answering the no-evaluation objection" | **Wrong — retract** | Life-Bench is **multimodal and photo-centric**: "photo-centric personal histories," 11,800 QA pairs over images. Dialexis has no images. Unusable. The evaluation must still be built. |
| Kejriwal is "direct precedent for our §6c person resolution" | **Overstated** | It is a survey chapter. It establishes the problem's standing, not a technique to beat. |
| RUVA "makes our argument already in print" | **Confirmed but narrower** | The anti-RAG/accountability argument is theirs. The write-time-correctness argument is not. |
| The PKG anchor definition fits Dialexis | **Needs a decision** | Their criterion (2) is "personalized services as the primary purpose." Dialexis is for sensemaking. See §1. |

Useful datum from Life-Bench even though it is unusable as our benchmark: retrieval accuracy
"falls below 0.40 on aggregated tasks," i.e. multi-hop reasoning over personal history is an
open problem across all four paradigms they tested.

---

## 6. The claim, restated after verification

> Dialexis is an implemented, deployed instance of **pre-consolidation governance for a
> personal knowledge graph**: an LLM proposes a transaction manifest, and a deterministic
> enforcer applies it atomically or halts, on a human-auditable markdown substrate. SSGM
> (arXiv:2603.11768) specifies that consistency must be verified before consolidation but
> supplies no implementation; LLM-Modulo (ICML 2024) establishes that generative models need
> external verifiers but targets plan validity rather than structural soundness; the PKG
> ecosystem survey (AI Open 2024) defines the object but centres service personalization
> rather than sensemaking. Dialexis's contribution is the mechanism, and months of
> single-user operational evidence that living inside it is practical.

Still required, and not obtainable from the literature: **the measurement.** Replay ingestions
with the enforcer logging rather than enforcing, and count what would have landed. No external
benchmark fits.

---

## 7. Still unverified

- ⚠️ arXiv:2305.15771 (Valmeekam et al.) — the ~12% autonomous-planning figure.
- ⚠️ PDF-level claims flagged inline in §2 and §3.
- **Venue reconnaissance remains unresolved.** DBLP is behind Anubis bot-checking (returns
  HTTP 200 with a challenge page — do not trust a 200 from `dblp.org`). K-CAP's standing, and
  ISWC's in-use track criteria, are still unchecked. The **HCI first-person methodology**
  question was resolved separately and favourably: Lucero, *A Sample of One* (DIS 2019, 114
  citations) plus *Longitudinal First-Person HCI Research Methods* (2021) — see `README.md` §6.
- Semantic Scholar's API works via Fetcher but rate-limits (HTTP 429) when called directly.
