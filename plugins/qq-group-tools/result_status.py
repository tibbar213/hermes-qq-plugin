"""Normalize native dispatch and inline-child results without inventing successful work."""
def task_result_status(payload):
    if not isinstance(payload, dict) or payload.get("error") or payload.get("failed") is True:
        return "failed"
    if payload.get("status") == "dispatched":
        return "running" if isinstance(payload.get("delegation_id"), str) and payload["delegation_id"].strip() else "failed"
    results = payload.get("results")
    if isinstance(results, list) and results:
        statuses = [task_result_status(row) for row in results]
        if all(s == "completed" for s in statuses):
            return "completed"
        return "interrupted" if all(s in {"completed", "interrupted"} for s in statuses) else "failed"
    if payload.get("status") == "completed":
        return "completed"
    if payload.get("status") in {"interrupted", "cancelled", "canceled"}:
        return "interrupted"
    return "failed"
