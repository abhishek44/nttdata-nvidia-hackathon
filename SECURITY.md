# Security Policy and Prototype Caveats

RecallZero is an engineering prototype, not a production safety-decision system.

## Secrets

- Keep `NVIDIA_API_KEY` in environment variables or an approved secret manager.
- Never commit `.env`.
- Do not place keys in NAT/AIQ YAML files or notebooks.
- Rotate any key exposed in logs or source control.

## Evidence and privacy

Owner complaint narratives are uncontrolled free text and may contain personal or sensitive details. Before organizational use:

- Define retention and deletion periods.
- Apply role-based access control.
- Redact unnecessary identifiers and personal data.
- Encrypt stored evidence and backups.
- Audit access to complaint narratives.
- Reconsider whether even an 11-character VIN prefix is necessary.

## API exposure

The included FastAPI server is configured for development convenience and permissive CORS. Do not expose it to an untrusted network without:

- Authentication and authorization.
- TLS termination.
- Restricted CORS origins.
- Rate limits and input-size limits.
- Asynchronous job controls for expensive requests.
- Centralized, redacted logs.
- Dependency and container vulnerability scanning.

## Model and analytical safety

- LLM output is validated but can still be wrong; use extraction confidence/method provenance and inspect source evidence.
- A complaint-associated crash, fire, injury, or death flag does not prove causation.
- Complaint volume is not an exposure-adjusted failure rate.
- Recall text similarity does not establish campaign scope.
- Default thresholds are uncalibrated prototype values.
- `EARLY_SIGNAL_DETECTED` is a backtest result, not evidence that a regulator or manufacturer should have acted on that exact date.

## Reporting vulnerabilities

For a private deployment, report vulnerabilities through your organization's security channel. Do not include API keys, raw personal data, or sensitive evidence in public issues.
