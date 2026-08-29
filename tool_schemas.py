"""
OpenAI-style tool/function definitions matching tools.py.

Use directly with the OpenAI API (fallback_agent.py) OR as the reference when
registering tools in the TrueFoundry harness. Keep in sync with tools.py.
"""

TOOL_SCHEMAS = [
    {
        "type": "function",
        "function": {
            "name": "get_active_alerts",
            "description": "List currently firing production alerts.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_metrics",
            "description": "Get current status and metrics for a service (status, version, error_rate, p99_latency_ms, cpu, memory, replicas).",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_logs",
            "description": "Recent log lines for a service. level is one of ERROR, WARN, INFO, ALL.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "level": {"type": "string", "enum": ["ERROR", "WARN", "INFO", "ALL"]},
                    "limit": {"type": "integer"},
                },
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "list_services",
            "description": "List all services with status and version.",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_recent_deploys",
            "description": "Recent deployments for a service (version, deployed_at, deployed_by, status). Use to check if a recent deploy correlates with the alert.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "limit": {"type": "integer"},
                },
                "required": ["service"],
            },
        },
    },
    # ---- destructive: REQUIRES_APPROVAL ----
    {
        "type": "function",
        "function": {
            "name": "rollback_service",
            "description": "DESTRUCTIVE. Roll a service back to a previous version. Requires human approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "to_version": {"type": "string"},
                },
                "required": ["service", "to_version"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "restart_service",
            "description": "DESTRUCTIVE. Restart a service. Requires human approval. Note: does not fix a bad deploy.",
            "parameters": {
                "type": "object",
                "properties": {"service": {"type": "string"}},
                "required": ["service"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "scale_service",
            "description": "DESTRUCTIVE. Change replica count for a service. Requires human approval.",
            "parameters": {
                "type": "object",
                "properties": {
                    "service": {"type": "string"},
                    "replicas": {"type": "integer"},
                },
                "required": ["service", "replicas"],
            },
        },
    },
]
