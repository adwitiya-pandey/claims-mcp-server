# Decisions

This log records the implementation choices for the v0.1.0 contract.

| # | Decision | Rejected alternative | Why | Proven by |
|---|---|---|---|---|
| D1 | Five named, parameterized read tools | A generic SQL tool | Fixed contracts reduce attack surface and are auditable | `tests/test_queries.py`, `tests/test_injection.py` |
| D2 | `mode=ro`, `query_only`, and a read-only SQLite authorizer | Regex checking for `SELECT` | Each boundary blocks a different class of accidental or hostile write | `tests/test_db_readonly.py` |
| D3 | SQLite behind `db.py` | Add a database service | No server is needed in CI; query contract stays behind one module | `tests/test_db_readonly.py` |
| D4 | Append-only JSONL audit file separate from the claims DB | Audit table in the claims DB | Claims data remains read-only | `tests/test_audit.py` |
| D5 | Return no data if either audit sink fails | Best-effort logging | A call must not appear successful without its audit trail | `tests/test_audit.py`, `tests/test_injection.py` |
| D6 | Sanitize, cap, delimiter-wrap, and mark all note text untrusted | Pass note text through raw | It labels data as data and reduces control-character risks; it does not guarantee model safety | `tests/test_security.py`, `tests/test_injection.py` |
| D7 | Per-key tool allowlists | One all-powerful key | Least privilege limits misuse of a stolen key | `tests/test_security.py`, `tests/test_server_protocol.py` |
| D8 | SHA-256 hashes at rest and constant-time comparison | Plaintext keys in `keys.json` | The key database is not itself a credential | `tests/test_security.py` |
| D9 | Local stdio has no caller-key auth; HTTP requires a bearer key | Expose unauthenticated network service | A local pipe is protected by its launching process; network access needs explicit authentication | `tests/test_server_protocol.py` |
| D10 | One read-only SQLite connection per call | A shared connection or pool | SDK sync handlers may run in different worker threads | `tests/test_server_protocol.py` |
| D11 | In-memory fixed-window rate limit | Redis or token bucket | This MVP is single-process and documents that limitation | `tests/test_security.py`, `tests/test_injection.py` |
| D12 | Query contract tests were written before SQL | Implement unverified SQL first | Tests pin the join, filters, sorting, aggregates, and arithmetic | `tests/test_queries.py` |
| D13 | Override `MCPServer.call_tool` to receive raw argument dictionaries before SDK tool validation | Typed SDK handler validation | The installed SDK validates typed handler arguments before the function and can echo rejected input; the override preserves typed schemas while validating inside the audited pipeline | `tests/test_server_s13.py` |
| D14 | Wrap the Streamable HTTP app with static bearer-key ASGI middleware | SDK OAuth `TokenVerifier` plus OAuth discovery settings | This project has a local hashed static-key file and no OAuth issuer; the ASGI boundary rejects requests before MCP parsing and emits a redacted auth event | `tests/test_server_protocol.py` |
| D15 | Keep the spec's file audit and add a separate structured stderr security event | Replace either sink | JSONL provides the original audit contract; stderr JSON supports the requested transport-safe operational stream | `tests/test_audit.py`, `tests/test_server_s13.py` |
| D16 | Mask identifiers in audit arguments and structured claim IDs | Log full IDs everywhere | IDs are treated as sensitive even though the dataset is synthetic; tool responses retain the exact contract fields | `tests/test_audit.py`, `tests/test_server_s13.py` |
| D17 | Return structured content alongside compact JSON text | Text-only tool output | Consumers can use the schema-shaped result without parsing text | `tests/test_server_protocol.py` |

## SDK evidence

The installed environment used Python 3.12.15 and `mcp` 2.3.0. The import and
behavior probes are recorded in [mcp_sdk_notes.md](./mcp_sdk_notes.md). The
official SDK documentation describes `TokenVerifier` as an OAuth resource-server
integration; this project instead uses a small HTTP ASGI middleware because it
has static hashed keys and no authorization server.

## Audit behavior

Each tool call writes one append-only JSONL record and one redacted JSON event to
stderr. HTTP authentication attempts produce a separate stderr event before
MCP request parsing. The stderr event never contains the plaintext bearer key;
key values are represented by a SHA-256 prefix. Invalid tool inputs are not
written to audit `args`; only model field names are recorded.
