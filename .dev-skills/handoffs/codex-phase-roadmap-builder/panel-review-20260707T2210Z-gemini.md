## gemini — OK

Here is an independent review of the Phase Roadmap (v6) for the Advisor Panel ingestion gap (#114), focusing on risks, structural tradeoffs, and adversarial edge cases before you execute CTXFREEZE.

### 1. Architectural & Security Risks (Adversarial View)
*   **The "Hash and Analyze" DoS Risk:** To build the `context_refs` manifest, the ingestion layer plans to perform path normalization, hashing, MIME detection, and PDF page counts. If a caller or automated process passes a massive file (e.g., a 50GB log file), a circular symlink, or a special device file (`/dev/urandom`), the ingestion process could hang indefinitely, OOM, or crash. 
    *   *Tradeoff:* Gathering rich metadata improves the LLM's context, but doing so synchronously at ingestion time introduces vulnerability to malicious or accidental filesystem traps.
*   **"Soft Warning" Hallucination Risk:** The roadmap specifies that missing/unreadable paths fail closed by default but allow an explicit "soft-warning opt-in" which produces "explicit unreadable metadata." 
    *   *Risk:* LLMs are notorious for hallucinating file contents when told a file exists but cannot be read. If the ingestion layer just injects "File X: Unreadable", the LLM leg may confidently invent the contents of File X based on its filename. 
*   **Leakage via Output (The "Boomerang" Effect):** The sentinel non-inlining proof (CTXVERIFY-1) explicitly ensures the *input bundle* is clean. However, if an LLM leg uses local tools to read a strictly private file, it may blindly quote or summarize that private data in its final returned artifact or logs. The roadmap claims privacy by protecting the ingestion prompt, but ignores the exfiltration path through the agent's output.

### 2. Phase Decomposition Tradeoffs
*   **Coupling Privacy (CTXIMPL) with Reliability (CTXRELY):** You are tying a critical privacy/architecture fix (context ingestion) to a generic reliability enhancement (Gemini retries and timeout threading). While they may share a branch currently, combining them in a single freeze and PR increases the blast radius if the retry logic proves flaky.
    *   *Tradeoff:* Releasing them together saves PR overhead, but if the transient retry state machine introduces race conditions, it could force a rollback that accidentally rips out the `context_refs` privacy boundary.

### 3. Contract & Scope Clarifications needed in CTXFREEZE
*   **Dynamic vs. Static Instructions:** IF-0-CTXFREEZE-2 mentions the manifest will include "local-tool instruction text." The ingestion layer shouldn't assume the downstream leg actually *has* the tools to read the file (e.g. `cat` vs `view_file` vs no tools at all). If the instructions are static, they will break unsupported backings. The contract must clarify how the panel knows what tool instructions to inject.
*   **Output DLP Out-of-Scope:** The Non-Goals section should explicitly state whether Data Loss Prevention (DLP) or output sanitization of the agent's response is out of scope. 

---

### Final Recommendation

**PROCEED to CTXFREEZE, but ONLY AFTER modifying the spec to address the following blockers:**

1.  **Enforce File Constraints in CTXFREEZE-2:** Add explicit bounds for metadata generation. The contract MUST define maximum file size thresholds for hashing/PDF page counting (e.g., "Files over 50MB skip hashing and page counting"), and enforce strict rejection of symlink escapes and non-regular files (block devices, pipes).
2.  **Mitigate Soft-Warning Hallucinations:** Update the CTXFREEZE-1 contract so that the "soft-warning" opt-in injects strict negative prompts (e.g., *"CRITICAL: You cannot read this file. Do not attempt to guess its contents"*).
3.  **Decouple CTXRELY as a Fast-Follow:** Treat Phase 3 (CTXRELY) as a completely independent lane. If the timeout/retry logic fails in testing, it should not block the release of Phase 1, 2, 4, and 5. Ensure the PR closeout (Phase 5) can successfully ship `context_refs` even if the retry logic is stripped from the final merge.
