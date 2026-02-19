# OWASP Top 10 (2021) Mapping

How this project mitigates each OWASP Top 10 category.

---

## A01:2021 – Broken Access Control

| Mitigation | Location |
|------------|----------|
| RBAC enforced server-side on every request | API gateway: `require_role()`, `get_current_user()`; backend services do not trust client role |
| Region-scoped data for Local Operator | Asset/alert list filtered by `X-Region-Ids` and `region_id`; gateway forwards only allowlisted query params |
| Query param allowlisting | `filter_query_params()` in gateway and `ALLOWED_QUERY_*` in `defense_shared/security.py` |
| Audit log for sensitive actions | `audit_log()` on emergency_stop and command; control service writes to `audit.command_log` |
| JWT audience and issuer | `decode_jwt()` validates issuer and audience |

---

## A02:2021 – Cryptographic Failures

| Mitigation | Location |
|------------|----------|
| JWT secret length | `decode_jwt()` returns None if `JWT_SECRET` is missing or len < 32 |
| No secrets in logs | Audit log logs action/resource/user_id only; no tokens or passwords |
| TLS in production | Enforced at load balancer / ingress; `Strict-Transport-Security` header set |
| No sensitive data in query strings | Sensitive operations use POST body; query params are allowlisted and sanitized |

---

## A03:2021 – Injection

| Mitigation | Location |
|------------|----------|
| Parameterized queries | All DB access uses asyncpg with `$1`, `$2` placeholders; no string concatenation |
| String sanitization | `sanitize_string()`, `sanitize_issued_by()` with max length and control-char strip |
| Command payload validation | `validate_command_payload()` limits depth, key count, and byte size; intent allowlist |
| Input validation | Pydantic models with `Field(max_length=...)`; inference request fields bounded |

---

## A04:2021 – Insecure Design

| Mitigation | Location |
|------------|----------|
| AI safeguardrails | Allowed intents, forbidden intents, payload size/depth limits in `defense_shared/safeguardrails.py` |
| No autonomous lethal action | FORBIDDEN_INTENTS and intent allowlist; design docs state no lethal/irreversible action |
| Fail-safe path | Emergency stop is dedicated endpoint with minimal dependencies; logged always |
| Rate limiting | Per-IP (or X-Forwarded-For) rate limit in gateway; stricter for auth-like paths |

---

## A05:2021 – Security Misconfiguration

| Mitigation | Location |
|------------|----------|
| Security headers | `SecurityHeadersMiddleware`: X-Content-Type-Options, X-Frame-Options, CSP, HSTS, Referrer-Policy |
| CORS allowlist | `CORS_ORIGINS` env; no wildcard in production |
| Default deny for URLs | Inference `image_url` allowed only if `INFERENCE_ALLOWED_URL_PREFIXES` includes the prefix (SSRF) |
| Audit list limit | Control service caps `limit` at `MAX_AUDIT_LIMIT` (500) |

---

## A06:2021 – Vulnerable and Outdated Components

| Mitigation | Location |
|------------|----------|
| Pinned dependencies | `requirements.txt` with version lower bounds; lock file recommended for deploys |
| No unnecessary services exposed | Health endpoints do not leak stack or config; debug mode not used in prod |
| Upgrade policy | Document in README; CI can run `pip audit` or similar |

---

## A07:2021 – Identification and Authentication Failures

| Mitigation | Location |
|------------|----------|
| JWT validation | Algorithm, issuer, audience, expiry; leeway for clock skew |
| No default credentials | No hardcoded passwords; credentials from env or secret manager |
| Rate limit on auth path | Lower limit for login-like paths to slow brute force |
| Dev token opt-in | `ALLOW_DEV_TOKEN=true` and token `dev-token` only when explicitly set |

---

## A08:2021 – Software and Data Integrity Failures

| Mitigation | Location |
|------------|----------|
| Command integrity | Commands validated (intent, payload) before execution; issued_by set server-side from JWT |
| No deserialization of untrusted objects | JSON only; no pickle or arbitrary object restore from client |
| Image URL fetch | Only allowlisted hosts (SSRF); no redirect following to internal IPs without explicit allowlist |

---

## A09:2021 – Security Logging and Monitoring Failures

| Mitigation | Location |
|------------|----------|
| Audit log | Every emergency_stop and command logged with user_id, action, resource, path |
| Structured logging | `audit_log()` uses structured fields (action, resource, user_id, detail) |
| No PII in logs | Audit does not log request body or tokens; detail truncated to 200 chars |
| Rate limit headers | X-RateLimit-Limit and X-RateLimit-Remaining on responses |

---

## A10:2021 – Server-Side Request Forgery (SSRF)

| Mitigation | Location |
|------------|----------|
| URL allowlist for inference | `is_url_safe_for_fetch()` in `defense_shared/security.py`; `INFERENCE_ALLOWED_URL_PREFIXES` env |
| No client-controlled URL fetch without check | Inference accepts `image_url` only if prefix is in allowlist; default localhost/127.0.0.1 for dev |
| Query params not passed as URLs | Downstream service URLs are from env, not from client |

---

## AI Safeguardrails Summary

- **Allowed intents**: Only `emergency_stop`, `override`, `path_plan`, `mission_abort`, `take_control`, `investigate`, `patrol`, `retreat` are accepted. `FORBIDDEN_INTENTS` (e.g. lethal, weapon) are never accepted.
- **Payload**: Max keys, depth, and byte size enforced; invalid payloads rejected with 400.
- **Asset ID**: Must be UUID or literal `all`; validated in gateway and control service.
- **Inference**: Max image size (base64), batch size cap, and SSRF-safe URL only for `image_url`.
