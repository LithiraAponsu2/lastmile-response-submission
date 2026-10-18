# Security reporting

LastMile Response is a prototype using fictional exercise records, not an emergency service. Report suspected vulnerabilities privately to the repository owner through an existing private contact channel. Do not post exploit details, credentials, invitation/session links, uploaded records or personal information in public issues.

Private vulnerability reporting for this repository will be enabled/verified before public release; only then should its dedicated report link be advertised. No fixed response deadline is promised. Include the affected version, impact and a reproduction with isolated fictional data.

Relevant issues include incident/role permission bypass, untrusted-content execution, prompt injection that bypasses domain controls, or unintended exposure of records/credentials. Do not probe real government systems, use real personal data or run load tests against the hosted demo. The normal-account flow and simulated demo roles have distinct trust boundaries; see [SAFETY.md](docs/SAFETY.md).

Keep credentials outside source and screenshots/logs. Cloud Run uses an attached identity. Rotate any genuine exposed secret; deleting a file alone does not invalidate it.
