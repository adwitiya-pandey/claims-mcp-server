# Queries in plain English

The server exposes five named lookups. Each SQL statement is fixed in the source;
values such as claim IDs, member IDs, years, limits, and offsets are passed through
SQLite `?` placeholders. Callers cannot supply table names, SQL fragments, or sort
orders. The database connection is independently opened in read-only mode.

## `get_claim`

This query starts with one row in `claims`, then joins `members` to learn the
member's plan and `providers` to learn the provider's network status. It returns
only the fields in the tool contract. The claim ID is bound as a value, so SQLite
never treats it as SQL syntax.

## `list_member_claims`

First, a separate lookup checks that the member exists. A `COUNT(*)` query reports
the number of rows matching the member and optional status. The page query uses the
same filter, sorts by service date and claim ID newest-first, then applies
`LIMIT ? OFFSET ?`. A missing member is distinguishable from a member with no
claims.

## `get_coverage_rule`

The query matches a fixed pair of columns, `plan_id` and `procedure_code`, against
two bound values. SQLite integers for `covered` and `requires_preauth` are converted
to booleans in Python. Missing rules remain missing; the server does not infer that
an absent rule means covered.

## `get_member_ytd_summary`

The first query joins a member to the member's plan for its annual limit and
deductible. The second filters claims to the supplied member and the first four
characters of `service_date`, groups by claim status, and calculates count and
sums. Python subtracts the paid-claim sum from the annual limit and floors the
result at zero. A member with no claims in that year has an empty status list.

## `get_claim_notes`

The query checks that the claim exists, then selects its notes newest-first with a
bound limit. The query intentionally returns note text unchanged to its caller
inside the server; the output pipeline sanitizes and caps it, and marks it
untrusted before returning it. Notes are never included in logs.

## Beginner summary

The SQL is deliberately boring: five fixed questions, five fixed query shapes, and
all caller data is supplied separately from the SQL text. `?` parameters prevent a
claim ID that looks like SQL from becoming SQL. The database layer also blocks
writes, and the output pipeline labels claim notes as untrusted text rather than
instructions.
