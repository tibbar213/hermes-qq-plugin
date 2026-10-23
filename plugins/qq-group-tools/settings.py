"""Operator identity is resolved from the current profile, never a task message."""
def owner_id():
    from hermes_cli.config import load_config_readonly
    settings = (((load_config_readonly().get("plugins") or {}).get("entries") or {}).get("qq-group-tools") or {}).get("settings") or {}
    value = str(settings.get("owner_id") or "").strip()
    if not value:
        raise RuntimeError("Configure qq-group-tools settings.owner_id")
    return value
