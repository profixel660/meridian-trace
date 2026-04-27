# Project state report — syd2-shell-cd

Snapshot of the current project SQLite. Generated automatically; not edited by hand.
Open the matching Excel at `data/projects/syd2-shell-cd-master.xlsx` for the spreadsheet view.

## 1. Counts

| Metric | Value |
|---|---:|
| Sources imported | 3 |
| Sources scanned | 3 |
| Deliverables (all) | 323 |
| Deliverables (master register) | 242 |
| Deliverables auto_approved | 242 |
| Deliverables quarantined | 81 |
| Audit (OUTSIDE) rows | 46 |
| HITL questions (pending) | 16 |
| Conflicts (pending) | 11 |
| Extraction jobs | 5 |
| LLM calls | 68 |
| LLM cost recorded (cents) | 414 |

## 2. Sources

| Filename | Class | State | Path | Demarc? | Template? |
|---|---|---|---|:-:|:-:|
| `AT-GLOBAL-OR-000103_SPC-ACC-01[11].pdf` | global_ose_spec | - | text_spec |  |  |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | customer_requirements | - | bod_import |  |  |
| `AT-GLOBAL-OR-000303_SCH-ACC-01_Chiller_Demarcation.pdf` | demarcation_schedule | - | excluded | Y |  |

## 3. Trade distribution (deliverables)

| Trade | Source | Count |
|---|---|---:|
| Mechanical | default | 106 |
| Chiller Vendor | default | 87 |
| Electrical | default | 57 |
| DCS | default | 17 |
| Carpentry | default | 15 |
| Fire | default | 11 |
| Telecommunications | default | 10 |
| Concrete | default | 8 |
| Busway Vendor | default | 4 |
| Generator Vendor | default | 3 |
| General Contractor / Principal | default | 2 |
| Hydraulic | default | 2 |
| Formwork | default | 1 |

## 4. Service distribution (deliverables)

| Service | Count |
|---|---:|
| Chiller System | 143 |
| Power distribution | 62 |
| HVAC | 53 |
| (null) | 24 |
| DCS / Controls | 16 |
| Fire detection & suppression | 11 |
| Comms/ICT | 10 |
| Lighting | 2 |
| Hydraulics | 2 |

## 5. Flag frequency

| Flag | Count |
|---|---:|
| `negotiated_response` | 67 |
| `conflicts_with_source_<id>` | 23 |
| `definition_borderline` | 13 |
| `scope_shifted_to_nrc` | 7 |
| `trade_inferred` | 2 |
| `taxonomy_new_value_proposed` | 1 |

## 6. Sample deliverables — one per trade

| Trade | Service | Cat | Conf | Summary |
|---|---|---|---|---|
| Busway Vendor | Power distribution | delivery | medium | Spec203 rack power circuits via 4N3 aisle busduct with 400A TOBs in 8:6 arrangement for high-density racks and IEC 60309 circuits for low-density racks, with 80% derating on tap-off nameplate ⚠ |
| Carpentry | - | delivery | medium | Spec203 data hall layout with compliant cold/hot aisle clearances and MIMO aisle width; perimeter clearance 3,048 mm on 3 sides and minimum 2,150 mm on 1 side per submitted rack layouts ⚠ |
| Chiller Vendor | Chiller System | delivery | high | Verification that flow meters are installed and tested; flushing and passivation not through equipment |
| Concrete | - | delivery | high | Finished floor elevation above 100-year and 500-year floodplain |
| DCS | DCS / Controls | delivery | medium | CDU internal pressure sensors for pump control and branch pipework differential pressure sensors for monitoring, with TWL differential pressure alarming at 25 PSI (alarm threshold subject to reconfigu |
| Electrical | Power distribution | delivery | high | 4x9s availability electrical distribution to all IT racks with concurrent maintainability via multiple power paths to dual/multi-corded equipment |
| Fire | Fire detection & suppression | delivery | medium | Heat detectors and fire extinguishers in generator room/enclosure per local code compliance ⚠ |
| Formwork | - | builders_works | high | Formwork for chiller support plinths meeting structural and seismic requirements |
| General Contractor / Principal | Chiller System | design | high | Incorporation of Chiller System into overall data centre design including spatial, technical, loading and T&C coordination |
| Generator Vendor | Power distribution | delivery | medium | Standby generators with ISO 8528 G2 transient compliance, isochronous governors, true RMS voltage regulators, and AREP+PMI or PMG excitation system ⚠ |
| Hydraulic | Hydraulics | delivery | high | Drain pipework connection from nearest drain point to chiller drain outlet |
| Mechanical | Chiller System | design | high | Approval of manufacturing drawings confirming compliance with project specifications |
| Telecommunications | Comms/ICT | delivery | medium | Spec108 data hall vertical clearance above racks: minimum 1 m horizontal from cold aisle, 300 mm above each cable tray, and 1.8 m above maximum rack height — compliance pending design development ⚠ |

## 7. Conflicts surfaced for HITL

### Conflict `43397215…` — kind: **cross_source_content**

**Most onerous:** NULL — _not directly comparable_

**Reasoning:** Not directly comparable: the Demarcation Schedule constrains the protocol (ModBus RTU) while the OSE spec constrains the physical medium and termination point (RS485 cables to CMS/CMCS); one addresses software/protocol, the other addresses physical cabling scope and trade responsibility.

**Parties:**
- _deliverable_ — Global OSE spec requires RS485 cables from chiller HLI to CMS/CMCS system for DCS interface, specifying the physical cable layer as a separate deliverable by the DCS trade.
- _deliverable_ — Demarcation Schedule requires DCS interface via ModBus RTU protocol high-level interface with confirmation of control strategy.

### Conflict `929386db…` — kind: **cross_source_content**

**Most onerous:** identified

**Reasoning:** The customer requirements document imposes the most onerous reading by adding a specific quantitative THDi <5% limit on top of the IEEE 519 standard reference, which is a stricter and more measurable obligation than either demarcation row.

**Parties:**
- _deliverable_ — Demarcation Schedule row 61 requires Line Reactor and RFI filters compliant with IEEE Std. 519-1992 or local equivalent (specific 1992 edition cited).
- _deliverable_ — Demarcation Schedule row 20 requires factory-mounted VFDs with RFI filters and harmonic suppression compliant with IEEE Std 519 or local equivalent (no edition specified).
- _deliverable_ — Customer requirements require ECMs/VFDs on mechanical equipment to limit reflected harmonics per IEEE 519 with THDi <5%, adding a quantitative threshold not present in the Demarcation Schedule.

### Conflict `0d2d26e3…` — kind: **cross_source_content**

**Most onerous:** identified

**Reasoning:** The customer requirements document is more onerous as it imposes a specific 7-minute restart performance obligation that is absent from the Demarcation Schedule, creating a tighter functional requirement on the Chiller Vendor.

**Parties:**
- _deliverable_ — Customer requirements specify chiller plant must have a quick-restart feature resuming operation and achieving design leaving chilled water temperature within 7 minutes.
- _deliverable_ — Demarcation Schedule requires design, supply, delivery and commissioning of complete Chiller System with no restart time performance criterion stated.

### Conflict `235eea19…` — kind: **cross_source_content**

**Most onerous:** NULL — _not directly comparable_

**Reasoning:** Not directly comparable: the Demarcation Schedule constrains efficiency via a certification scheme (Greenmark Platinum SGP) while the customer requirements constrain efficiency via specific PUE targets; these are different measurement frameworks and it cannot be determined which imposes the greater obligation on the chiller system without further analysis.

**Parties:**
- _deliverable_ — Customer requirements specify design peak PUE 1.43 and annualised PUE 1.33, validated in writing by Engineer of Record.
- _deliverable_ — Demarcation Schedule requires Chiller System energy efficiency compliant with Greenmark Platinum Requirements (SGP).

### Conflict `1c9485d1…` — kind: **cross_source_content**

**Most onerous:** NULL — _not directly comparable_

**Reasoning:** Not directly comparable: the OSE spec is more onerous on technical standard (adds seismic requirement) while the Demarcation Schedule is more onerous on scope detail (RAF support and cut-outs); additionally the trade allocation differs (Concrete/Formwork vs Mechanical/Chiller Vendor), making these not directly comparable on a single dimension.

**Parties:**
- _deliverable_ — Demarcation Schedule row 50 requires plinths including RAF support and cut-outs for Chiller System, allocated to Mechanical (and Chiller Vendor per row ef9dfd0e), with no seismic requirement stated.
- _deliverable_ — Global OSE spec Appendix B Item 6 requires chiller support plinths meeting structural and seismic requirements, allocated to Concrete and Formwork trades.

### Conflict `79a7bfe4…` — kind: **cross_source_content**

**Most onerous:** identified

**Reasoning:** The customer requirements document is more onerous as it imposes a specific measurable chilled water inlet temperature ceiling (≤22°C) with a defined exceedance allowance that is not carried through into the global OSE spec, meaning the spec as written does not fully implement this customer requirement.

**Parties:**
- _deliverable_ — Global OSE spec describes the chiller assembly and its performance requirements but does not state a specific chilled water supply temperature limit of ≤22°C.
- _deliverable_ — Customer requirements require technology water supply system maintaining inlet temperature ≤22°C (with allowance up to 26°C for up to 175 hours/year).

### Conflict `55f0ddbb…` — kind: **document_class**

**Most onerous:** identified

**Reasoning:** The customer requirements document is more onerous as it imposes a specific measurable restart time obligation (7 minutes) that the derived global OSE spec does not carry, meaning the spec as written does not fully implement the customer requirement.

**Parties:**
- _deliverable_ — Global OSE spec (SPC-ACC-01) describes the chiller assembly and its components but does not include a 7-minute restart performance criterion.
- _deliverable_ — Customer requirements (SYD29EX2) require chiller plant quick-restart achieving design LCHWT within 7 minutes.

### Conflict `8d53a0a5…` — kind: **responsibility**

**Most onerous:** NULL — _not directly comparable_

**Reasoning:** Not directly comparable: the two sources allocate the same scope to different trades (Concrete/Formwork vs Mechanical/Chiller Vendor); the Demarcation Schedule is the primary reference for trade allocation per §5 but the conflict must be surfaced for HITL reconciliation.

**Parties:**
- _deliverable_ — Demarcation Schedule assigns plinths including RAF support and cut-outs to Mechanical trade.
- _deliverable_ — Global OSE spec assigns chiller support plinths to Concrete trade (and Formwork per f2fdc320).

### Conflict `7303ca0a…` — kind: **scope_demarcation**

**Most onerous:** identified

**Reasoning:** The OSE spec explicitly assigns this scope to Mechanical, creating an obligation that is not corroborated or demarcated in the Demarcation Schedule, leaving the responsibility boundary unresolved and potentially creating a gap or duplication.

**Parties:**
- _deliverable_ — Global OSE spec Appendix B Item 2 assigns final chilled water pipework connection from termination valve to chiller/chilled water pump to the Mechanical trade.
- _deliverable_ — Demarcation Schedule covers the complete Chiller System but does not explicitly list the final CHW pipework connection from termination valve as a distinct demarcated scope item.

### Conflict `1e18182a…` — kind: **scope_demarcation**

**Most onerous:** identified

**Reasoning:** The OSE spec creates an explicit Electrical trade obligation for this cabling scope that is not reflected in the Demarcation Schedule, leaving a potential responsibility gap that the more onerous reading (OSE spec) would require the Electrical trade to close.

**Parties:**
- _deliverable_ — Global OSE spec Appendix B Item 3 assigns power supply cabling from main switchboard to each local chiller isolator to the Electrical trade.
- _deliverable_ — Demarcation Schedule does not list power supply cabling from main switchboard to chiller isolator as a demarcated scope item for any party.

### Conflict `c5d14bda…` — kind: **scope_demarcation**

**Most onerous:** identified

**Reasoning:** The OSE spec creates explicit Hydraulic trade obligations for cold water supply and drain connections that are absent from the Demarcation Schedule, imposing obligations not captured in the primary demarcation reference.

**Parties:**
- _deliverable_ — Global OSE spec Appendix B Item 4 assigns domestic cold water supply extension from termination valve to each chiller to the Hydraulic trade.
- _deliverable_ — Demarcation Schedule does not list domestic cold water supply or drain pipework to chillers as demarcated scope items.

## 8. Pending HITL questions

### other

**Context:** Page 8 item (w) references 'DCS AT-GLOBAL-TR000015b CMS Points List' as a deliverable document to be provided. It is unclear whether this is a vendor-supplied document (chiller vendor provides a populated points list) or a separate DCS/CMS vendor document. The Appendix D DCS Schedule has been extracted as a chiller vendor deliverable (the minimum points list embedded in this spec), but the full CMS Points List document may be a separate DCS trade deliverable.

**Question:** Is the 'AT-GLOBAL-TR000015b CMS Points List' document a deliverable to be provided by the Chiller Vendor (as a populated submittal), by the DCS/CMS vendor, or by both? This affects trade attribution and whether a separate row is needed.

### other

**Context:** Page 11 references a waterside economiser package (Option) with piping connections, balancing valves, and control logic. It is unclear from this global spec whether the waterside economiser is always vendor-integrated within the chiller footprint or is a separately supplied package by a different vendor or trade.

**Question:** Is the waterside economiser package always supplied as an integrated part of the chiller by the Chiller Vendor, or can it be a separately procured item from a different vendor or trade? This affects whether it should be attributed to Chiller Vendor or Mechanical trade.

### other

**Context:** Page 26 references an 'active harmonic filter (AHF)' as a conditional item: where vendors cannot achieve <5% THDi at component level, they shall provide pricing and specification for an AHF, and if the AHF has a different communication protocol, a converter card shall be included. This appears to be a conditional sub-option within the chiller vendor scope.

**Question:** Should the active harmonic filter (AHF) and associated converter card be extracted as a separate borderline deliverable row (conditional on THDi non-compliance), or does it roll up into the chiller assembly deliverable?

### other

**Context:** Disc Section 'DCE Civil, Structural & Architectural (CSA)' does not appear in the service-mapping table provided. Rows under this section have been assigned service=null. The section covers civil, structural, and architectural works.

**Question:** Please confirm the correct service mapping for 'DCE Civil, Structural & Architectural (CSA)'. Should it map to a specific service (e.g., 'Structural', 'Architectural'), or remain null? Also confirm whether the default trade for these rows should be 'Concrete', 'Carpentry', or a combination depending on the specific requirement.

### other

**Context:** Disc Section 'DCE Telecom' and 'DCE IDF Telecom' / 'DCE IDF' appear in the source but are not explicitly listed in the service-mapping table. They have been mapped to 'Comms/ICT' and trade 'Telecommunications' as the closest match.

**Question:** Please confirm that 'DCE Telecom', 'DCE IDF Telecom', and 'DCE IDF' should all map to service='Comms/ICT' and trade='Telecommunications'. If 'DCE IDF Mechanical' and 'DCE IDF Electrical' should map to different services/trades (e.g., HVAC/Mechanical and Power distribution/Electrical respectively), please advise.

### other

**Context:** Disc Section 'DCE Fire Protection System' appears in the source. It has been mapped to service='Fire detection & suppression' and trade='Fire' as the closest match, but this mapping is not explicitly listed in the service-mapping table.

**Question:** Please confirm that 'DCE Fire Protection System' should map to service='Fire detection & suppression' and trade='Fire'.

### other

**Context:** Disc Section 'DCE Electrical Power Monitoring Systems (EPMS)' appears in the source. It has been mapped to service='Power distribution' and trade='Electrical' as the closest match, but this mapping is not explicitly listed in the service-mapping table.

**Question:** Please confirm that 'DCE Electrical Power Monitoring Systems (EPMS)' should map to service='Power distribution' and trade='Electrical'. Alternatively, should a new service value such as 'Electrical power monitoring' be proposed?

### other

**Context:** Disc Section 'Operations' appears in the source for rows 2.10.0.1 and 2.10.1.1 covering dedicated office and storage space. This section does not appear in the service-mapping table.

**Question:** Please confirm the correct service mapping for 'Operations' (covering dedicated office and storage space requirements). Should it map to null, or to a specific service? Also confirm the appropriate trade — these rows have been assigned trade='Carpentry' as the fit-out trade most likely responsible for office/storage fitout.

### other

**Context:** Row 3.3.1.1 appears twice in the source — once under 'DCE Electrical Engineering (EE)' (MV/LV cables) and once under 'DCE Electrical Power Monitoring Systems (EPMS)' (electrical monitoring devices on UPS). Both have been extracted as separate deliverables with the same Req Id.

**Question:** Please confirm whether Req Id 3.3.1.1 is a duplicate Req Id in the source (i.e., two different requirements sharing the same ID), and advise whether the source_ref should be disambiguated differently (e.g., by Disc Section suffix).

### other

**Context:** Row 2.1.34.1 (PUE) has Landlord Response 'Not Comply' with a substantive qualifier providing specific PUE values (Peak 1.43, Annualised 1.33) rather than committing to a formal EOR-validated written report as required by the tenant. The requirement asks for a written validation by the EOR.

**Question:** Should row 2.1.34.1 (PUE validation) be retained as a deliverable (EOR-validated PUE report) given the Landlord has provided values but not explicitly committed to a formal written EOR validation? Please confirm whether a formal PUE validation report is expected as a project deliverable.

### other

**Context:** Row 3.3.24.1 (Tenant initiated load shed capability) has Landlord Response 'Not Comply' with a substantive qualifier stating that remote tripping of MV switchgear will not be available, but SCADA remote open/close from control room HMI is provided. The requirement asks for raceway, cabling, and termination provisions for a tenant RTAC.

**Question:** Should row 3.3.24.1 be retained as a deliverable (MV SCADA system with remote HMI) given the tenant's specific requirement for RTAC remote trip capability is not being met? Please confirm whether the SCADA/HMI system itself constitutes a deliverable in scope, or whether this row should be rejected as non-compliant.

### other

**Context:** 

**Question:** Row 21 covers 'Co-ordination of DCS interface provisions' and 'Confirmation of the control strategy'. The Supplier (Chiller Vendor) is marked responsible. Should the DCS contractor also be extracted as a separate row given the explicit DCS interface scope, or is this solely a Chiller Vendor deliverable?

### other

**Context:** 

**Question:** Row 81 references commissioning documentation (L1-L3). The scope item references a commissioning process output. Please confirm whether the completed commissioning documentation package (as a handover record) should be treated as INSIDE (operational documentation) or whether it should be audited as a commissioning process activity.

### other

**Context:** 

**Question:** Row 9 states 'Consultant and general contractor shall be held accountable to ensure suffice design is complete.' Should the General Contractor / Principal be extracted as an additional responsible party row for the acoustic treatment scope item, or is this accountability note not a direct allocation?

### other

**Context:** 

**Question:** Row 25 'Supply & install of Chilled Water flow DP switches' shows only the Contractor (Mechanical) as responsible with no Supplier tick. Please confirm whether the Chiller Vendor should also be included or whether this is solely a Mechanical Contractor scope item.

### other

**Context:** 

**Question:** Row 74 'Removal of any commissioning spades in the Chilled water system' — only the Chiller Vendor (Supplier) is marked responsible. Please confirm whether the Mechanical Contractor should also be included given the physical nature of the work.

## 9. Audit sample (rejected OUTSIDE — 10 of 46)

| Source | Candidate | Rejection reason |
|---|---|---|
| `AT-GLOBAL-OR-000303_SCH-ACC-01_Chill` | Setup program of delivery dates to meet Contractor's construction program. | A delivery programme is explicitly excluded as a process document and does not form part of the completed building, constitute physical works, or carry continui |
| `AT-GLOBAL-OR-000303_SCH-ACC-01_Chill` | Provide supervisory and technical support for Chiller System installation. | Supervisory and technical support during installation is a service/attendance activity, not a physical work or documentation deliverable, and fails the §3 defin |
| `AT-GLOBAL-OR-000303_SCH-ACC-01_Chill` | Documentation works using online Automated Commissioning Management System (as a | Documentation of commissioning activities via a management system is a process/workflow activity and does not constitute documentation with continuing operation |
| `AT-GLOBAL-OR-000303_SCH-ACC-01_Chill` | Provide training to the operations personnel. | Training services are explicitly excluded as they are attendance/service activities and do not form part of the completed building, physical works, or operation |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | Facility Name: SYD2 | §3 gate: metadata/administrative field identifying the facility name; not a deliverable under §3(a), (b), or (c). |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | Address Line 1: 1 Sirius Road | §3 gate: administrative address metadata; not a deliverable under §3(a), (b), or (c). |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | Address Line 2: NA | §3 gate: administrative address metadata; not a deliverable under §3(a), (b), or (c). |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | City: Lane Cove | §3 gate: administrative address metadata; not a deliverable under §3(a), (b), or (c). |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | State/Province/Region: New South Wales | §3 gate: administrative address metadata; not a deliverable under §3(a), (b), or (c). |
| `SYD2CD_(SYD29EX2)_-_AirTrunk.xlsx` | Zip: 2066 | §3 gate: administrative address metadata; not a deliverable under §3(a), (b), or (c). |

## 10. LLM cost actuals

| Purpose | Model | Calls | Cost (cents) | Cost (USD) |
|---|---|---:|---:|---:|
| extract_bod | claude-sonnet-4-6 | 4 | 271 | $2.71 |
| extract_text_spec | claude-sonnet-4-6 | 4 | 83 | $0.83 |
| conflict_pass | claude-sonnet-4-6 | 2 | 52 | $0.52 |
| quality_scan | claude-sonnet-4-6 | 7 | 8 | $0.08 |
| triage | claude-haiku-4-5-20251001 | 51 | 0 | $0.00 |
| **TOTAL** | | | **414** | **$4.14** |

## 11. Schema migrations applied

| Version | Applied at |
|---:|---|
| 1 | 2026-04-26T11:54:31Z |
| 2 | 2026-04-26T15:10:32Z |

---

_End of report._