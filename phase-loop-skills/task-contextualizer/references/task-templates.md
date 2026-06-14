# Task Prompt Templates

Copy and fill in the placeholders (in ALL_CAPS) for each task type.

## Bug Fix Template

```
Fix DESCRIPTION in FILE_PATH.

Error: "ERROR_MESSAGE" at line LINE_NUMBER.

Related files:
- TYPE_DEFS_PATH — type definitions used by this file
- BASE_CLASS_PATH — parent class or service it extends
- TEST_FILE_PATH — existing tests for this module
- CONFIG_PATH — relevant config (tsconfig.json, etc.)

Architecture: BRIEF_DESCRIPTION of how this module fits into the system.

Scope: Only modify ALLOWED_FILES. Do not change EXCLUDED_FILES.
Expected output: Modified files that resolve the type error.
```

## New Feature Template

```
Implement FEATURE_DESCRIPTION.

Example to follow: EXAMPLE_FILE_PATH (shows the pattern for similar features)
Create new files in: TARGET_DIRECTORY
Naming convention: NAMING_PATTERN (e.g., camelCase, kebab-case)

Related files:
- ROUTER_OR_INDEX_PATH — where to register/export the new feature
- TYPE_DEFS_PATH — existing types to reuse or extend
- TEST_PATTERN_PATH — example test file showing test conventions

Architecture: BRIEF_DESCRIPTION of how new features are structured in this project.

Scope: Create new files in TARGET_DIRECTORY. Register in ROUTER_FILE. Do not modify EXCLUDED_FILES.
Expected output: New files implementing the feature + registration in index/router.
```

## Explore / Research Template

```
Research QUESTION about this codebase.

Start from these files:
- FILE_1 — BRIEF_DESCRIPTION
- FILE_2 — BRIEF_DESCRIPTION
- FILE_3 — BRIEF_DESCRIPTION

What I already know:
- KNOWN_FACT_1
- KNOWN_FACT_2

Specific questions to answer:
1. QUESTION_1
2. QUESTION_2
3. QUESTION_3

Expected output: A summary answering each question with file paths and line references.
```

## Refactor Template

```
Refactor DESCRIPTION across these files:
- FILE_1
- FILE_2
- FILE_3

Current pattern: CURRENT_PATTERN_DESCRIPTION
Target pattern: TARGET_PATTERN_DESCRIPTION

Constraints:
- CONSTRAINT_1 (e.g., "maintain backward compatibility with existing API")
- CONSTRAINT_2 (e.g., "do not change test files, only source files")

Type definitions: TYPE_DEFS_PATH
Test file: TEST_FILE_PATH (run after refactor to verify)

Expected output: Modified files following the target pattern. All existing tests must still pass.
```

## Multi-File Edit Template

```
Make the following related changes:

1. In FILE_1: CHANGE_DESCRIPTION_1
2. In FILE_2: CHANGE_DESCRIPTION_2
3. In FILE_3: CHANGE_DESCRIPTION_3

These changes are related because: RELATIONSHIP_DESCRIPTION

Type definitions: TYPE_DEFS_PATH
Config: CONFIG_PATH

Verification: After all edits, run VERIFICATION_COMMAND to confirm no errors.
Scope: Only modify the listed files.
```
