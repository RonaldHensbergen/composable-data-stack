"""data-access-policy contract + governed-contract enforcement (#1284).

Covers the acceptance criteria of #1284 (provider-neutral data-access policy
contract, policy-as-code at the query layer):

- shared/contracts/data-access-policy.yaml is schema-valid via
  cli.validator.validate_contract_file (the repo-wide contract test also
  globs it) and pins the provider-neutral field shape; sql-database.yaml
  documents the reserved `governed` advertisement field.
- cli/resources/module.schema.json accepts `policyFrom` on a consume entry
  (and still rejects unknown keys there).
- validate_contract_bindings fails closed with E167 when a consumer binds a
  governed contract (`governed: true`, literal or via a ${config.<path>}
  reference incl. the configSchema-default fallback) without a policyFrom
  binding; a declared policyFrom that resolves to a non-data-access-policy
  contract is E042, a malformed/unknown ref is E041, and ungoverned contracts
  need no binding at all (backward compatible).
- The planner resolves policyFrom into a "<consume-name>-policy" binding and
  the renderer exposes it as ${bindings.<consume-name>-policy.*}; the
  provided contract's host/port are cross-checked like other endpoint
  contracts (E150/E164 candidate lists cover data-access-policy).
- modules-experimental/security/opa is the reference implementation: schema
  valid, experimental, digest-pinned image, renders the profile's policy
  directory read-only into the engine, and provides data-access-policy.
- profiles/local-postgres-opa-superset validates end-to-end (postgres
  governed + opa + superset with both policy bindings), renders the policy
  decision URI into the superset service, and fails closed with E167 when a
  policy binding is removed.
"""

import json
import os
import tempfile
import unittest
import unittest.mock
from pathlib import Path

import yaml
from jsonschema import Draft202012Validator

from cli.planner import build_plan
from cli.renderer import (
    _provided_contract_host_candidates,
    _provided_contract_port_candidates,
    render_compose,
)
from cli.validator import (
    validate_contract_bindings,
    validate_contract_file,
    validate_profile,
)

REPO_ROOT = Path(__file__).resolve().parent.parent
CONTRACTS_DIR = REPO_ROOT / "shared" / "contracts"
POLICY_CONTRACT_PATH = CONTRACTS_DIR / "data-access-policy.yaml"
SQL_DATABASE_CONTRACT_PATH = CONTRACTS_DIR / "sql-database.yaml"
OPA_MODULE_PATH = REPO_ROOT / "modules-experimental" / "security" / "opa" / "module.yaml"
DEMO_PROFILE_PATH = (
    REPO_ROOT / "profiles" / "local-postgres-opa-superset" / "profile.yaml"
)
MODULE_SCHEMA = json.loads(
    (REPO_ROOT / "cli" / "resources" / "module.schema.json").read_text(encoding="utf-8")
)

_TEST_ENV = {
    "CDS_MODULE_PATH": str(REPO_ROOT),
    "CDS_POSTGRES_SUPERUSER_PASSWORD": "test-postgres-superuser",
    "CDS_ANALYTICS_DB_PASSWORD": "test-analytics-db",
    "CDS_ANALYTICS_DB_NAME": "analytics",
    "CDS_ANALYTICS_DB_USER": "analytics",
    "CDS_SUPERSET_DB_PASSWORD": "test-superset-db",
    "CDS_SUPERSET_DB_NAME": "superset",
    "CDS_SUPERSET_DB_USER": "superset",
    "CDS_SUPERSET_SECRET_KEY": "test-superset-secret-key",
    "CDS_SUPERSET_ADMIN_PASSWORD": "test-superset-admin",
}


def _module(provides=None, consumes=None, category="cat", name="mod", config_schema=None):
    spec = {"provides": provides or [], "consumes": consumes or []}
    spec["configSchema"] = config_schema or {"type": "object"}
    return {
        "kind": "Module",
        "metadata": {"category": category, "name": name},
        "spec": spec,
    }


def _schema_module(consumes):
    """A complete module document (apiVersion/runtime/implementation included)
    so it validates against the full module.schema.json."""
    module = _module(consumes=consumes)
    module["apiVersion"] = "cds/v1alpha1"
    module["metadata"]["version"] = "0.1.0"
    module["spec"]["runtime"] = {
        "type": "container",
        "service": {
            "name": "mod",
            "ports": [{"name": "http", "containerPort": 80, "protocol": "TCP"}],
        },
    }
    module["spec"]["implementation"] = {"kind": "docker-compose", "compose": {"services": {}}}
    return module


def _inst(inst_id, module, config=None, index=0):
    return {"index": index, "id": inst_id, "config": config or {}, "module": module}


def _governed_provider(governed=True, index=1):
    contract_spec = {"host": "pg", "port": 5432}
    if governed is not None:
        contract_spec["governed"] = governed
    return _inst(
        "pg",
        _module(
            provides=[
                {"name": "sql", "contract": {"kind": "sql-database", "spec": contract_spec}}
            ],
            category="warehouse",
            name="pg",
        ),
        index=index,
    )


def _policy_provider(index=2):
    return _inst(
        "policy",
        _module(
            provides=[
                {
                    "name": "data-access-policy",
                    "contract": {
                        "kind": "data-access-policy",
                        "spec": {
                            "host": "policy-opa",
                            "port": 8181,
                            "decisionPath": "/v1/data",
                            "decisionUri": "http://policy-opa:8181/v1/data",
                        },
                    },
                }
            ],
            category="security",
            name="opa",
        ),
        index=index,
    )


def _consumer(consume, config):
    return _inst(
        "app",
        _module(consumes=[consume], category="bi", name="app"),
        config=config,
        index=0,
    )


class PolicyContractFileTest(unittest.TestCase):
    def test_contract_file_is_schema_valid(self) -> None:
        self.assertEqual(validate_contract_file(POLICY_CONTRACT_PATH), [])

    def test_contract_shape_is_provider_neutral(self) -> None:
        contract = yaml.safe_load(POLICY_CONTRACT_PATH.read_text(encoding="utf-8"))
        self.assertEqual(contract["metadata"]["name"], "data-access-policy")
        fields = contract["spec"]["fields"]
        for name in ("host", "port", "decisionPath", "decisionUri"):
            self.assertTrue(fields[name]["required"], name)
        for name in ("scheme", "healthPath", "policyVersion"):
            self.assertIn(name, fields)
            self.assertFalse(fields[name]["required"], name)
        # Provider-neutral: the contract names no engine or policy language.
        text = POLICY_CONTRACT_PATH.read_text(encoding="utf-8").lower()
        for engine in ("opa", "rego", "cedar", "trino"):
            self.assertNotIn(engine, text.replace("issue #1284", ""), engine)
        examples = contract["spec"]["examples"]
        self.assertTrue(
            any(
                e.get("decisionUri") == "http://policy-engine:8181/v1/data"
                and e.get("decisionPath") == "/v1/data"
                for e in examples
            )
        )

    def test_sql_database_contract_documents_governed_field(self) -> None:
        self.assertEqual(validate_contract_file(SQL_DATABASE_CONTRACT_PATH), [])
        contract = yaml.safe_load(SQL_DATABASE_CONTRACT_PATH.read_text(encoding="utf-8"))
        governed = contract["spec"]["fields"].get("governed")
        self.assertIsNotNone(governed)
        self.assertFalse(governed["required"])
        self.assertIn("E167", governed["description"])


class PolicyFromSchemaTest(unittest.TestCase):
    def _consume_entry(self, extra):
        entry = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
        }
        entry.update(extra)
        return entry

    def test_policy_from_is_accepted_on_consume_entries(self) -> None:
        module = _schema_module(
            [self._consume_entry({"policyFrom": "spec.config.dbPolicy"})]
        )
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(module))
        self.assertEqual(errors, [])

    def test_unknown_consume_keys_are_still_rejected(self) -> None:
        module = _schema_module(
            [self._consume_entry({"policyFroom": "spec.config.dbPolicy"})]
        )
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(module))
        self.assertTrue(errors)
        self.assertIn("policyFroom", errors[0].message)

    def test_policy_from_must_be_a_spec_config_path(self) -> None:
        module = _schema_module(
            [self._consume_entry({"policyFrom": "config.dbPolicy"})]
        )
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(module))
        self.assertTrue(errors)


class GovernedContractValidationTest(unittest.TestCase):
    """validate_contract_bindings with synthetic instances (issue #1284)."""

    def _run(self, consume, consumer_config, providers):
        consumer = _consumer(consume, consumer_config)
        return validate_contract_bindings([consumer] + providers)

    def test_governed_literal_true_without_policy_from_fails_closed(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
        }
        diags = self._run(consume, {"db": {"contractRef": "pg.sql"}}, [_governed_provider(True)])
        self.assertEqual([d.code for d in diags], ["E167"])
        self.assertIn("policyFrom", diags[0].message)
        self.assertIn("pg.sql", diags[0].message)

    def test_governed_true_with_policy_binding_passes(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        diags = self._run(
            consume,
            {
                "db": {"contractRef": "pg.sql"},
                "dbPolicy": {"contractRef": "policy.data-access-policy"},
            },
            [_governed_provider(True), _policy_provider()],
        )
        self.assertEqual(diags, [])

    def test_governed_via_config_reference(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        provider = _governed_provider(None)
        provider["module"]["spec"]["provides"][0]["contract"]["spec"]["governed"] = (
            "${config.governed}"
        )
        provider["module"]["spec"]["configSchema"] = {
            "type": "object",
            "properties": {"governed": {"type": "boolean", "default": False}},
        }
        # governed not set in the producer profile config -> schema default false
        diags = self._run(consume, {"db": {"contractRef": "pg.sql"}}, [provider])
        self.assertEqual(diags, [])
        # explicitly true -> E167 without a policy binding
        provider["config"] = {"governed": True}
        diags = self._run(consume, {"db": {"contractRef": "pg.sql"}}, [provider])
        self.assertEqual([d.code for d in diags], ["E167"])

    def test_governed_via_config_reference_with_schema_default_true(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
        }
        provider = _governed_provider(None)
        provider["module"]["spec"]["provides"][0]["contract"]["spec"]["governed"] = (
            "${config.governed}"
        )
        provider["module"]["spec"]["configSchema"] = {
            "type": "object",
            "properties": {"governed": {"type": "boolean", "default": True}},
        }
        diags = self._run(consume, {"db": {"contractRef": "pg.sql"}}, [provider])
        self.assertEqual([d.code for d in diags], ["E167"])

    def test_ungoverned_contract_needs_no_policy_binding(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        # provider does not advertise governed at all; consumer config leaves
        # the optional policy field unset
        diags = self._run(consume, {"db": {"contractRef": "pg.sql"}}, [_governed_provider(None)])
        self.assertEqual(diags, [])

    def test_policy_binding_to_wrong_kind_is_e042(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        cache = _inst(
            "cache",
            _module(
                provides=[{"name": "cache", "contract": {"kind": "cache-service"}}],
                category="cache",
                name="keydb",
            ),
            index=2,
        )
        diags = self._run(
            consume,
            {
                "db": {"contractRef": "pg.sql"},
                "dbPolicy": {"contractRef": "cache.cache"},
            },
            [_governed_provider(True), cache],
        )
        self.assertEqual([d.code for d in diags], ["E042"])
        self.assertIn("data-access-policy", diags[0].message)

    def test_policy_ref_to_unknown_module_is_e041(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        diags = self._run(
            consume,
            {
                "db": {"contractRef": "pg.sql"},
                "dbPolicy": {"contractRef": "nope.policy"},
            },
            [_governed_provider(True)],
        )
        self.assertEqual([d.code for d in diags], ["E041"])

    def test_malformed_policy_ref_is_e041(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        diags = self._run(
            consume,
            {
                "db": {"contractRef": "pg.sql"},
                "dbPolicy": {"contractRef": "not-a-ref"},
            },
            [_governed_provider(True), _policy_provider()],
        )
        self.assertEqual([d.code for d in diags], ["E041"])

    def test_policy_from_resolving_to_non_object_is_loud(self) -> None:
        consume = {
            "name": "db",
            "contract": {"kind": "sql-database"},
            "required": True,
            "mappedFrom": "spec.config.db",
            "policyFrom": "spec.config.dbPolicy",
        }
        # ungoverned, but the declared policyFrom resolves to a non-binding:
        # still a hard error so a typo cannot silently disable the wiring
        diags = self._run(
            consume,
            {"db": {"contractRef": "pg.sql"}, "dbPolicy": "oops"},
            [_governed_provider(False), _policy_provider()],
        )
        self.assertEqual([d.code for d in diags], ["E041"])


class RenderCrossCheckCandidatesTest(unittest.TestCase):
    """The provided data-access-policy contract is host/port cross-checked
    like the other endpoint contracts (E150/E164, issues #1010/#1245)."""

    def test_host_candidates_cover_host_and_decision_uri(self) -> None:
        spec = {
            "host": "policy-opa",
            "port": 8181,
            "decisionUri": "http://policy-opa:8181/v1/data",
        }
        candidates = _provided_contract_host_candidates("data-access-policy", spec, "policy")
        fields = [f for f, _ in candidates]
        self.assertEqual(fields, ["host", "decisionUri"])

    def test_port_candidates_cover_port_field_and_decision_uri(self) -> None:
        spec = {
            "host": "policy-opa",
            "port": 8181,
            "decisionUri": "http://policy-opa:8181/v1/data",
        }
        candidates = _provided_contract_port_candidates("data-access-policy", spec, "policy")
        by_port_field = {port_field: port for _, _, port_field, port in candidates}
        self.assertEqual(by_port_field, {"port": 8181, "decisionUri": 8181})


class OpaModuleShapeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.module = yaml.safe_load(OPA_MODULE_PATH.read_text(encoding="utf-8"))

    def test_module_is_schema_valid(self) -> None:
        errors = list(Draft202012Validator(MODULE_SCHEMA).iter_errors(self.module))
        self.assertEqual(errors, [])

    def test_module_metadata_and_contract(self) -> None:
        self.assertFalse(self.module["metadata"]["productionSuitable"])
        self.assertEqual(self.module["metadata"]["category"], "security")
        self.assertEqual(self.module["metadata"]["name"], "opa")
        provides = {p["name"]: p["contract"] for p in self.module["spec"]["provides"]}
        self.assertEqual(list(provides), ["data-access-policy"])
        spec = provides["data-access-policy"]["spec"]
        self.assertEqual(provides["data-access-policy"]["kind"], "data-access-policy")
        self.assertEqual(spec["port"], 8181)
        self.assertEqual(spec["decisionPath"], "/v1/data")
        self.assertEqual(spec["decisionUri"], "http://${service.dns}:8181/v1/data")

    def test_image_is_digest_pinned_and_policies_dir_is_mounted(self) -> None:
        services = self.module["spec"]["implementation"]["compose"]["services"]
        service = services["opa"]
        self.assertIn("@sha256:", service["image"])
        self.assertTrue(service["image"].startswith("openpolicyagent/opa:"))
        volumes = service["volumes"]
        bind = next(v for v in volumes if v.get("type") == "bind")
        self.assertEqual(bind["source"], "${config.policiesDir}")
        self.assertEqual(bind["target"], "/policies")
        self.assertTrue(bind["read_only"])
        # host publish is loopback-only
        self.assertEqual(service["ports"], ["127.0.0.1:${config.apiPort}:8181"])
        # container hardening
        self.assertTrue(service["read_only"])
        self.assertIn("ALL", service["cap_drop"])
        self.assertEqual(service["user"], "1000:1000")


def _demo_profile_dict() -> dict:
    profile = yaml.safe_load(DEMO_PROFILE_PATH.read_text(encoding="utf-8"))
    # resolve module sources against CDS_MODULE_PATH for the tmp copy
    for module in profile["spec"]["modules"]:
        module["source"] = module["source"][len("../../") :]
    return profile


def _write_profile(profile: dict, tmp: str) -> Path:
    path = Path(tmp) / "profile.yaml"
    path.write_text(yaml.safe_dump(profile), encoding="utf-8")
    return path


class GovernedProfileEndToEndTest(unittest.TestCase):
    """Full validate -> plan -> render over the real modules with
    CDS_MODULE_PATH=repo root (same pattern as test_otel_collector_module)."""

    def _validate(self, profile: dict) -> list:
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_profile(profile, tmp)
            with unittest.mock.patch.dict(os.environ, _TEST_ENV, clear=False):
                return validate_profile(str(path))

    def _plan_and_render(self, profile: dict):
        with tempfile.TemporaryDirectory() as tmp:
            path = _write_profile(profile, tmp)
            with unittest.mock.patch.dict(os.environ, _TEST_ENV, clear=False):
                diags = validate_profile(str(path))
                self.assertEqual(
                    [d.format() for d in diags if d.level == "error"], [],
                    "validate must pass before plan/render",
                )
                plan, plan_diags = build_plan(str(path))
        self.assertEqual([d.format() for d in plan_diags if d.level == "error"], [])
        self.assertIsNotNone(plan)
        with unittest.mock.patch.dict(os.environ, _TEST_ENV, clear=False):
            compose_text, render_diags = render_compose(plan)
        self.assertEqual([d.format() for d in render_diags if d.level == "error"], [])
        return plan, yaml.safe_load(compose_text)

    def test_shipped_demo_profile_validates(self) -> None:
        # the shipped profile uses ../../-relative sources; validate in place
        diags = validate_profile(str(DEMO_PROFILE_PATH))
        self.assertEqual([d.format() for d in diags if d.level == "error"], [])

    def test_governed_profile_plans_and_renders_policy_wiring(self) -> None:
        plan, compose = self._plan_and_render(_demo_profile_dict())

        superset = next(m for m in plan["modules"] if m["id"] == "superset")
        consumes = superset["consumes"]
        self.assertIn("analytics-database-policy", consumes)
        self.assertIn("metadata-database-policy", consumes)
        policy_contract = consumes["analytics-database-policy"]["contract"]
        self.assertEqual(policy_contract["kind"], "data-access-policy")
        self.assertEqual(
            policy_contract["spec"]["decisionUri"], "http://policy-opa:8181/v1/data"
        )

        opa_service = compose["services"]["policy-opa"]
        self.assertIn("@sha256:", opa_service["image"])
        self.assertEqual(opa_service["ports"], ["127.0.0.1:8181:8181"])
        bind = next(v for v in opa_service["volumes"] if v.get("type") == "bind")
        self.assertTrue(bind["source"].endswith("policies"))
        self.assertEqual(bind["target"], "/policies")
        self.assertTrue(bind["read_only"])

        superset_service = compose["services"]["superset"]
        env = superset_service["environment"]
        self.assertEqual(
            env["CDS_ANALYTICS_DB_POLICY_DECISION_URI"], "http://policy-opa:8181/v1/data"
        )
        self.assertEqual(
            env["CDS_METADATA_DB_POLICY_DECISION_URI"], "http://policy-opa:8181/v1/data"
        )

    def test_missing_policy_binding_fails_closed_with_e167(self) -> None:
        profile = _demo_profile_dict()
        superset = next(m for m in profile["spec"]["modules"] if m["id"] == "superset")
        del superset["config"]["analyticsDataPolicy"]
        diags = self._validate(profile)
        errors = [d for d in diags if d.level == "error"]
        self.assertIn("E167", [d.code for d in errors])
        e167 = next(d for d in errors if d.code == "E167")
        self.assertIn("analytics-database", e167.message)
        self.assertIn("postgres.sql-database", e167.message)

    def test_policy_binding_to_wrong_kind_is_e042_end_to_end(self) -> None:
        profile = _demo_profile_dict()
        superset = next(m for m in profile["spec"]["modules"] if m["id"] == "superset")
        superset["config"]["analyticsDataPolicy"] = {"contractRef": "keydb.cache-service"}
        diags = self._validate(profile)
        errors = [d for d in diags if d.level == "error"]
        self.assertEqual([d.code for d in errors], ["E042"])

    def test_ungoverned_provider_needs_no_policy_binding(self) -> None:
        profile = _demo_profile_dict()
        postgres = next(m for m in profile["spec"]["modules"] if m["id"] == "postgres")
        postgres["config"]["governed"] = False
        superset = next(m for m in profile["spec"]["modules"] if m["id"] == "superset")
        del superset["config"]["analyticsDataPolicy"]
        del superset["config"]["metadataDataPolicy"]
        diags = self._validate(profile)
        self.assertEqual([d.format() for d in diags if d.level == "error"], [])
        # and the policy env renders empty (no binding resolved)
        plan, compose = self._plan_and_render(profile)
        superset_module = next(m for m in plan["modules"] if m["id"] == "superset")
        self.assertNotIn("analytics-database-policy", superset_module["consumes"])
        env = compose["services"]["superset"]["environment"]
        self.assertEqual(env["CDS_ANALYTICS_DB_POLICY_DECISION_URI"], "")


if __name__ == "__main__":
    unittest.main()
