# Sample Contract and Demo Questions

Reference for `sample_contract.txt`, the test document used to exercise the multi-agent
contract analyzer. It documents what is deliberately wrong with the contract, which agent
is expected to catch each issue, and the questions that produce the most useful demo.

## The contract file

`sample_contract.txt` — Master Professional Services Agreement between Acme Technologies Inc.
(Client) and Horizon Consulting Group LLC (Service Provider), for a cloud migration and data
platform modernization engagement.

| | |
|---|---|
| Companion | `sample_questions.txt` — questions to ask about it |
| Length | 19,130 characters / 229 lines |
| Term | 24-month initial term, auto-renewing 12-month periods |
| Value | $85,000/month (~$2.04M initial term) plus uncapped expenses |
| Governing law | New York, binding AAA arbitration, jury and class waiver |

### Article map

| Article | Subject | Sections |
|---|---|---|
| 1 | Services and Term | Scope, term and renewal, service levels, milestones |
| 2 | Fees and Payment | Monthly fee, late interest, expenses, escalation, disputes |
| 3 | Confidentiality | Definition, exclusions, 5-year survival |
| 4 | Data Protection and Security | Client Data, PII processing, cross-border transfer, safeguards, incidents, analytics reuse, deletion |
| 5 | Intellectual Property | Deliverables assignment, Provider Materials, license, feedback |
| 6 | Personnel and Subcontractors | Contractor status, subcontracting, key personnel, non-solicitation |
| 7 | Indemnification | Provider indemnity, Client indemnity, procedure |
| 8 | Limitation of Liability | Cap, consequential damages exclusion, exceptions, basis of bargain |
| 9 | Insurance | Required coverage, certificates |
| 10 | Termination | Convenience, early termination fee, cause, non-payment, change of control, effects, survival |
| 11 | Dispute Resolution | Governing law, escalation, arbitration |
| 12 | General Provisions | Assignment, force majeure, entire agreement, amendments, severability, notices |

## Deliberately planted issues

The contract is drafted so that problems are **present but defective** rather than simply
absent. Missing clauses give the agents nothing to reason about; defective ones produce
specific, citable findings.

| # | Issue | Clause | Expected agent |
|---|---|---|---|
| 1 | Indemnity is carved out of the liability cap, making it effectively uncapped | 8.3(a) vs 8.1 | Risk |
| 2 | Data breach is also carved out of the cap | 8.3(d) | Risk |
| 3 | Breach notice is "commercially reasonable," not GDPR's 72 hours | 4.5 | Compliance |
| 4 | EU/UK/California PII transferred to India with no DPA or SCCs | 4.2, 4.3 | Compliance |
| 5 | Provider keeps de-identified Client Data in perpetuity for its own products | 4.6 | Compliance / Risk |
| 6 | Pass-through expenses uncapped and exempt from pre-approval | 2.3 | Risk |
| 7 | Evergreen auto-renewal behind a 120-day notice window | 1.2 | Clause / Risk |
| 8 | Early termination fee of 50% of remaining fees, non-reciprocal | 10.2 | Risk |
| 9 | Subcontracting to anyone without Client consent | 6.2 | Risk |
| 10 | Non-solicitation penalty of 100% of annual compensation | 6.4 | Compliance (enforceability) |
| 11 | $2M insurance against unlimited indemnity exposure | 9.1 vs 8.3 | Risk |
| 12 | IP assignment conditioned on payment; Provider retains all tooling under a revocable license | 5.1–5.3 | Clause / Risk |
| 13 | Service credits capped at 5% of monthly fee as sole remedy | 1.3 | Risk |
| 14 | Fee escalation at the greater of 5% or CPI, with no ceiling | 2.4 | Risk |
| 15 | Liquidated damages of $50K/month for milestone delay | 1.4 | Risk (enforceability) |

Also absent by design, and typically surfaced as gaps: audit rights, SOC 2 or equivalent
security attestation, business continuity and disaster recovery obligations, and
non-discrimination / EEO provisions.

The combination of issues 1, 2 and 11 is the sharpest finding: the agreement advertises a
$1.02M liability ceiling while the carve-outs remove that ceiling for the two most likely
loss scenarios, backed by only $2M of insurance.

## Demo questions

`sample_questions.txt` holds a copy-paste list grouped by theme, including negotiation and
follow-up prompts. The table below is the subset with the clearest mapping to the planted
issues, for when you want a predictable result.

| Question | Exercises | Expected findings |
|---|---|---|
| What are the key risks in the termination, indemnification, and liability provisions? | All three analysis agents | Issues 1, 2, 8, 11 |
| Is our liability actually capped under this agreement? | Risk | The 8.3 carve-out contradicting 8.1 |
| Are we GDPR and CCPA compliant if we sign this? | Compliance | Issues 3, 4, 5 |
| What happens to our data if we terminate? | Compliance, Clause | 4.6, 4.7, 10.6 |
| Who owns the deliverables and the tooling? | Clause | Issue 12 |
| What are our total costs over the full term? | Clause | Issues 6, 14 — fee plus uncapped expenses and escalation |
| How easily can we exit this contract? | Risk | Issues 7, 8 — auto-renewal plus 50% exit fee |
| Should we approve this contract? | Response agent decision path | Approve / Review / Reject with reasoning |

Omitting the question entirely triggers the response agent's full executive-summary format
instead of a targeted answer.

## Expected output

With the planted issues, the risk agent scores this contract around **72/100**. That crosses
the risk threshold, so `threshold_analysis` reports `exceeds_threshold: true` and
`requires_legal_review: true`, and the response agent's recommendation is `REVIEW` rather
than `Approve`.

The threshold defaults to `0.7` and is read from the request payload's `threshold` field, so
it can be overridden per request. The orchestrating Lambda does not currently send it, so
0.7 applies in practice. (`RISK_THRESHOLD` in the legacy `config.py` is not used by the
deployed agents — see the note on dead code below.)

A contract that scores as low risk makes for a weaker demo, so if you edit the document,
check that the score stays above the threshold.

## Running it

Invoke through the orchestrating Lambda, which fans out to the clause, risk, and compliance
agents in parallel and then passes their results to the response agent for synthesis:

Build the request payload (run from this directory), then invoke:

```bash
python3 -c "import json;c=open('sample_contract.txt').read();q='What are the key risks in the termination, indemnification, and liability provisions?';open('/tmp/payload.json','w').write(json.dumps({'body':json.dumps({'contract_text':c,'user_question':q})}))"

aws lambda invoke \
  --function-name legal-contract-api \
  --cli-binary-format raw-in-base64-out \
  --payload file:///tmp/payload.json \
  --region us-east-1 \
  /tmp/response.json
```

Read the report out of the response:

```bash
python3 -c "import json;d=json.load(open('/tmp/response.json'));b=json.loads(d['body']);print(b['agent_results']);print(b['contract_analysis'].get('summary_response',''))"
```

The `agent_results` field reports per-agent status, which is the quickest way to confirm all
four agents ran rather than silently degrading to a partial answer.

Agent runtime ARNs are read from the Lambda's `RESPONSE_AGENT_ARN` and `ALL_AGENT_ARNS`
environment variables, so they do not need to be supplied per request.

## Timing and the 29-second budget

Measured warm, with this document:

| Stage | Elapsed |
|---|---|
| Clause, risk, compliance (parallel) | 7–11s |
| Response agent synthesis | +9–11s |
| **Total** | **19–22s** |

API Gateway REST APIs enforce a hard 29-second integration timeout that cannot be raised,
which leaves roughly 7–10 seconds of headroom. Two consequences:

- **Warm up before presenting.** Fire one throwaway invocation; a cold container start will
  distort the first result and can exceed the budget.
- **Growing this document eats the headroom.** Latency is bound by generation rather than
  input size — doubling the contract added only ~4s — but the margin is not large.

Per-agent output is capped to hold this budget. Each cap has a code default and can be
overridden by an environment variable:

| Variable | Default | Agent |
|---|---|---|
| `CLAUSE_AGENT_MAX_TOKENS` | 1800 | Clause |
| `CLAUSE_AGENT_MAX_CLAUSES` | 10 | Clause |
| `RISK_AGENT_MAX_TOKENS` | 1500 | Risk |
| `RISK_AGENT_MAX_RISKS` | 5 | Risk |
| `COMPLIANCE_AGENT_MAX_TOKENS` | 1500 | Compliance |
| `RESPONSE_AGENT_MAX_TOKENS` | 2000 | Response |

Raising them improves depth at the cost of latency. To change one, add it to the agent's
`envVars` in `agentcore/agentcore.json` and run `agentcore deploy` — these are runtime
environment variables, so a redeploy is required.

If you need more time than 29 seconds, Quick Suite's MCP connector allows 60 seconds
(fixed, not configurable, and without streaming or partial updates).

## Note on dead code

The agents that actually run live in `app/<agent_name>/main.py` and are deployed via
`agentcore deploy`. The older `agents/` directory and the root `config.py` predate the
migration to `agentcore-cli` and are no longer deployed or referenced. Editing them has no
effect on behavior.
