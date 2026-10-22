# Threat model

## Scope

This server is a local, read-only MCP boundary over synthetic health-insurance
claims. It serves five fixed query tools. There are no LLM calls, write tools,
external data sources, or real people in the seed.

## Assets

- Claims, coverage, member, provider, and note rows in the SQLite database.
- API keys and their tool allowlists in `keys.json`.
- The integrity and availability of the append-only audit record.
- The JSON-RPC stdout channel used by the stdio transport.
- Tool responses, including untrusted claim note text.

## Trust boundaries

1. **Caller to transport:** stdio is trusted to the local launching process.
   Streamable HTTP accepts only a valid bearer key on the configured loopback
   endpoint.
2. **Transport to tool pipeline:** raw argument dictionaries are intercepted
   before SDK tool-argument validation. Strict local models reject malformed,
   missing, and extra fields.
3. **Tool pipeline to database:** only fixed parameterized query functions can
   open a per-call read-only SQLite connection.
4. **Database to caller:** output fields are selected by the tool contract;
   note content is sanitized, capped, delimited, and labeled untrusted.
5. **Process to logs:** stdout remains available for stdio JSON-RPC. Structured
   security JSON goes only to stderr; the separate append-only audit file does
   not contain result payloads or note text.

## STRIDE analysis

| Threat class | Example | Mitigation | Test evidence |
|---|---|---|---|
| Spoofing | An unknown or missing HTTP bearer key calls a tool | Authenticate the bearer key against SHA-256 hashes before MCP parsing; compare in constant time; log only a key-hash prefix | `tests/test_security.py`, `tests/test_server_protocol.py` |
| Tampering | An input changes the query to read unrelated tables or write data | Fixed SQL with `?` values; strict ASCII ID patterns; SQLite read-only URI, query-only flag, and authorizer | `tests/test_queries.py`, `tests/test_db_readonly.py`, `tests/test_injection.py` |
| Repudiation | A caller denies a tool access or a failed request is omitted from the trail | One append-only JSONL line for every tool invocation; redacted structured stderr event; fail closed if audit output fails | `tests/test_audit.py`, `tests/test_server_s13.py`, `tests/test_injection.py` |
| Information disclosure | A key, note, invalid argument, synthetic member identifier, or traceback appears in logs | Hash keys; omit invalid values; mask identifiers in audit events; never log result payloads or note text; return only a request reference on unexpected errors | `tests/test_audit.py`, `tests/test_server_protocol.py`, `tests/test_injection.py` |
| Denial of service | Huge notes, unbounded pagination, or repeated calls consume resources | Input bounds, 1,000-character output cap, bounded result limits, and a per-key fixed-window rate limiter | `tests/test_validation.py`, `tests/test_security.py`, `tests/test_injection.py` |
| Elevation of privilege | A limited key calls an unapproved tool or a caller uses extra parameters to bypass validation | Per-key tool allowlists; closed Pydantic schemas; raw-argument interception; only five registered tools | `tests/test_server_protocol.py`, `tests/test_server_s13.py`, `tests/test_injection.py` |

## Security requirement to test mapping

| Requirement | Implementation | Named test |
|---|---|---|
| S1 read-only database | `src/claims_mcp/db.py` | `tests/test_db_readonly.py` |
| S2 values never become SQL syntax | `src/claims_mcp/queries.py`, `src/claims_mcp/models.py` | `tests/test_validation.py`, `tests/test_queries.py`, `tests/test_injection.py` |
| S3 parameterized query AST policy | `src/claims_mcp/queries.py` | `tests/test_queries.py::test_query_module_has_no_sql_interpolation_or_concatenation` |
| S4 HTTP authentication and hashed keys | `src/claims_mcp/http_auth.py`, `src/claims_mcp/security.py` | `tests/test_security.py`, `tests/test_server_protocol.py` |
| S5 per-key tool allowlists | `src/claims_mcp/security.py`, `src/claims_mcp/server.py` | `tests/test_security.py`, `tests/test_server_protocol.py` |
| S6 fixed-window rate limit | `src/claims_mcp/security.py` | `tests/test_security.py`, `tests/test_injection.py` |
| S7 note sanitization and untrusted marking | `src/claims_mcp/security.py`, `src/claims_mcp/server.py` | `tests/test_security.py`, `tests/test_injection.py` |
| S8 safe unexpected-error response | `src/claims_mcp/server.py` | `tests/test_server_protocol.py::test_unexpected_database_failure_returns_safe_internal_error` |
| S9 exactly one fail-closed audit file record per tool call | `src/claims_mcp/audit.py`, `src/claims_mcp/server.py` | `tests/test_audit.py`, `tests/test_server_s13.py`, `tests/test_injection.py` |
| S10 startup configuration validation | `src/claims_mcp/config.py` | `tests/test_config.py` |
| S11 secret and runtime artifact exclusions | `.gitignore`, `.dockerignore` | `tests/test_repository_hygiene.py` |
| S12 locked minimal dependencies | `pyproject.toml`, `uv.lock` | `tests/test_repository_hygiene.py`, CI `uv sync --locked` |
| S13 no SDK argument rejection before the pipeline | `src/claims_mcp/server.py` | `tests/test_server_s13.py` |

## Residual risks and limitations

- A downstream model can still be influenced by adversarial note text even when
  it is sanitized and delimited. The untrusted marker is not an enforcement
  mechanism.
- stdio has no caller identity beyond the local process. Anyone who can control
  that process can use the tools.
- HTTP is loopback-only and has no TLS. It is not suitable for direct exposure
  on an untrusted network.
- The limiter is process-local; multiple replicas do not share quotas.
- The JSONL audit file is not tamper-evident, encrypted, or remotely retained.
- Key generation prints a one-time key. Operators must protect that output and
  perform manual key rotation.
- Tool responses retain the member identifier required by the published
  `get_member_ytd_summary` contract. IDs are masked in audit/security logs, and
  no SSN field exists in the synthetic schema.
- Tests cover explicit cases, not all possible attacks or bugs.
