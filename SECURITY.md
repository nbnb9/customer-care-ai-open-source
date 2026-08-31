# Security Policy

## Supported versions

Security fixes are applied to the latest commit on `main`. This repository is a
pre-production reference implementation; no tagged release is currently approved
for production use.

## Reporting a vulnerability

Do not open a public issue for a suspected vulnerability. Use
[GitHub private vulnerability reporting](https://github.com/nbnb9/customer-care-ai-open-source/security/advisories/new)
and include:

- affected commit and component;
- reproduction steps using synthetic data;
- expected and observed security boundary;
- potential tenant, identity, data, or external-action impact; and
- any temporary mitigation you have verified.

Do not submit real customer data, credentials, tokens, private endpoints, or
production logs. Reports are handled on a best-effort basis; no response or
remediation SLA is currently offered.

The automated security suite covers documented regression cases only. It is not
a substitute for production identity, infrastructure hardening, penetration
testing, monitoring, incident response, or an organization-specific risk review.
