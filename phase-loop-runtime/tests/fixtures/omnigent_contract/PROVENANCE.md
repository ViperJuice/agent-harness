# Vendored Omnigent v0.4.0 transport contract (ABDOMNI)

`http-surface.json` and `source-metadata.json` are copied **byte-for-byte** from
omniagent-plus at:

    repo:   Consiliency/omniagent-plus
    commit: 744420d2e545a0c6dbdb4c58528e859e7a439a1a  (origin/main)
    path:   fixtures/omnigent/discovery/{http-surface.json,source-metadata.json}

They are the *frozen* v0.4.0 transport contract (`freeze_target.tag = v0.4.0`,
`package_version = 0.4.0`). ABDOMNI's Python Omnigent backing
(`advisor_board.backing_omnigent`) is anchored to THIS contract, not to a reading
of the TypeScript `http-client.ts`: `tests/test_advisor_board_backing_omnigent.py`
asserts every `(method, path-template)` the Python client issues appears in
`http-surface.json`, and that the freeze target is `0.4.0`. That converts "we did
not fork the transport" from a claim into a checked invariant — if omniagent-plus
moves the surface, re-vendor these files and the conformance test re-locks it.
