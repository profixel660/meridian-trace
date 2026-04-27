# CONTEXT.md Review

## 1. Stress-testing locked decisions

### Trade vs Service
- Assumes every deliverable fits cleanly into (trade, service)
- Many deliverables are process-driven, contractual, or meta-level
- Recommendation: allow null values and reduce rigidity

### Excel as output
- Conflict between database-backed system and static export
- Users will treat Excel as editable source of truth
- Recommendation: decide if Excel is export-only or round-trip

### Human-in-the-loop
- High-friction QA vs non-technical users
- Users will likely bypass review
- Recommendation: choose strict gating or fast review, not both

### BYO API keys
- Reduces central dependency but increases support complexity
- Recommendation: limit providers initially

### Reproducibility
- Model + prompt is insufficient
- Missing parameters and input logging
- Recommendation: store full inference context

---

## 2. Gaps

- No document revision authority rules
- No formal definition of “deliverable”
- No granularity control
- No multi-document context strategy
- No performance expectations
- Weak definition of responsibility boundaries

---

## 3. Contradictions

- Flexible taxonomy vs rigid schema
- Non-technical UX vs deep configurability
- Interactive pauses vs batch automation

---

## 4. Flexibility Principle risks

- Hard-coded source reference formats
- Assumed extraction pipeline
- Excel-centric thinking

---

## 5. Open items critique

- Naming and branding are not critical
- Category axis is actually a schema question
- Multi-tag handling should be multi-row, not comma-separated
- Workspace model is critical and should not be deferred
- Sample documents will likely change assumptions

---

## Final priorities

1. Define “deliverable”
2. Decide Excel’s role
3. Define document versioning rules
