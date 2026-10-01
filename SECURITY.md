# Security Policy

## Supported versions

Security fixes land on `main` and ship in the next release. Only the latest
release is supported; please upgrade before reporting.

| Version | Supported |
| --- | --- |
| The [latest release](https://github.com/IACBI/promptbase-profile-exporter/releases/latest) | Yes |
| Any earlier release | No |

## Reporting a vulnerability

Please do not open a public issue for security problems. Report them privately
through
[GitHub's private vulnerability reporting](https://github.com/IACBI/promptbase-profile-exporter/security/advisories/new).

Include what you found, how to reproduce it, and the impact you expect. You
will get an acknowledgement, and a fix or a decision will follow as quickly as
the issue allows. Credit is given in the advisory unless you prefer otherwise.

## Scope

The exporter reads only public PromptBase data and never asks for credentials,
API keys, or cookies. Reports are most useful when they involve:

- the local web UI: bypassing its CSRF or DNS-rebinding checks, writing
  outside the working directory, or reading files through `/download` or the
  comparison-catalog field (see the
  [security model](docs/web-ui.md#security-model));
- script injection through exported files, such as the HTML catalog or CSV
  cells written with `--csv-safe`;
- file writes outside the requested output location, or path traversal;
- command execution, including through the GitHub Action's inputs;
- packaging or supply-chain issues that affect users.

Behavior of PromptBase itself is out of scope; report that to PromptBase.

## Responsible use

Only export profiles and data you are allowed to use, and respect PromptBase's
terms and the rights of prompt authors.
