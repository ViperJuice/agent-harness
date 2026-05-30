class DotfilesSourceRoot {
  path_glob string
  classification string
  owner string
  ingestion_policy string
}

class DotfilesSchemaRef {
  class_name string
  source_path string
  digest string
}

class DotfilesC4Ref {
  title string
  source_path string
  anchor string
}

class DotfilesTaskCatalogRef {
  catalog_id string
  source_path string
  audience string
}

class DotfilesAdoptionManifest {
  source_roots DotfilesSourceRoot[]
  schema_refs DotfilesSchemaRef[]
  plan_refs DotfilesPlanRef[]
  c4_document_refs DotfilesC4Ref[]
  task_catalog_refs DotfilesTaskCatalogRef[]
  operating_mode string
  redacted_metadata_ref string
  visibility_contract_ref string
  version string
  generated_at string
}
