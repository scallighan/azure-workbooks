"""Local workbook checks and read-only numerical regression tests on Azure's KQL engine."""

import datetime
import json
import os
import re
import subprocess
import unittest
import uuid

from build_workbook import build, read_query, stats_query

WORKSPACE = os.environ.get("LOG_ANALYTICS_WORKSPACE_ID")
SUBSCRIPTION = "11111111-1111-1111-1111-111111111111"
ACCOUNT = f"/subscriptions/{SUBSCRIPTION}/resourceGroups/test/providers/Microsoft.CognitiveServices/accounts/a"
SECOND_ACCOUNT = ACCOUNT[:-1] + "b"


def event(minute=0, second=0, account=ACCOUNT, workspace="workspace-a",
          category="AzureOpenAIRequestUsage", correlation_id="", **properties):
    p = {
        "modelDeploymentName": "shared-name", "modelName": "model-a", "modelVersion": "1",
        "promptTokens": [10], "generatedTokens": [0], "requestId": str(uuid.uuid4()),
        **properties,
    }
    return {
        "TimeGenerated": (
            datetime.datetime(2026, 1, 1) + datetime.timedelta(minutes=minute, seconds=second)
        ).isoformat() + "Z",
        "ResourceProvider": "MICROSOFT.COGNITIVESERVICES",
        "_ResourceId": account, "ResourceId": account,
        "Category": category, "properties_s": json.dumps(p), "TenantId": workspace,
        "CorrelationId": correlation_id,
    }


def substitute(query, overrides=None):
    values = {
        "TimeRange:startISO": "2026-01-01T00:00:00Z",
        "TimeRange:endISO": "2026-01-01T01:40:00Z",
        "Subscriptions": repr(SUBSCRIPTION), "Accounts": f"'{ACCOUNT}','{SECOND_ACCOUNT}'",
        "Workspaces": "'workspace-a','workspace-b'",
        "Models": "'*'", "Headroom": "1.3", "LogCategory": "AzureOpenAIRequestUsage",
        **(overrides or {}),
    }
    return re.sub(r"\{([^{}]+)\}", lambda m: values.get(m[1], m[0]), query)


class WorkbookScopeTests(unittest.TestCase):
    def setUp(self):
        self.items = {item["name"]: item["content"] for item in build()["items"]}
        self.parameters = {
            p["name"]: p
            for content in self.items.values()
            for p in content.get("parameters", [])
        }

    def test_subscriptions_instances_and_models_support_multiple_selection(self):
        for name in ("Subscriptions", "Accounts", "Models"):
            with self.subTest(parameter=name):
                picker = self.parameters[name]
                self.assertTrue(picker["multiSelect"])
                self.assertFalse(picker.get("isHiddenWhenLocked", False))
                self.assertEqual((picker["quote"], picker["delimiter"]), ("'", ","))
        self.assertEqual(self.parameters["Accounts"]["label"], "Foundry instances")
        self.assertEqual(self.parameters["Accounts"]["crossComponentResources"], ["{Subscriptions}"])
        self.assertIn("kind in~ ('OpenAI', 'AIServices')", self.parameters["Accounts"]["query"])

    def test_workspaces_are_automatically_selected_across_subscriptions(self):
        subscriptions = self.parameters["WorkspaceDiscoverySubscriptions"]
        self.assertTrue(subscriptions["isHiddenWhenLocked"])
        self.assertEqual(subscriptions["value"], ["value::all"])
        self.assertEqual(subscriptions["defaultValue"], "value::all")
        self.assertIn("selected=true", subscriptions["query"])
        picker = self.parameters["Workspaces"]
        self.assertTrue(picker["isHiddenWhenLocked"])
        self.assertTrue(picker["multiSelect"])
        self.assertEqual(picker["value"], ["value::all"])
        self.assertEqual(picker["defaultValue"], "value::all")
        self.assertTrue(picker["typeSettings"]["includeAll"])
        self.assertIn("selected=true", picker["query"])
        self.assertIn("value::all", picker["typeSettings"]["additionalResourceOptions"])
        self.assertNotIn("selectAllValue", picker["typeSettings"])
        self.assertEqual(picker["crossComponentResources"], ["{WorkspaceDiscoverySubscriptions}"])
        self.assertNotIn("{Subscriptions}", picker["query"])

    def test_multiple_resources_are_bound_to_logs_and_metrics(self):
        for name, content in self.items.items():
            with self.subTest(panel=name):
                if content.get("queryType") == 0:
                    self.assertEqual(content["crossComponentResources"], ["{Workspaces}"])
                if content.get("version") == "MetricsItem/2.0":
                    self.assertEqual(content["resourceIds"], ["{Accounts}"])
                    self.assertEqual(content["resourceParameter"], "Accounts")
        self.assertEqual(self.parameters["Models"]["crossComponentResources"], ["{Workspaces}"])
        self.assertIn("AccountId in~ ({Accounts})", read_query("usage"))
        self.assertNotIn("SubscriptionId in~ ({Subscriptions})", read_query("usage"))

    def test_capacity_drilldown_stays_single_account(self):
        picker = self.parameters["CapacityAccount"]
        self.assertFalse(picker.get("multiSelect", False))
        self.assertNotIn("value::all", picker["typeSettings"]["additionalResourceOptions"])
        inventory = json.loads(self.items["inventory"]["query"])
        self.assertEqual(inventory["path"], "{CapacityAccount}/deployments")


@unittest.skipUnless(WORKSPACE, "Set LOG_ANALYTICS_WORKSPACE_ID to run read-only KQL tests.")
class QueryTests(unittest.TestCase):
    def execute(self, query, events, overrides=None):
        source = (
            "let Source=print Events=dynamic(" + json.dumps(events) + ");\n"
            "let AzureDiagnostics=Source | mv-expand Event=Events | project "
            "TimeGenerated=todatetime(Event.TimeGenerated), ResourceProvider=tostring(Event.ResourceProvider), "
            "_ResourceId=tostring(Event._ResourceId), ResourceId=tostring(Event.ResourceId), "
            "Category=tostring(Event.Category), properties_s=tostring(Event.properties_s), "
            "TenantId=tostring(Event.TenantId), requestId_s=tostring(Event.requestId_s), "
            "CorrelationId=tostring(Event.CorrelationId), OperationName=tostring(Event.OperationName);\n"
        )
        command = [
            "az", "rest", "--method", "post",
            "--url", f"https://api.loganalytics.azure.com/v1/workspaces/{WORKSPACE}/query",
            "--resource", "https://api.loganalytics.io",
            "--body", json.dumps({"query": source + substitute(query, overrides)}),
            "--output", "json",
        ]
        result = subprocess.run(command, capture_output=True, text=True, check=False)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertNotIn("error", data, data)
        table = data["tables"][0]
        columns = [c["name"] for c in table["columns"]]
        return [dict(zip(columns, row)) for row in table["rows"]]

    def test_idle_zeros_and_exact_nearest_rank_percentiles(self):
        rows = [event(i, promptTokens=[value]) for i, value in enumerate((10, 20, 30, 40))]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["AverageTPM"], 1)
        self.assertEqual([result[k] for k in ("P50TPM", "P95TPM", "P99TPM", "PeakTPM")], [0, 0, 30, 40])
        self.assertEqual(result["P99WithHeadroomTPM"], 39)
        self.assertEqual(result["ObservedTokens"], 100)

    def test_sum_simultaneous_traffic_and_keep_physical_deployments_distinct(self):
        rows = [event(promptTokens=[100]), event(account=SECOND_ACCOUNT, promptTokens=[200])]
        fleet = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(fleet["PeakTPM"], 300)
        deployments = self.execute(stats_query("EntityKey"), rows)
        self.assertEqual(sorted(r["PeakTPM"] for r in deployments), [100, 200])

    def test_selected_accounts_across_subscriptions_are_included(self):
        other_account = ACCOUNT.replace(SUBSCRIPTION, "22222222-2222-2222-2222-222222222222")
        rows = [
            event(account=ACCOUNT, promptTokens=[10]),
            event(account=other_account, workspace="workspace-b", promptTokens=[20]),
            event(account=SECOND_ACCOUNT, promptTokens=[1000]),
        ]
        result = self.execute(stats_query('"fleet"'), rows, {
            "Accounts": f"'{ACCOUNT}','{other_account}'",
        })[0]
        self.assertEqual(result["ObservedTokens"], 30)

    def test_arrays_cached_tokens_and_versions(self):
        rows = [
            event(promptTokens=[10, 20], generatedTokens=[3, 4], cachedTokens=[25]),
            event(modelVersion="2", promptTokens=[100], generatedTokens=[5]),
        ]
        result = self.execute(stats_query("EntityKey"), rows)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["PeakTPM"], 142)

    def test_scalar_legacy_source_is_not_added_to_usage_source(self):
        rows = [
            event(promptTokens=[20], generatedTokens=[5]),
            event(category="RequestResponse", promptTokens=20, completionTokens=5),
        ]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        legacy = self.execute(stats_query('"fleet"'), rows, {"LogCategory": "RequestResponse"})[0]
        self.assertEqual(result["ObservedTokens"], 25)
        self.assertEqual(legacy["ObservedTokens"], 25)

    def test_end_exclusive_and_partial_minutes_excluded(self):
        rows = [event(0, promptTokens=[999]), event(1, promptTokens=[10]), event(2, promptTokens=[999])]
        result = self.execute(stats_query('"fleet"'), rows, {
            "TimeRange:startISO": "2026-01-01T00:00:30Z",
            "TimeRange:endISO": "2026-01-01T00:02:30Z",
        })[0]
        self.assertEqual(result["CalendarMinutes"], 1)
        self.assertEqual(result["AverageTPM"], 10)

    def test_missing_telemetry_does_not_create_zero_demand(self):
        result = self.execute(stats_query('"fleet"'), [])
        self.assertEqual(result, [])
        quality = self.execute(read_query("usage") + read_query("quality"), [])[0]
        self.assertTrue(quality["Status"].startswith("NO USAGE DATA:"))

    def test_duplicate_routing_is_removed_before_tpm_and_rpm_statistics(self):
        first = event(requestId="request-1")
        second = event(requestId="request-2", promptTokens=[20])
        rows = [
            first, {**first, "TenantId": "workspace-b"}, first,
            second, {**second, "TenantId": "workspace-b"},
        ]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 30)
        self.assertEqual(result["PeakTPM"], 30)
        self.assertEqual(result["PeakRPM"], 2)
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertTrue(quality["Status"].startswith("CONDITIONAL:"))
        self.assertEqual(quality["DuplicateRowsRemoved"], 3)
        self.assertEqual(quality["UsableRows"], 2)
        self.assertEqual(quality["AccountsInMultipleWorkspaces"], 1)

    def test_distinct_requests_and_accounts_with_identical_usage_are_preserved(self):
        rows = [
            event(requestId="request-1"),
            event(requestId="request-2", workspace="workspace-b"),
            event(requestId="request-1", account=SECOND_ACCOUNT),
            event(requestId="request-1", modelDeploymentName="another-deployment"),
        ]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 40)
        self.assertEqual(result["PeakRPM"], 4)

    def test_top_level_request_id_is_used_when_properties_id_is_missing(self):
        row = {**event(requestId=None), "requestId_s": "request-1"}
        result = self.execute(stats_query('"fleet"'), [row, {**row, "TenantId": "workspace-b"}])[0]
        self.assertEqual(result["ObservedTokens"], 10)
        self.assertEqual(result["PeakRPM"], 1)

    def test_correlation_id_deduplicates_matching_copies_with_timestamp_drift(self):
        first = event(second=55, requestId=None, correlation_id="correlation-1")
        second = event(minute=1, second=5, workspace="workspace-b",
                       requestId=None, correlation_id="CORRELATION-1")
        rows = [first, second]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 10)
        self.assertEqual(result["PeakRPM"], 1)
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertEqual(quality["SelectedWorkspaces"], 2)
        self.assertEqual(quality["DuplicateRowsRemoved"], 1)
        self.assertEqual(quality["TimestampShiftedRequests"], 1)
        self.assertEqual(quality["CrossMinuteRequests"], 1)
        trend = self.execute(read_query("usage") + read_query("trend"), rows)
        self.assertEqual(trend[0]["ObservedTPM"], 10)
        self.assertEqual(trend[1]["ObservedTPM"], 0)

    def test_reused_correlation_id_in_one_workspace_blocks_sizing(self):
        rows = [
            event(requestId=None, correlation_id="same"),
            event(second=1, requestId=None, correlation_id="same"),
            event(second=2, workspace="workspace-b", requestId=None, correlation_id="same"),
        ]
        self.assertEqual(self.execute(stats_query('"fleet"'), rows), [])
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertTrue(quality["Status"].startswith("BLOCKED: an identifier"))
        self.assertEqual(quality["ReusedIdsInWorkspace"], 1)

    def test_zero_correlation_id_is_not_treated_as_a_request_identifier(self):
        rows = [
            event(requestId=None, correlation_id="00000000-0000-0000-0000-000000000000"),
            event(workspace="workspace-b", requestId=None, correlation_id="00000000-0000-0000-0000-000000000000"),
        ]
        self.assertEqual(self.execute(stats_query('"fleet"'), rows), [])
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertEqual(quality["AmbiguousRowsWithoutId"], 2)

    def test_missing_request_ids_are_preserved_in_a_single_workspace(self):
        rows = [event(requestId=None), event(requestId=None)]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 20)
        self.assertEqual(result["PeakRPM"], 2)

    def test_missing_request_ids_block_ambiguous_multi_workspace_usage(self):
        rows = [event(requestId=None), event(workspace="workspace-b")]
        self.assertEqual(self.execute(stats_query('"fleet"'), rows), [])
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertTrue(quality["Status"].startswith("BLOCKED: usage without a request or correlation ID"))
        self.assertEqual(quality["AmbiguousRowsWithoutId"], 1)
        # An unrelated account's workspace must not make these rows ambiguous.
        rows[1] = event(account=SECOND_ACCOUNT, workspace="workspace-b")
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 20)

    def test_conflicting_copies_block_sizing_instead_of_picking_arbitrary_usage(self):
        for change in (
            {"promptTokens": [999]}, {"modelName": "model-b"}, {"modelVersion": "2"},
            {"minute": 1},
        ):
            with self.subTest(change=change):
                rows = [
                    event(requestId="request-1"),
                    event(requestId="request-1", workspace="workspace-b", **change),
                ]
                self.assertEqual(self.execute(stats_query('"fleet"'), rows), [])
                quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
                self.assertTrue(quality["Status"].startswith("BLOCKED: copies"))
                self.assertEqual(quality["ConflictingRequests"], 1)

    def test_bad_tokens_and_oversized_windows_are_explicitly_blocked(self):
        bad = [event(promptTokens="invalid")]
        self.assertEqual(self.execute(stats_query('"fleet"'), bad), [])
        quality = self.execute(read_query("usage") + read_query("quality"), bad)[0]
        self.assertEqual(quality["InvalidRows"], 1)
        quality = self.execute(read_query("usage") + read_query("quality"), [event()], {
            "TimeRange:endISO": "2026-03-01T00:00:00Z",
        })[0]
        self.assertTrue(quality["Status"].startswith("BLOCKED:"))

    def test_unattributed_usage_contributes_to_fleet_but_not_deployment_sizing(self):
        missing = event(modelDeploymentName="", promptTokens=[20], requestId=None, correlation_id="unknown")
        rows = [event(promptTokens=[10]), missing, {**missing, "TenantId": "workspace-b"}]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 30)
        self.assertEqual(result["PeakRPM"], 2)
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertTrue(quality["Status"].startswith("PARTIAL DEPLOYMENTS:"))
        self.assertEqual(quality["UnattributedRows"], 1)
        self.assertEqual(quality["UnattributedTokens"], 20)
        deployment_query = next(item["content"]["query"] for item in build()["items"]
                                if item["name"] == "deployment-summary")
        deployments = self.execute(deployment_query, rows)
        self.assertEqual(len(deployments), 1)
        self.assertEqual(deployments[0]["Deployment"], "shared-name")
        self.assertEqual(deployments[0]["ObservedTokens"], 10)

    def test_conflicting_deployment_identity_blocks_sizing(self):
        rows = [
            event(modelDeploymentName="", requestId="same"),
            event(modelDeploymentName="named", requestId="same", workspace="workspace-b"),
        ]
        self.assertEqual(self.execute(stats_query('"fleet"'), rows), [])
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertEqual(quality["DeploymentConflicts"], 1)

    def test_non_usage_events_do_not_create_deduplication_conflicts(self):
        rows = [
            event(requestId=None, correlation_id="shared"),
            event(minute=2, workspace="workspace-b", requestId=None, correlation_id="shared",
                  modelDeploymentName="", modelName="other", promptTokens=None, generatedTokens=None),
        ]
        result = self.execute(stats_query('"fleet"'), rows)[0]
        self.assertEqual(result["ObservedTokens"], 10)
        quality = self.execute(read_query("usage") + read_query("quality"), rows)[0]
        self.assertEqual(quality["RowsWithoutTokens"], 1)
        self.assertEqual(quality["AccountsInMultipleWorkspaces"], 0)
        self.assertEqual(quality["ConflictingRequests"], 0)
        self.assertEqual(quality["DeploymentConflicts"], 0)

    def test_model_filter_applies_before_aggregation(self):
        first = event(promptTokens=[10])
        rows = [
            first, {**first, "TenantId": "workspace-b"},
            event(modelName="model-b", account=SECOND_ACCOUNT, promptTokens=[20]),
            event(modelName="model-c", promptTokens=[1000]),
        ]
        result = self.execute(stats_query('"fleet"'), rows, {"Models": "'model-a','model-b'"})[0]
        self.assertEqual(result["ObservedTokens"], 30)

    def test_model_discovery_spans_workspaces_and_only_selected_instances(self):
        query = next(
            p["query"] for item in build()["items"]
            for p in item.get("content", {}).get("parameters", [])
            if p["name"] == "Models"
        )
        rows = [
            event(), event(modelName="model-b", account=SECOND_ACCOUNT, workspace="workspace-b"),
            event(modelName="unselected", account=ACCOUNT + "-unselected"),
        ]
        result = self.execute(query, rows, {
            "TimeRange": ">= datetime(2026-01-01) and TimeGenerated < datetime(2026-01-02)",
        })
        self.assertEqual({row["value"] for row in result}, {"*", "model-a", "model-b"})

    def test_all_log_visualizations_compile_with_synthetic_data(self):
        for item in build()["items"]:
            content = item.get("content", {})
            if content.get("queryType") == 0 and "query" in content:
                with self.subTest(panel=item["name"]):
                    self.execute(content["query"], [event()], {"CapacityAccount": ACCOUNT})


if __name__ == "__main__":
    unittest.main()
