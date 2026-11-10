1. Project objective
    - Build an AI-assisted  email triage service for Larkfield Home, a US online furniture and home goods retailer.
    - The service receives customer support emails and return a structured decision.
    - The service must use the supplied customer, order, shipment, refund, ticket and policy data.
    - The system proposes actions only. It must never actually issue refunds, send emails or modify customer orders.

2. API contract
Document these exact endpoints:
    METHOD         Endpoint                 Purpose
    GET            /health                  Return {"status":"ok"}
    POST           /tickets                 Process one incoming ticket
    GET            /runs/{run_id}           Retrieve the current run, including pending human approval
    POST           /runs/{run_id}/decision  Approve or reject a proposed action
    GET            /ledger                  Retrieve recorded actions

3. Request format
    - POST /tickets accepts one ticket.
    - Ticket structure must match the supplied inbox/public_tickets.json examples and relevant schema.
    - The service must support additional unseen tickets in the same format.
    - The decision endpoint accepts:
        - decision: approve or reject
        - approver: string identifying the approver.

4. Response contract
Use the supplied schema/response_schema.py as the source of truth for Pydantic models.

Record these required run fields:
- ticket_id
- run_id
- ticket_type
- resolved
- outcome
- reason_codes
- checks
- human_approval_required
- approver_role
- draft_reply
- trace

The documented ticket types are REFUND, RETURN, ORDER_STATUS, DELIVERY_ISSUE, and OTHER.

The documented outcomes are PROCEED, PROCEED_MODIFIED, PEND, ESCALATE, REJECT, INVALID_REQUEST, and UNABLE_TO_VERIFY. 

5. Checks and evidence
    - Each check must contain check_id, result and policy_clause.
    - Check results must be one of PASS, FAIL, NOT_APPLICABLE or UNABLE_TO_VERIFY.
    - Evidence may include source, reference and value.
    - A check that cannot be completed must never be marked PASS.

6. Human approval and action ledger
    - The service must propose actions and wait for human approval where required.
    - Approver roles: SUPPORT_LEAD, TRUST_SAFETY, CARRIER_CLAIMS, or null.
    - The ledger must expose run_id, ticket_id, action, amount_usd, status and recorded_at.
    - Action types: REFUND, REPLY, ESCALATION.
    - Status values: PROPOSED, APPROVED, REJECTED.
    - Repeated approval decisions must not create duplicate financial actions.

7. Data and policy requirements
    - Use the supplied data pack and preserve original source files.
    - Use the supplied policy documents to determine business rules.
    - Use the data dictionary to understand the datasets.
    - Do not invent policy rules when the source documents are silent.
    - Document assumptions and policy ambiguities in APPROACH.md or ARTEFACT.md.

8. Trace and cost
Every response must include a trace containing:
    - Model calls, model name and input/output token counts.
    - Tools called.
    - Documents used and their versions.
    - Measured cost_usd. 

9. Evaluation
The solution must:
    - Process the supplied public tickets and unseen tickets.
    - Validate responses against the supplied schema.
    - Include an evaluation of decisions and policy compliance.
    - Record actual evaluation results and measurements in ARTEFACT.md.
    - Explain unfinished functionality honestly.

10. Deployment and deliverables
    - Expose a public HTTP service without authentication.
    - Read the listening port from the PORT environment variable.
    - Keep the endpoint reachable until the assessment review ends.
    - Provide a working HTTPS endpoint.
    - Include source code, README.md, APPROACH.md and ARTEFACT.md.
    - Publish the project in a public GitHub repository.
    - Submit the repository URL and deployed endpoint URL on the assessment dashboard.

11. Entry point and assumptions
    - Follow the starter template and dashboard brief for the application entry point.
    - Document any requirements that are not explicitly specified as assumptions.
    - The dashboard brief takes precedence over examples in the execution guide.