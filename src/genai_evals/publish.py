"""Publish eval scores next to the customer's existing alarms: CloudWatch (PutMetricData or EMF), Cloud Monitoring, OTLP."""
from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

NAMESPACE = "GenAIEvals"


def metric_points(result: dict[str, Any]) -> list[dict[str, Any]]:
    """One point per (target, grader[, prompt version]) with mean and pass rate."""
    pts = []
    for t, s in result["summary"].items():
        pv = ",".join(f"{k}@{v}" for k, v in (result.get("prompt_versions", {}).get(t) or {}).items()) or "none"
        for g, d in s["graders"].items():
            if d.get("mean") is None:
                continue
            pts.append({"suite": result["suite"], "target": t, "grader": g, "prompt_version": pv, "score": d["mean"],
                        "pass_rate": d.get("pass_rate"), "n": d["n"]})
    return pts


def to_emf(result: dict[str, Any]) -> list[str]:
    """CloudWatch Embedded Metric Format lines (stdout of Lambda / ECS, or the CloudWatch agent, turns them into metrics)."""
    lines = []
    ts = int(time.time() * 1000)
    for p in metric_points(result):
        lines.append(json.dumps({
            "_aws": {"Timestamp": ts, "CloudWatchMetrics": [{"Namespace": NAMESPACE, "Dimensions": [["Suite", "Target", "Grader"], ["Suite", "Target", "Grader", "PromptVersion"]],
                                                             "Metrics": [{"Name": "Score", "Unit": "None"}, {"Name": "PassRate", "Unit": "None"}]}]},
            "Suite": p["suite"], "Target": p["target"], "Grader": p["grader"], "PromptVersion": p["prompt_version"],
            "Score": p["score"], "PassRate": p["pass_rate"] if p["pass_rate"] is not None else p["score"], "Samples": p["n"]}))
    return lines


def publish_cloudwatch(result: dict[str, Any], region: str | None = None, client=None) -> int:
    import boto3
    c = client or boto3.client("cloudwatch", region_name=region)
    data = []
    for p in metric_points(result):
        dims = [{"Name": "Suite", "Value": p["suite"]}, {"Name": "Target", "Value": p["target"]}, {"Name": "Grader", "Value": p["grader"]}]
        data.append({"MetricName": "Score", "Dimensions": dims, "Value": p["score"], "Unit": "None"})
        data.append({"MetricName": "Score", "Dimensions": dims + [{"Name": "PromptVersion", "Value": p["prompt_version"]}], "Value": p["score"], "Unit": "None"})
        if p["pass_rate"] is not None:
            data.append({"MetricName": "PassRate", "Dimensions": dims, "Value": p["pass_rate"], "Unit": "None"})
    for i in range(0, len(data), 1000):
        c.put_metric_data(Namespace=NAMESPACE, MetricData=data[i:i + 1000])
    return len(data)


def publish_cloud_monitoring(result: dict[str, Any], project: str, client=None) -> int:
    from google.cloud import monitoring_v3  # type: ignore
    c = client or monitoring_v3.MetricServiceClient()
    now = datetime.now(timezone.utc)
    series = []
    for p in metric_points(result):
        for name, val in (("score", p["score"]), ("pass_rate", p["pass_rate"])):
            if val is None:
                continue
            ts = monitoring_v3.TimeSeries()
            ts.metric.type = f"custom.googleapis.com/genai_eval/{name}"
            ts.metric.labels.update({"suite": p["suite"][:60], "target": p["target"][:60], "grader": p["grader"][:60], "prompt_version": p["prompt_version"][:60]})
            ts.resource.type = "global"
            ts.resource.labels["project_id"] = project
            point = monitoring_v3.Point({"interval": {"end_time": {"seconds": int(now.timestamp())}}, "value": {"double_value": float(val)}})
            ts.points = [point]
            series.append(ts)
    for i in range(0, len(series), 200):
        c.create_time_series(name=f"projects/{project}", time_series=series[i:i + 200])
    return len(series)


def publish(result: dict[str, Any], targets: list[str], *, out_dir: str | Path = ".", region: str | None = None, project: str | None = None) -> dict[str, Any]:
    done: dict[str, Any] = {}
    for t in targets:
        if t == "emf":
            p = Path(out_dir) / "metrics.emf.jsonl"
            p.write_text("\n".join(to_emf(result)) + "\n", encoding="utf-8")
            done["emf"] = str(p)
        elif t == "cloudwatch":
            done["cloudwatch"] = publish_cloudwatch(result, region)
        elif t == "cloud_monitoring":
            if not project:
                raise ValueError("cloud_monitoring needs --project")
            done["cloud_monitoring"] = publish_cloud_monitoring(result, project)
        elif t == "otlp":
            from .otel import Telemetry
            Telemetry().record_run(result)
            done["otlp"] = "recorded"
        else:
            raise ValueError(f"unknown publish target {t}")
    return done
