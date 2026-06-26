class DotfilesC4Anchor {
  id string
  title string
  source_path string
}

class DotfilesC4Document {
  title string
  mermaid_context_source string
  mermaid_container_source string
  mermaid_component_source string
  anchors DotfilesC4Anchor[]
  description string
}
