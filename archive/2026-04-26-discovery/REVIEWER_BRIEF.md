# Reviewer Brief — for use when sharing CONTEXT.md

I'm in the discovery / scoping phase of a tool I want to build. The attached **CONTEXT.md** is the consolidated output of that phase — a record of policy and architecture decisions, plus a *Flexibility Principle* that constrains all later design choices.

**It is not a build-ready spec.** I am not asking you to design the implementation, scope an MVP, draft a data model, or write code. I am asking you to **harden the current context** before I take it forward.

Specifically, I want you to:

1. **Stress-test the locked decisions** — where am I taking on risk I haven't acknowledged? Where is a "lock" actually a hidden assumption that should be re-opened?
2. **Find the gaps** — what's missing that should be discussed at this stage, before implementation work begins?
3. **Challenge contradictions** — anywhere two decisions don't sit comfortably together.
4. **Pressure-test the Flexibility Principle** — anywhere I've baked in today's assumptions in a way that won't survive future requirements.
5. **Critique the six open items** at the end — am I framing them correctly, or have I missed the real fork?

Two pieces of context I haven't captured in the document:

- I have not yet ingested sample project documents. The brief is deliberately designed for the unknown.
- This is a Proof of Concept being built solo, not a funded product engineering effort. Recommendations should respect that scale.

**Read CONTEXT.md in full before responding.** Then come back with your sharpest critique — not a list of compliments. I would rather know now where this brief is weak than discover it during the build.
