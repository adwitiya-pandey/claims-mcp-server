# Open questions

## Summary member identifier

The `get_member_ytd_summary` response contract explicitly includes `member_id`,
while the additional privacy request asks to mask or omit member IDs in summary
views. This implementation preserves the published tool contract and masks IDs
in audit/security events; the synthetic tool response still contains the requested
member ID. Changing that output would require an explicit contract decision.
