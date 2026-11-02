"""Build the portal-importable workbook using only the Python standard library."""

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent
ACCOUNT_TYPE = "microsoft.cognitiveservices/accounts"
WORKSPACE_TYPE = "microsoft.operationalinsights/workspaces"
SCHEMA = "https://github.com/Microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json"


def parameter(name, kind, **settings):
    return {
        "id": name,
        "version": "KqlParameterItem/1.0",
        "name": name,
        "type": kind,
        **settings,
    }


def resource_parameter(name, resource_type, query, scope):
    return parameter(
        name, 5, label=name, isRequired=True, multiSelect=True, quote="'",
        delimiter=",", query=query, queryType=1,
        resourceType="microsoft.resourcegraph/resources",
        crossComponentResources=[scope],
        typeSettings={
            "resourceTypeFilter": {resource_type: True},
            "additionalResourceOptions": ["value::all"],
            "showDefault": False,
        },
    )


def parameter_step(name, parameters):
    return {
        "type": 9, "name": name,
        "content": {
            "version": "KqlParameterItem/1.0",
            "parameters": parameters, "style": "above",
        },
    }


def text(name, markdown):
    return {"type": 1, "name": name, "content": {"json": markdown}}


def query_step(name, title, query, visualization="table", query_type=0, **settings):
    content = {
        "version": "KqlItem/1.0", "title": title, "query": query,
        "size": 0, "queryType": query_type, "visualization": visualization,
        "showExportToExcel": True, **settings,
    }
    if query_type == 0:
        content.update({
            "resourceType": WORKSPACE_TYPE,
            "crossComponentResources": ["{Workspaces}"],
            "timeContextFromParameter": "TimeRange",
        })
    return {"type": 3, "name": name, "content": content}


def read_query(name):
    return (ROOT / "queries" / f"{name}.kql").read_text(encoding="utf-8")


def stats_query(group_key):
    return read_query("usage") + read_query("statistics").replace("__GROUP_KEY__", group_key)


def metric_step(name, title, metric, category, model_dimension, aggregation=1, filters=None):
    return {
        "type": 10, "name": name,
        "content": {
            "version": "MetricsItem/2.0", "chartId": name, "title": title,
            "size": 0, "chartType": 0, "resourceType": ACCOUNT_TYPE,
            "resourceIds": ["{Accounts}"], "resourceParameter": "Accounts",
            "resourceLimit": 100, "timeContext": {"durationMs": 0},
            "timeContextFromParameter": "TimeRange",
            "metrics": [{
                "namespace": ACCOUNT_TYPE,
                "metric": f"{ACCOUNT_TYPE}-{category}-{metric}",
                "aggregation": aggregation, "splitBy": ["ModelDeploymentName"],
                "splitByLimit": 100,
                "filters": [{
                    "key": model_dimension, "operator": 0,
                    "values": [], "valueParam": "Models",
                }, *(filters or [])],
            }],
            "showExportToExcel": True,
        },
    }


def build():
    subscriptions_query = (
        "ResourceContainers | where type =~ 'microsoft.resources/subscriptions'\n"
        "| project value=subscriptionId, label=name, selected=true | order by label asc"
    )
    subscriptions = parameter(
        "Subscriptions", 6, isRequired=True, multiSelect=True, quote="'", delimiter=",",
        query="ResourceContainers | where type =~ 'microsoft.resources/subscriptions'\n"
              "| project value=subscriptionId, label=name | order by label asc",
        queryType=1, resourceType="microsoft.resourcegraph/resources",
        crossComponentResources=["value::all"],
        typeSettings={"additionalResourceOptions": [], "showDefault": False},
    )
    accounts = resource_parameter(
        "Accounts", ACCOUNT_TYPE,
        "Resources | where type =~ 'microsoft.cognitiveservices/accounts'\n"
        "| where kind in~ ('OpenAI', 'AIServices')\n"
        "| project value=id, label=strcat(name, ' / ', location, ' / ', subscriptionId)\n"
        "| order by label asc",
        "{Subscriptions}",
    )
    accounts["description"] = (
        "Select one or more Foundry / Azure OpenAI instances in the selected subscriptions."
    )
    accounts["label"] = "Foundry instances"
    discovery_subscriptions = parameter(
        "WorkspaceDiscoverySubscriptions", 6, isRequired=True, multiSelect=True,
        quote="'", delimiter=",", query=subscriptions_query, queryType=1,
        resourceType="microsoft.resourcegraph/resources", crossComponentResources=["value::all"],
        value=["value::all"], defaultValue="value::all", isHiddenWhenLocked=True,
        typeSettings={
            "additionalResourceOptions": ["value::all"], "includeAll": True,
            "showDefault": False,
        },
    )
    workspaces = resource_parameter(
        "Workspaces", WORKSPACE_TYPE,
        "Resources | where type =~ 'microsoft.operationalinsights/workspaces'\n"
        "| project value=id, label=strcat(name, ' / ', subscriptionId), selected=true | order by label asc",
        "{WorkspaceDiscoverySubscriptions}",
    )
    workspaces.update(isHiddenWhenLocked=True, value=["value::all"], defaultValue="value::all")
    workspaces["typeSettings"]["includeAll"] = True
    time_range = parameter(
        "TimeRange", 4, label="Time range", isRequired=True,
        value={"durationMs": 604800000},
        typeSettings={
            "allowCustom": True,
            "selectableValues": [{"durationMs": x * 86400000} for x in (1, 7, 14, 30)],
        },
    )
    category = parameter(
        "LogCategory", 2, label="Diagnostic usage source", isRequired=True,
        value="AzureOpenAIRequestUsage",
        jsonData=json.dumps([
            {"value": "AzureOpenAIRequestUsage", "label": "AzureOpenAIRequestUsage (preferred)"},
            {"value": "RequestResponse", "label": "RequestResponse (legacy)"},
        ]),
    )
    models_query = """
union isfuzzy=true
    (datatable(Model:string)["*"]),
    (AzureDiagnostics
     | where TimeGenerated {TimeRange}
     | where Category == '{LogCategory}'
     | extend AccountId=tolower(coalesce(_ResourceId, ResourceId)), p=parse_json(properties_s)
     | where AccountId in~ ({Accounts})
     | extend Model=iff(isempty(tostring(p.modelName)), "(unknown model)", tostring(p.modelName))
     | project Model)
| distinct Model
| project value=Model, label=iff(Model == "*", "All models", Model)
| order by label asc
""".strip()
    models = parameter(
        "Models", 2, label="Models", isRequired=True, multiSelect=True,
        quote="'", delimiter=",", value=["*"], query=models_query, queryType=0,
        resourceType=WORKSPACE_TYPE, crossComponentResources=["{Workspaces}"],
        timeContextFromParameter="TimeRange",
        typeSettings={"additionalResourceOptions": [], "showDefault": False},
    )
    headroom = parameter(
        "Headroom", 2, label="Demand multiplier", isRequired=True, value="1.3",
        jsonData=json.dumps([
            {"value": str(v), "label": f"{v:.2f}x observed TPM"} for v in (1.1, 1.2, 1.3, 1.5, 2.0)
        ]),
    )
    items = [
        text("title", "# Azure OpenAI deployment right-sizing\n"
             "Select subscriptions, Foundry instances and models. Diagnostic logs are queried across "
             "**all discovered workspaces**, including centralized workspaces in other subscriptions, "
             "and deduplicated using guarded request/correlation identity before charting. "
             "Uses **Azure Metrics + Diagnostic Settings**; no collector, API keys, or custom tables."),
        parameter_step("scope", [subscriptions, accounts, time_range, category]),
        parameter_step("workspace-discovery", [discovery_subscriptions, workspaces]),
        parameter_step("analysis-filters", [models, headroom]),
        text("method", "**Diagnostic analysis:** full UTC calendar minutes only; end exclusive. "
             "The latest five minutes are excluded for ingestion delay. Select at most 31 days. "
             "Statistics include unobserved minutes as zero **only under the assumption of continuous logging**. "
             "No rows is not evidence of an idle deployment. "
             "Conflicting copies or usage without an identifier in multi-workspace accounts block sizing. "
             "Correlation IDs are matched conservatively and may not uniquely identify requests. "
             "When copy timestamps differ, the earliest is used; check `CrossMinuteRequests`. "
             "Usage without a deployment is included in fleet/model totals but excluded from deployment sizing. "
             "Azure supports up to 100 workspaces per query; discovery/read permissions and service limits "
             "apply. Native metrics below use the original time selection."),
        query_step("quality", "Data quality and effective analysis window",
                   read_query("usage") + read_query("quality")),
    ]
    fleet = stats_query('"fleet"')
    items.append(query_step(
        "summary", "Observed tokens per calendar minute - selected fleet",
        fleet + """
| extend Metrics=pack_array(
    pack_array("Average TPM", AverageTPM), pack_array("P50 TPM", P50TPM),
    pack_array("P95 TPM", P95TPM), pack_array("P99 TPM", P99TPM),
    pack_array("Peak TPM", PeakTPM), pack_array("Observed tokens", ObservedTokens),
    pack_array("Calendar minutes", CalendarMinutes))
| mv-expand Metrics
| project Metric=tostring(Metrics[0]), Value=todouble(Metrics[1])
""",
        visualization="tiles",
        tileSettings={
            "titleContent": {"columnMatch": "Metric", "formatter": 1},
            "leftContent": {"columnMatch": "Value", "formatter": 12,
                            "formatOptions": {"palette": "auto"},
                            "numberFormat": {"unit": 0, "options": {
                                "style": "decimal", "useGrouping": True, "maximumFractionDigits": 2}}},
            "showBorder": True,
        },
    ))
    items.append(query_step(
        "trend", "Observed TPM - one-minute resolution, selected fleet",
        read_query("usage") + read_query("trend"), visualization="timechart",
        chartSettings={"xAxis": "Minute", "yAxis": ["ObservedTPM"]},
    ))
    items.append(query_step(
        "models-summary", "Model comparison - simultaneous traffic summed before percentiles",
        stats_query('tostring(pack_array(Model, Version))') + """
| extend Key=parse_json(GroupKey)
| project Model=tostring(Key[0]), Version=tostring(Key[1]), AverageTPM, P50TPM,
          P95TPM, P99TPM, PeakTPM, ObservedTokens, ActiveMinutes, CalendarMinutes
| order by PeakTPM desc
""",
    ))
    deployment_projection = """
| join kind=inner (
    Usage | summarize Models=make_set(Model, 100), Versions=make_set(Version, 100) by EntityKey
) on $left.GroupKey == $right.EntityKey
| extend Key=parse_json(GroupKey)
| where isnotempty(tostring(Key[1]))
| project AccountId=tostring(Key[0]), Deployment=tostring(Key[1]),
          Models, Versions,
          AverageTPM, P50TPM, P95TPM, P99TPM, PeakTPM, PeakRPM,
          P99WithHeadroomTPM, PeakWithHeadroomTPM, ObservedTokens, ActiveMinutes, CalendarMinutes
| order by PeakTPM desc
"""
    items.append(query_step(
        "deployment-summary", "Physical deployment demand and planning targets",
        stats_query("EntityKey") + deployment_projection,
        gridSettings={"filter": True, "rowLimit": 10000},
    ))
    items.extend([
        text("sizing-notes", "**Planning targets are not automatic resize recommendations.** "
             "`P99WithHeadroomTPM` and `PeakWithHeadroomTPM` multiply observed raw-token TPM by "
             "the selected demand multiplier. P99 can be zero for sparse workloads; check peak, RPM, "
             "429s, latency, expected growth and failover before reducing capacity. "
             "Limits are per deployment; fleet/model totals are not a fungible quota pool."),
        text("metrics-title", "## Native Azure Metrics\n"
             "These remain the service-side cross-check when diagnostic coverage is incomplete. "
             "Split by deployment; each account is queried separately. "
             "Chart totals are tokens **per displayed bucket**, not TPM when Azure selects a grain larger "
             "than one minute. Select PT1M in edit mode for a short window when comparing minute peaks. "
             "Up to 100 accounts and 100 series per metric are requested; narrow scope if Azure truncates "
             "or throttles. All models includes deployments not yet present in logs. "
             "PTU utilization is a different measure from raw-token TPM."),
        metric_step("native-tokens", "Processed inference tokens (sum per metric bucket)",
                    "TokenTransaction", "Azure OpenAI - Usage", "FeatureName"),
        metric_step("native-requests", "Requests (sum per metric bucket)",
                    "AzureOpenAIRequests", "Azure OpenAI - HTTP Requests", "ModelName"),
        metric_step("native-429", "Throttled requests - HTTP 429",
                    "AzureOpenAIRequests", "Azure OpenAI - HTTP Requests", "ModelName",
                    filters=[{"key": "StatusCode", "operator": 0, "values": ["429"]}]),
        metric_step("native-ptu", "PTU utilization V2 - maximum, not raw-token utilization",
                    "AzureOpenAIProvisionedManagedUtilizationV2", "Azure OpenAI - Usage",
                    "ModelName", aggregation=3),
        metric_step("native-latency", "Time to response (milliseconds, average)",
                    "AzureOpenAITimeToResponse", "Azure OpenAI - Latency",
                    "ModelName", aggregation=4),
        text("inventory-title", "## Live capacity drilldown\n"
             "Choose one account from the multi-account scope to compare observed demand with live "
             "deployment allocation. Inventory includes **all models** in this account. "
             "A blank demand value means no usable rows under the current model/category filters, "
             "**not zero use**. Inventory is current state, not a historical snapshot."),
    ])
    focus = parameter(
        "CapacityAccount", 5, label="Capacity drilldown account", isRequired=True,
        query="Resources | where type =~ 'microsoft.cognitiveservices/accounts'\n"
              "| where id in~ ({Accounts}) | order by name asc\n"
              "| project value=id, label=name",
        queryType=1, resourceType="microsoft.resourcegraph/resources",
        crossComponentResources=["{Subscriptions}"],
        defaultValue="value::1",
        typeSettings={"additionalResourceOptions": ["value::1"], "showDefault": False},
    )
    items.append(parameter_step("capacity-scope", [focus]))
    inventory = {
        "version": "ARMEndpoint/1.0", "method": "GETARRAY",
        "path": "{CapacityAccount}/deployments",
        "urlParams": [{"key": "api-version", "value": "2024-10-01"}],
        "transformers": [{
            "type": "jsonpath",
            "settings": {
                "tablePath": "$..[?(@.type == 'Microsoft.CognitiveServices/accounts/deployments')]",
                "columns": [
                    {"columnid": name, "path": path, "columnType": kind}
                    for name, path, kind in [
                        ("Deployment", "$.name", "string"),
                        ("ConfiguredModel", "$.properties.model.name", "string"),
                        ("ConfiguredVersion", "$.properties.model.version", "string"),
                        ("SKU", "$.sku.name", "string"),
                        ("CapacityUnits", "$.sku.capacity", "real"),
                        ("AllocatedTPM", "$.properties.rateLimits[?(@.key == 'token' && @.renewalPeriod == 60)].count", "real"),
                        ("RequestLimit", "$.properties.rateLimits[?(@.key == 'request')].count", "real"),
                        ("RequestWindowSeconds", "$.properties.rateLimits[?(@.key == 'request')].renewalPeriod", "real"),
                        ("ProvisioningState", "$.properties.provisioningState", "string"),
                    ]
                ],
            },
        }],
    }
    items.append(query_step("inventory", "Current deployment allocation - selected account, all models",
                            json.dumps(inventory), query_type=12))
    focus_query = stats_query("EntityKey") + deployment_projection + """
| where AccountId =~ '{CapacityAccount}'
| project Deployment, AverageTPM, PeakTPM, P99WithHeadroomTPM, PeakWithHeadroomTPM,
          VersionsInWindow=array_length(Versions)
"""
    items.append(query_step("focus-demand", "Selected account diagnostic demand", focus_query))
    merge = {
        "version": "Merge/1.0",
        "merges": [{
            "id": "capacity-demand", "mergeType": "leftouter",
            "leftTable": "inventory", "rightTable": "focus-demand",
            "leftColumn": "Deployment", "rightColumn": "Deployment",
        }],
        "projectRename": [
            {"originalName": f"[{table}].{column}", "mergedName": column,
             "fromId": "capacity-demand"}
            for table, columns in [
                ("inventory", ["Deployment", "ConfiguredModel", "ConfiguredVersion", "SKU",
                               "CapacityUnits", "AllocatedTPM"]),
                ("focus-demand", ["AverageTPM", "P99WithHeadroomTPM", "PeakWithHeadroomTPM",
                                  "VersionsInWindow"]),
            ]
            for column in columns
        ],
    }
    items.extend([
        query_step("capacity-comparison", "Allocation vs observed planning targets",
                   json.dumps(merge), query_type=7),
        text("capacity-notes", "`AllocatedTPM` comes from the explicit token rate limit with a "
             "60-second renewal period; missing limits stay blank. `CapacityUnits` are not universally "
             "TPM: provisioned SKUs use PTUs. Request limits may have shorter renewal windows; read "
             "`RequestWindowSeconds` before comparing with RPM. "
             "Traffic across model versions is combined for each physical deployment; "
             "historical allocations are not reconstructed. "
             "Do not interpret the ratio of observed tokens to allocation as Azure admission-rate utilization. "
             "Validate PTU changes with utilization V2, latency and load tests; no generic TPM-to-PTU conversion is applied."),
    ])
    return {"version": "Notebook/1.0", "items": items, "$schema": SCHEMA}


if __name__ == "__main__":
    output = ROOT / "AzureOpenAI-RightSizing.workbook"
    output.write_text(json.dumps(build(), indent=2) + "\n", encoding="utf-8")
    print(output)
