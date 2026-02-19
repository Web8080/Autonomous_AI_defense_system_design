# AI Safeguardrails

Constraints applied to autonomous and human-triggered actions so the system cannot take dangerous or unintended actions.

## Principles

- No lethal or irreversible autonomous action. High-impact actions require operator approval or are out of scope.
- All commands are validated (intent, asset_id, payload) before execution and logged.
- Inference inputs are bounded (size, batch size, URL allowlist) to prevent abuse and SSRF.

## Intent Allowlist

The following intents are accepted by the control service and gateway:

- `emergency_stop` – halt motion (all or one asset)
- `override` – human taking control
- `path_plan` – request path replan
- `mission_abort` – abort current mission
- `take_control` – operator take control
- `investigate` – move to investigate (AI or operator)
- `patrol` – run patrol route
- `retreat` – move to safe position

Intents such as `lethal`, `weapon`, `destroy`, `disable_permanent` are in `FORBIDDEN_INTENTS` and always rejected.

## AI-Only Intents

When the issuer is System AI (e.g. from autonomous agent), only these intents are allowed without human approval:

- `path_plan`, `investigate`, `patrol`, `retreat`, `mission_abort`

Other intents from the AI are rejected unless issued as part of an approved flow. Emergency stop and override are human-only from the API.

## Payload Limits

- Max 32 keys in command payload (nested keys count).
- Max depth 5 for nested objects/arrays.
- Max 8192 bytes serialized payload.

Larger or deeper payloads return 400.

## Asset ID

- Must be a valid UUID or the literal string `all` (for emergency stop all).
- Validated in gateway and control service; invalid values return 400.

## Inference Safeguards

- **image_b64**: Max size configurable via `MAX_IMAGE_B64_BYTES` (default 10 MiB). Invalid base64 or oversize returns 400.
- **image_url**: Allowed only if the URL prefix is in `INFERENCE_ALLOWED_URL_PREFIXES` (SSRF). Default dev: localhost, 127.0.0.1.
- **Batch**: Max frames per request via `MAX_BATCH_FRAMES` (default 20). Excess returns 400.
- **Detections per frame**: Capped by `MAX_DETECTIONS_PER_FRAME` (default 50) in inference logic.

## Geofence and Speed

Handled in the autonomous agent and control adapters: commands that would leave the allowed polygon or exceed max speed are rejected or clipped. See simulation/autonomous_agent.py and robotics adapters.

## Enabling Dev Token

For local testing without a real JWT:

1. Set `ALLOW_DEV_TOKEN=true` and use `Authorization: Bearer dev-token`.
2. Do not use in production; with a proper JWT_SECRET, decode_jwt will validate tokens and dev token is ignored unless ALLOW_DEV_TOKEN is set.
