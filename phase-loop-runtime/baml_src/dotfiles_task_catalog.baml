class DotfilesTask {
  id string
  title string
  audience string
  owner string
  dependencies string[]
  status string
}

class DotfilesTaskReference {
  title string
  source_path string
  anchor string
}

class DotfilesTaskCatalog {
  tasks DotfilesTask[]
  audiences string[]
  references DotfilesTaskReference[]
}
