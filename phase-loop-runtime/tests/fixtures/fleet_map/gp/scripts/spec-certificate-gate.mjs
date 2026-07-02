// Fixture source file: a gp-style gate script with a hard-coded sibling
// checkout path into the consiliency-portal repo, rather than a package dep.
const SPEC_CERTIFICATE_PATH = "/home/maintainer/code/consiliency-portal/plans/unification/spec-certificate.json";

export function readSpecCertificate() {
  return SPEC_CERTIFICATE_PATH;
}
