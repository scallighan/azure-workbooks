# Azure OpenAI right-sizing workbook

Import [`AzureOpenAI-RightSizing.workbook`](AzureOpenAI-RightSizing.workbook) into
**Azure Monitor > Workbooks > New > Edit > Advanced Editor** (the `</>` button).
Replace the gallery-template JSON, **Apply**, select the parameters, then **Save**
as a new workbook. No collector, custom table, application deployment, or API key
is required. This does not change model allocations or an existing workbook.

## What customers see

- Select multiple subscriptions, Foundry / Azure OpenAI instances and models.
  Diagnostic workspaces are discovered and queried automatically across accessible
  subscriptions, including centralized workspaces outside the selected subscriptions.
- Request-level deduplication before diagnostic charting and statistics, with
  duplicate counts and ambiguous/conflicting telemetry reported in the quality panel.
- Average, P50, P95, P99 and peak observed TPM; total tokens; a one-minute trend.
- Model comparisons and per-physical-deployment planning targets with configurable
  headroom. Account resource ID is part of deployment identity.
- Native token, request, HTTP 429, response-time and PTU utilization V2 charts.
- Live ARM deployment inventory and allocation/demand comparison for one selected
  account at a time. The fleet analysis remains multi-account/multi-subscription.
- Excel export from query and metric panels.

## Prerequisites

Readers need read access to selected accounts (including metrics and deployments),
query access to the discovered Log Analytics workspaces, and access to the workbook.
Use your organization's Azure RBAC roles, typically Reader/Monitoring Reader and
Log Analytics Reader. Saving a shared workbook additionally requires workbook
write permission in its destination resource group.

For **each** monitored Azure OpenAI or Foundry `AIServices` instance, configure
**Diagnostic Settings > Send to Log Analytics workspace > AzureOpenAIRequestUsage**.
Logs can be routed to multiple workspaces; matching request copies are counted once. Existing
`RequestResponse` data is supported by explicitly changing **Diagnostic usage
source**, but many RequestResponse events do not contain token usage.
The workbook never adds the two categories together.

Select **Subscriptions**, then **Foundry instances**, then **Models**. Each allows
multiple selections. The Foundry instances picker includes `AIServices` and
`OpenAI` accounts in the selected subscriptions, with an **All** option.
There is no workspace selection step: a hidden resource parameter selects **All**
workspaces returned by tenant-wide Resource Graph discovery. Workspace discovery
is not restricted to the workload subscriptions, and resource visibility does not
guarantee permission to query a workspace. Discovery/query errors must be resolved
before interpreting the results. The capacity drilldown remains one account at a time.
Model choices come from the selected diagnostic source and time range; **All
models** also includes native metric series not present in logs. If a logged model
name is missing, it appears as `(unknown model)`; use All models for its native
metric comparison rather than trying to filter Azure metrics by that placeholder.

Diagnostic collection is prospective, incurs Azure logging/retention charges, and
does not backfill history. Wait for usage to arrive before analyzing it. This
workbook needs usage metadata only, not prompt/response bodies. `AllMetrics` export
to `AzureMetrics` is optional and is **not** the deployment-level data source:
standard diagnostic metric export drops dimensions.

## Interpretation and limitations

| Measure | Meaning |
|---|---|
| Observed TPM | Sum of logged prompt + generated/completion tokens in each UTC minute. Token arrays and legacy scalar values are supported. Cached tokens are already part of prompt tokens and are not added again. |
| Percentiles | Exact nearest-rank percentiles of minute totals, including unobserved zero minutes. Simultaneous traffic is summed **before** calculating fleet/model percentiles, never by summing deployment percentiles. |
| Analysis window | Complete minutes within the selection, end exclusive, excluding the most recent five minutes for ingestion delay. From 1 minute to 31 days; the effective window is displayed. Older ingestion can still arrive later. |
| Planning targets | P99 or peak observed TPM times the demand multiplier, rounded up. These are comparison inputs, **not approved allocations or automatic resize recommendations**. |
| Allocated TPM | Current ARM `properties.rateLimits` token count with `renewalPeriod=60`. It is not inferred by multiplying SKU capacity, and it is not the subscription-approved quota pool. Missing limits remain blank. |
| Capacity units | SKU-specific capacity; PTU capacity is **not TPM**. Use utilization V2 and workload benchmarks for PTU decisions. |

Unobserved minutes cannot be distinguished from logging outages using request logs
alone. Confirm that diagnostics were enabled throughout the window and compare
totals/shape with native metrics before acting. Empty telemetry never produces
zero-demand recommendations. The quality panel reports missing-account coverage;
malformed token rows or ambiguous/conflicting request copies block diagnostic
statistics rather than silently dropping/double-counting traffic.
An account with some logs can still have unlogged deployments; blank capacity
comparison cells do not establish that a deployment is idle. Usage without a
deployment name contributes to fleet and model totals but is excluded from
per-deployment planning; the quality panel reports its rows and tokens.

### Automatic workspace queries and deduplication

All diagnostic panels query the discovered workspace set and filter to the selected
subscriptions, instances and models before aggregating. Model choices are discovered
from the same workspace set and selected instances. Native Azure Metrics still query
the selected instances directly and are not affected by log routing duplicates.

Usage records are grouped by account resource ID, deployment and identifier:
`properties_s.requestId`, `requestId_s`, or the standard `CorrelationId` column.
Only token-bearing events participate in deduplication. Copies must agree on
operation, model, version and token counts, and their timestamps must be less than
one minute apart. If copies have different timestamps, the earliest is used;
`CrossMinuteRequests` shows how many fall into different minute buckets. An ID
with multiple timestamps within one workspace, conflicting usage, a timestamp
difference of at least one minute, or both missing and named deployments blocks
diagnostic statistics.

Azure defines `CorrelationId` as grouping related events, not as a guaranteed
unique request ID. The matching rules above reduce ambiguity but cannot eliminate
every possible collision; compare results with native metrics before sizing.
Usage without either identifier is preserved for an account with usage in one
workspace; it blocks sizing when the account has usage in multiple workspaces.
The quality panel reports selected workspaces, workspaces with logs, duplicate
rows removed, ambiguous identifiers, timestamp drift and unattributed usage.
If the workbook reports no data, check `SelectedWorkspaces`, access, the selected
diagnostic category and the time range; `RequestResponse` and
`AzureOpenAIRequestUsage` are not interchangeable.

Azure limits a cross-workspace query to **100 workspaces**. This workbook does not
batch larger workspace sets or silently select only the first 100. Large discovered
sets, missing query permissions and service throttling can cause query failures;
automatic discovery cannot retrieve data from undiscoverable or inaccessible
workspaces. Portal rendering and discovery completeness must be validated in the
target tenant.

The native metric charts use the **original** selected window, including recent
minutes, and Azure may choose a coarser grain for long ranges. Their token counts
are per displayed bucket, not necessarily TPM. To compare exact minute peaks,
use a short, fixed range ending at least five minutes ago and select PT1M in the
metric step editor. Narrow scope if the requested 100-account/100-series limits
or Azure service limits cause truncation, throttling or errors.

Deployment statistics combine selected traffic across model versions within the
same physical deployment. The live inventory can have changed since that traffic;
historical allocation changes, deleted deployments and creation times are not
reconstructed. Fleet totals across different models, SKUs and regions are not a
shared capacity pool. Prompt/output token metrics do not cover every audio or
realtime billing unit; use the relevant service metrics for those modalities.

For Standard/PAYG, check per-deployment peak demand, RPM, throttles, burstiness,
latency, growth and failover before changing allocation. Azure rate limiting uses
estimated admission tokens and short request windows, not merely observed
token/minute totals. A P99 of zero is valid for sparse traffic, but does **not**
justify removing capacity. No generic TPM-to-PTU or dollar-savings estimate is
applied.

## Development and validation

The checked-in workbook is generated from readable KQL and a standard-library
Python builder:

```bash
python build_workbook.py
```

Numerical tests execute synthetic data using Azure's actual KQL engine. They
require an authenticated `az` session and query permission on an existing
workspace; they ingest no records and modify no Azure resources:

```bash
LOG_ANALYTICS_WORKSPACE_ID='<workspace-customer-id-guid>' python -m unittest -v
```

The tests cover idle-minute percentiles, simultaneous traffic, duplicate deployment
names across accounts, multiple model versions, token arrays/scalars, category
separation, time boundaries, model filters, missing data, correlation-ID
deduplication, timestamp drift, missing deployments, distinct-request preservation,
non-usage events, missing IDs and conflicting copies.
An ordinary run without that environment variable skips the live tests.
Local tests still validate multi-select instance/model/subscription pickers,
automatic hidden all-workspace selection and downstream query/metric bindings.
Portal rendering and cross-tenant access still require validation in the customer's
Azure environment.

## References

- [Supported Azure OpenAI metrics](https://learn.microsoft.com/azure/azure-monitor/reference/supported-metrics/microsoft-cognitiveservices-accounts-metrics)
- [Supported diagnostic categories](https://learn.microsoft.com/azure/azure-monitor/reference/supported-logs/microsoft-cognitiveservices-accounts-logs)
- [Diagnostic Settings and metric export limitations](https://learn.microsoft.com/azure/azure-monitor/data-collection/diagnostic-settings)
- [Workbook data sources](https://learn.microsoft.com/azure/azure-monitor/visualize/workbooks-data-sources)
- [Cross-workspace queries and limits](https://learn.microsoft.com/azure/azure-monitor/logs/cross-workspace-query)
- [Workbook JSON schema](https://github.com/microsoft/Application-Insights-Workbooks/blob/master/schema/workbook.json)
