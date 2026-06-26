client<llm> EvidenceAuditClient {
  provider "openai-generic"
  options {
    base_url "https://example.invalid/v1"
    model "phase-loop-evidence-audit"
    default_role "user"
  }
}

class EvidenceJudgment {
  verdict string
  confidence float
  reasoning string
  specific_concerns string[]
}

function EvaluateSuspectedFakeEvidence(
  tier2_signal_summary: string,
  sample_artifact_content: string,
  expected_artifact_characteristics: string
) -> EvidenceJudgment {
  client EvidenceAuditClient
  prompt #"
Evaluate whether a suspected evidence artifact looks fabricated.

Inputs are intentionally bounded:
- Tier 2 signal summary: {{ tier2_signal_summary }}
- Sample artifact content: {{ sample_artifact_content }}
- Expected artifact characteristics: {{ expected_artifact_characteristics }}

Return only an EvidenceJudgment object. Use verdict "real", "fake", or
"uncertain". Keep reasoning concise and list concrete concerns, if any.

{{ ctx.output_format }}
"#
}
