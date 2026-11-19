# Example data-access policy for the local-postgres-opa-superset profile
# (issue #1284). These Rego files are the reference implementation of the
# provider-neutral data-access-policy contract: they are version-controlled
# next to the profile and mounted read-only into the policy engine
# (modules-experimental/security/opa), which serves decisions at
# POST /v1/data/<package path>.
#
# The contract itself names no engine or language; this file is plain Rego
# because the opa module is the OPA reference implementation.
package cds.analytics

# Purpose binding: access to the analytics dataset is only allowed for an
# approved processing purpose.
default allow := false

allow if {
	input.purpose == "reporting"
}

allow if {
	input.purpose == "statistical-analysis"
}

# Row filter: a consumer only sees rows of its own department.
row_filter contains row if {
	some row in input.rows
	row.department == input.attributes.department
}

# Column masking: direct identifiers are masked for every department except
# the owning one.
masked_columns contains "bsn" if {
	input.attributes.department != "hr"
}
