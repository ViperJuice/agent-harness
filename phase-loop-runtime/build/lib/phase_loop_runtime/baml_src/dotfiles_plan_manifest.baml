// DotfilesPlanType enum literals: phase, detailed
// type enum literals: phase, detailed

class DotfilesPlanMetadataItem {
  key string
  value string
}

class DotfilesPlanMetadata {
  entries DotfilesPlanMetadataItem[]
}

class DotfilesPlanRef {
  slug string
  file string
  type string
  digest string?
  status string
}

class DotfilesPlanLifecycleEvent {
  transition string
  by string
  at string
  metadata DotfilesPlanMetadata
}

class DotfilesPlanEntry {
  slug string
  file string
  type string
  status string
  created_at string
  updated_at string
  owner_skill string
  handoff_ref string?
  reflection_ref string?
  task_summary string?
  acceptance_criteria_count int?
  roadmap_ref DotfilesPlanRef?
  phase_alias string?
  if_gates_produced string[]
  lanes string[]
  lifecycle DotfilesPlanLifecycleEvent[]
}

class DotfilesPlanManifest {
  schema_version int
  plans DotfilesPlanEntry[]
}
