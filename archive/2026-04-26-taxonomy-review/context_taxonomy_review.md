# CONTEXT.md v2 — Focused Review (Taxonomy & Deliverable Definition)

## 1. Locked Deliverable Definition (Final)

**Definition:**
A deliverable is a system, component of a system, building component, or other physical or logical item that forms part of the completed structure or its operational services.

**Explicit Exclusions:**
Process, contractual, administrative, or project-management artefacts that do not form part of the completed building or its operational services, including:
- RFIs
- Submittals
- Programmes
- ITPs
- Warranties
- Reports
- Meeting actions
- General coordination tasks

---

## 2. Taxonomy Model — Confirmed Structure

### Schema (fixed)
- `trade` (nullable)
- `service` (nullable)
- `category` (nullable)

### Taxonomy (flexible, data-driven)
- Values are extensible per project
- Not hard-coded
- Governed via user approval and normalisation

---

## 3. Key Clarifications to Add to CONTEXT.md

### 3.1 Deliverable Scope Boundary

Add explicit statement:

> Deliverables are limited to items that become part of the completed building or its operational systems. The tool does not extract general project management or contractual artefacts.

---

### 3.2 Role of Category Axis

Clarify:

> Category is a lightweight semantic axis used to classify cross-cutting or non-system-specific building items. It is secondary to trade and service and should not be relied upon as the primary classification dimension.

---

### 3.3 Null Handling (Intentional)

Add:

> `trade` and `service` may be null where a deliverable cannot be meaningfully attributed to a specific trade or service. This is expected behaviour, not an error condition.

---

### 3.4 Builders Works Positioning

Clarify:

> Builders works are included where they result in physical modifications or components that form part of the completed building. These are typically assigned to `trade = General Contractor / Principal` and `category = builders_works`.

---

## 4. Remaining Taxonomy Risks (To Monitor)

### 4.1 Category Overload

Risk:
- Category mixes lifecycle, responsibility, and activity types

Mitigation (v1):
- Accept coarse grouping
- Avoid expanding category aggressively

Future (v1.x):
- Consider splitting into multiple dimensions if needed

---

### 4.2 Taxonomy Drift

Risk:
- Duplicate or inconsistent values (e.g. "Electrical" vs "Electrical Contractor")

Required control:
- Canonical value enforcement
- Synonym merging via user confirmation
- Prefer reuse over creation

---

### 4.3 Trade Semantics (GC Role)

Clarify in doc:

> "General Contractor / Principal" is included to capture ownership of cross-cutting and builders-works items that do not belong to specialist trades.

---

## 5. Net Effect of These Decisions

- Trade/service remain strong, high-signal dimensions
- Category becomes supportive, not dominant
- Dataset becomes cleaner and more pivot-friendly
- Extraction task becomes more deterministic for the LLM
- Reduced noise from non-building artefacts

---

## 6. Action Summary

You should update CONTEXT.md to:

1. Lock the refined deliverable definition and exclusions
2. Add explicit boundary statement (what is NOT a deliverable)
3. Clarify category role (secondary axis)
4. Document intentional null handling
5. Add taxonomy governance (normalisation + synonym control)
6. Explicitly define GC role in trade taxonomy

---

This locks the taxonomy model into a stable, scalable state for implementation.
