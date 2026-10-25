"""Operator identity is resolved from the current profile, never a task message."""
def owner_id():
    from hermes_cli.config import load_config_readonly
    settings = (((load_config_readonly().get("plugins") or {}).get("entries") or {}).get("qq-group-tools") or {}).get("settings") or {}
    value = str(settings.get("owner_id") or "").strip()
    if not value:
        raise RuntimeError("Configure qq-group-tools settings.owner_id")
    return value


def display_identity(identifier, kind='member'):
    """Display-only labels never participate in authorization or numeric-ID inference."""
    from hermes_cli.config import load_config_readonly
    settings=((((load_config_readonly().get('plugins') or {}).get('entries') or {}).get('qq-group-tools') or {}).get('settings') or {})
    identifier=str(identifier or '')
    if kind=='member' and identifier==owner_id():
        return '主人'
    labels=settings.get('group_labels' if kind=='group' else 'member_labels') or {}
    label=labels.get(identifier) if isinstance(labels,dict) else None
    if isinstance(label,str) and label.strip():
        return label.strip().replace('\n',' ')[:60]
    return ('授权群' if kind=='group' else '群友')+'·'+identifier[-6:]
