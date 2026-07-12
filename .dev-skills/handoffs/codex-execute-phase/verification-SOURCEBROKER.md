# Verification: SOURCEBROKER

Summary: PASSED locally — focused broker/resolver suite 67 passed, 1 skipped;
standalone Gate A 2,389 passed, 35 skipped, 593 deselected. Git diff checks
passed. The 0.7.0 sdist and wheel build passed, roadmap validation reported one
valid phase, and plan validation reported three lanes with zero warnings.
GitHub PR #180's seven checks passed before the latest documentation amendment
and must pass again at the final head before merge.

- Redaction posture: metadata only.
- Permanent live deployment: deferred until the corrected Agent Harness PR
  merges; no broker listener, environment, `/opt` venv, or Tailscale route was
  created by these gates.
- System-unit boundary: disposable claw root-manager transients ran as
  `viperjuice:viperjuice` with zero permitted/effective/ambient capabilities and
  `NoNewPrivileges=1`.
- Mount confinement: `ProtectHome=tmpfs` created a distinct mount namespace;
  only the exact owner socket was rebound read-only with host-matching device
  and inode. Adjacent Codex and unrelated home content were hidden.
- Runtime compatibility: Python thread start/join, exact owner-socket connect,
  `PrivateDevices`, `ProtectKernelModules`, and deny-all/allow-localhost IP
  policy passed together. `MemoryDenyWriteExecute` is omitted because an
  isolated trace proved it denied the Python 3.13/glibc executable thread-stack
  `mprotect` with `EPERM`.
- Procfs confinement: a same-UID control proved both hidden-home escape paths
  readable through `/proc/<pid>/root`. All supported `ProtectProc` modes remained
  insufficient. `InaccessiblePaths=/proc` removed both escape paths while the
  thread, socket-connect, capability, and no-new-privileges checks still passed.
- User-manager rejection: claw systemd 249 accepted but did not enforce the
  former user-unit mount controls (`PrivateMounts=no`), so the deployment
  artifact is now a root-managed system unit with a root-owned immutable `/opt`
  venv and root-owned digest-only `/etc` environment.
- Review status: Grok and Gemini agreed on the initial PR #180 head. Sol's
  same-UID procfs finding is remediated above. Exact amended-head four-seat
  re-review remains required before merge.
