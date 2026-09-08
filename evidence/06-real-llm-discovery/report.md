# Run report — b8ec4e9e701a42c7819af74748564a7b

- **Mode:** discovery
- **Goal:** look up member 34567 and read their current savings balance
- **Target:** http://host.docker.internal:8799/search
- **Status:** completed (goal achieved)
- **Steps:** 11
- **Artifact:** 3a1d8ac5a4544d07b2a5c1adf3433430 v2

## Timeline

- **run_started**
- **sandbox_started**
- [0] **decision** tool=type
  - _Enter the member ID to search for._
- [0] **guardrail** verdict=allow
- [0] **action** action_type=type
- [1] **decision** tool=click
  - _The search field already contains 34567; submit the search form._
- [1] **guardrail** verdict=allow
- [1] **action** action_type=click
- [2] **decision** tool=click
  - _Open the member record for 34567 to view their savings balance._
- [2] **guardrail** verdict=allow
- [2] **action** action_type=click
- [3] **decision** tool=extract
  - _Read the savings balance value shown on the member page._
- [3] **guardrail** verdict=allow
- [3] **action** action_type=extract
- [4] **decision** tool=extract
  - _Read the savings balance value shown next to the Savings label._
- [4] **guardrail** verdict=allow
- [4] **action** action_type=extract
- [5] **decision** tool=extract
  - _Read the savings balance value shown next to the Savings label._
- [5] **guardrail** verdict=allow
- [5] **action** action_type=extract
- [6] **decision** tool=extract
  - _Read the savings balance value shown next to the Savings label._
- [6] **guardrail** verdict=allow
- [6] **action** action_type=extract
- [7] **decision** tool=extract
  - _Read the savings balance value shown next to the Savings label._
- [7] **guardrail** verdict=allow
- [7] **action** action_type=extract
- [8] **decision** tool=extract
  - _Read the savings balance value shown on the member page._
- [8] **guardrail** verdict=allow
- [8] **action** action_type=extract
- [9] **decision** tool=extract
  - _Read the savings balance value from the member page._
- [9] **guardrail** verdict=allow
- [9] **action** action_type=extract
- [10] **decision** tool=assert_state
  - _Verify the savings balance is visible on the member page before reporting._
- [10] **guardrail** verdict=allow
- [10] **action** action_type=assert_state
- [11] **decision** tool=done
  - _The savings balance for member 34567 is visible on screen as $102,930.42._
- [11] **checkpoint** description=agent declared done
- **run_finished** status=completed

## Replay invocations

- params={'member_id': '34567'} → **success** outputs={'savings_balance': {'raw': '$102,930.42', 'amount': 102930.42}}
- params={'member_id': '00000'} → **business_outcome** `member_not_found`

## Capability artifact

- input_schema: `{"type": "object", "properties": {"member_id": {"type": "string", "example": "34567"}}, "required": ["member_id"]}`
- output_schema: `{"type": "object", "properties": {"savings_balance": {"type": "object", "x-shape": "currency"}}, "required": ["savings_balance"]}`
- checkpoint: `{"kind": "text_present", "params": {"text": "$102,930.42", "near": "Savings"}, "description": "derived"}`
- known_outcomes: ['member_not_found', 'permission_denied']

### Steps

0. **type** — Enter the member ID to search for. (idempotent=True)
   - rank 0 `dom_anchor` — form-control name attribute — legacy server-rendered forms expose these and they are stable across releases
   - rank 1 `role_name` — ARIA role only — usable when the control is the sole one of its role on the screen; verify uniqueness at replay
1. **click** — The search field already contains 34567; submit the search form. (idempotent=True)
   - rank 0 `dom_anchor` — form-control name attribute — legacy server-rendered forms expose these and they are stable across releases
   - rank 1 `role_name` — ARIA role only — usable when the control is the sole one of its role on the screen; verify uniqueness at replay
   - rank 2 `text` — visible/link text — readable and fairly stable, but breaks on wording or localization changes
2. **click** — Open the member record for 34567 to view their savings balance. (idempotent=True)
   - rank 0 `relative_to_landmark` — anchored to a visible row label ('the value cell in the Savings row') — robust against table nesting with no ids
   - rank 1 `text` — visible/link text — readable and fairly stable, but breaks on wording or localization changes
3. **extract** — Read the savings balance value shown on the member page. (idempotent=True)
   - rank 0 `relative_to_landmark` — anchored to a visible row label ('the value cell in the Savings row') — robust against table nesting with no ids
   - rank 1 `text` — visible/link text — readable and fairly stable, but breaks on wording or localization changes
4. **assert_state** — Verify the savings balance is visible on the member page before reporting. (idempotent=True)

## Evidence

- /evidence/b8ec4e9e701a42c7819af74748564a7b/step0-screenshot-1788904260756.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step1-screenshot-1788904263730.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step10-screenshot-1788904353232.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step11-screenshot-1788904354903.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step2-screenshot-1788904296726.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step3-screenshot-1788904302524.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step4-screenshot-1788904305283.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step5-screenshot-1788904308431.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step6-screenshot-1788904340417.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step7-screenshot-1788904343057.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step8-screenshot-1788904347406.png
- /evidence/b8ec4e9e701a42c7819af74748564a7b/step9-screenshot-1788904349865.png
