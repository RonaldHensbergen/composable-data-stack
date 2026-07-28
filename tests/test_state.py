import unittest

from cli.state import format_state_output, group_services_by_health, parse_compose_ps_json


class ParseComposePsJsonTest(unittest.TestCase):
    def test_parses_json_array_shape(self):
        # Compose pre-2.21.0 output.
        raw = '[{"Service": "web", "Health": "healthy"}, {"Service": "db", "Health": ""}]'
        self.assertEqual(
            parse_compose_ps_json(raw),
            [{"Service": "web", "Health": "healthy"}, {"Service": "db", "Health": ""}],
        )

    def test_parses_ndjson_shape(self):
        raw = (
            '{"Service": "web", "Health": "healthy"}\n'
            '{"Service": "db", "Health": ""}\n'
        )
        self.assertEqual(
            parse_compose_ps_json(raw),
            [{"Service": "web", "Health": "healthy"}, {"Service": "db", "Health": ""}],
        )

    def test_single_service_ndjson_line_with_no_wrapper(self):
        raw = '{"Service": "web", "Health": "healthy"}'
        self.assertEqual(parse_compose_ps_json(raw), [{"Service": "web", "Health": "healthy"}])

    def test_empty_output_returns_empty_list(self):
        self.assertEqual(parse_compose_ps_json(""), [])
        self.assertEqual(parse_compose_ps_json("   \n  "), [])

    def test_skips_blank_lines_in_ndjson(self):
        raw = '{"Service": "web", "Health": "healthy"}\n\n{"Service": "db", "Health": ""}\n'
        result = parse_compose_ps_json(raw)
        self.assertEqual(len(result), 2)

    def test_skips_malformed_lines_in_ndjson_rather_than_failing_the_whole_parse(self):
        raw = '{"Service": "web", "Health": "healthy"}\nnot json at all\n{"Service": "db", "Health": ""}\n'
        result = parse_compose_ps_json(raw)
        self.assertEqual([s["Service"] for s in result], ["web", "db"])


class GroupServicesByHealthTest(unittest.TestCase):
    """Fixtures use generic service names, no module- or provider-specific knowledge."""

    def test_buckets_by_health_when_present(self):
        services = [
            {"Service": "svc-a", "Health": "healthy", "State": "running"},
            {"Service": "svc-b", "Health": "unhealthy", "State": "running"},
            {"Service": "svc-c", "Health": "starting", "State": "running"},
        ]
        self.assertEqual(
            group_services_by_health(services),
            {
                "HEALTHY": ["svc-a"],
                "STARTING": ["svc-c"],
                "UNHEALTHY": ["svc-b"],
            },
        )

    def test_falls_back_to_state_when_health_is_empty(self):
        services = [{"Service": "svc-a", "Health": "", "State": "exited"}]
        self.assertEqual(group_services_by_health(services), {"EXITED": ["svc-a"]})

    def test_falls_back_to_unknown_when_both_health_and_state_are_missing(self):
        services = [{"Service": "svc-a"}]
        self.assertEqual(group_services_by_health(services), {"UNKNOWN": ["svc-a"]})

    def test_prefers_name_when_service_field_absent(self):
        services = [{"Name": "project-svc-a-1", "Health": "healthy"}]
        self.assertEqual(group_services_by_health(services), {"HEALTHY": ["project-svc-a-1"]})

    def test_output_is_deterministically_sorted_regardless_of_input_order(self):
        services = [
            {"Service": "zeta", "Health": "healthy"},
            {"Service": "alpha", "Health": "unhealthy"},
            {"Service": "beta", "Health": "healthy"},
        ]
        result = group_services_by_health(services)
        self.assertEqual(list(result.keys()), ["HEALTHY", "UNHEALTHY"])
        self.assertEqual(result["HEALTHY"], ["beta", "zeta"])

    def test_each_service_appears_once(self):
        services = [
            {"Service": "svc-a", "Health": "healthy"},
            {"Service": "svc-a", "Health": "healthy"},
        ]
        result = group_services_by_health(services)
        self.assertEqual(result["HEALTHY"], ["svc-a"])

    def test_empty_input_returns_empty_dict(self):
        self.assertEqual(group_services_by_health([]), {})


class FormatStateOutputTest(unittest.TestCase):
    def test_formats_buckets_and_services(self):
        grouped = {"HEALTHY": ["svc-a", "svc-b"], "EXITED": ["svc-c"]}
        output = format_state_output(grouped, use_color=False)
        self.assertEqual(
            output,
            "HEALTHY:\n  - svc-a\n  - svc-b\nEXITED:\n  - svc-c",
        )

    def test_no_ansi_codes_without_color(self):
        output = format_state_output({"HEALTHY": ["svc-a"]}, use_color=False)
        self.assertNotIn("\033", output)

    def test_ansi_codes_present_with_color(self):
        output = format_state_output({"HEALTHY": ["svc-a"]}, use_color=True)
        self.assertIn("\033", output)

    def test_empty_grouping_has_explicit_message(self):
        self.assertEqual(format_state_output({}, use_color=False), "No services found.")


if __name__ == "__main__":
    unittest.main()
