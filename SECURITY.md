# Security Policy

## Reporting a vulnerability

Please do not open a public GitHub issue for a vulnerability that could expose
credentials, authorization data, private audio, transcripts, or remote service
access.

Use GitHub's private vulnerability reporting feature for this repository when
it is available. If private reporting is not enabled, contact the repository
owner privately through the contact method listed on the owner's GitHub
profile.

Include:

- the affected component and version or commit;
- steps to reproduce the issue;
- the likely impact;
- any suggested mitigation;
- only sanitized logs or examples.

Do not include real API keys, Telegram tokens, user IDs, audio, transcripts, or
authorization databases.

## Supported versions

Security fixes are applied to the current default branch. Older tags are not
guaranteed to receive backports.

## Deployment responsibilities

Operators should:

- keep `.env`, `authorized.json`, and the SQLite whitelist database private;
- rotate any credential that may have been exposed;
- run the container as the provided non-root user;
- keep `authorized.json` mounted read-only;
- keep dependencies and the host Docker/FFmpeg installation updated;
- leave `LOG_SENSITIVE_TEXT=0` except during controlled debugging;
- restrict filesystem access to `audio_files/` and remove stale runtime data.

