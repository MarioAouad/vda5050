"""
Static data extracted directly from the VDA 5050 spec, used by the
deterministic tools below. No LLM involved in either lookup — these are
facts from the standard, not generated text.
"""

# Section 6.6.5.1 — the four error levels and their meanings, verbatim from
# the spec (paraphrased slightly to stay under the project's own copyright
# guidelines rather than quoting the standard's exact wording at length).
ERROR_LEVELS = {
    "WARNING": {
        "meaning": "Does not require immediate attention — may be self-resolving (e.g. a dirty LiDAR scanner).",
        "robot_behavior": "Can continue its current order and accept new orders.",
    },
    "URGENT": {
        "meaning": "Requires immediate attention (e.g. low battery).",
        "robot_behavior": "Can continue its current order and accept new orders.",
    },
    "CRITICAL": {
        "meaning": "Requires immediate attention (e.g. trying to pick up an object that isn't there).",
        "robot_behavior": "Cannot continue its current order, but can still accept new orders.",
    },
    "FATAL": {
        "meaning": "Requires user intervention (e.g. losing localization).",
        "robot_behavior": "Cannot continue its current order and cannot accept new orders.",
    },
}

# Section 6.6.5.4 — the predefined error type table, transcribed directly
# (errorType -> errorLevel, description, typical errorReference, and how
# long the mobile robot is expected to keep reporting it).
ERROR_TYPES = {
    "UNSUPPORTED_PARAMETER": {"level": "CRITICAL", "description": "Receipt of a message with an unsupported optional parameter.", "reference": "Name of parameter", "report_duration": "Until new order is accepted."},
    "NO_ORDER_TO_CANCEL": {"level": "WARNING", "description": "Received a cancelOrder action but has no active order to cancel.", "reference": "actionId of cancelOrder", "report_duration": "Until new order is accepted."},
    "VALIDATION_FAILURE": {"level": "WARNING", "description": "Receipt of a malformed order.", "reference": "orderId and orderUpdateId of the rejected message, if available", "report_duration": "Until new order is accepted."},
    "INVALID_ORDER_ACTION": {"level": "WARNING", "description": "Receipt of an order containing unsupported actions.", "reference": "orderId and orderUpdateId of the rejected message", "report_duration": "Until new order is accepted."},
    "INVALID_INSTANT_ACTION": {"level": "WARNING", "description": "Receipt of an unsupported instant action.", "reference": "actionId of instantAction", "report_duration": "Until new instant action is accepted."},
    "OUTDATED_ORDER_UPDATE": {"level": "WARNING", "description": "Receipt of an order with the correct orderId but an outdated orderUpdateId.", "reference": "orderId and orderUpdateId of the rejected message", "report_duration": "Until new order is accepted."},
    "SAME_ORDER_UPDATE_ID": {"level": "WARNING", "description": "Receipt of a duplicate order message (same orderId and orderUpdateId).", "reference": "orderId and orderUpdateId of the rejected message", "report_duration": "Until new order is accepted."},
    "ORDER_UPDATE_FOLLOWING_CANCEL": {"level": "WARNING", "description": "Receipt of an order update for an order that has already been cancelled.", "reference": "orderId and orderUpdateId of the rejected message", "report_duration": "Until new order is accepted."},
    "OUTSIDE_OF_CORRIDOR": {"level": "CRITICAL", "description": "Leaving the corridor defined for an edge.", "reference": "edgeId", "report_duration": "Until no longer violating the corridor boundaries."},
    "INSUFFICIENT_MEMORY": {"level": "URGENT", "description": "Not enough memory to process the received order.", "reference": "orderId and orderUpdateId of the rejected message, if available", "report_duration": "Until new order is accepted."},
    "DUPLICATE_MAP": {"level": "WARNING", "description": "Receipt of a map with a mapId and mapVersion that already exist.", "reference": "mapId and mapVersion of the duplicate", "report_duration": "Until a new map-related instantAction is accepted."},
    "BLOCKED_ZONE_VIOLATION": {"level": "CRITICAL", "description": "Entering a BLOCKED zone.", "reference": "zoneId", "report_duration": "Until no longer violating the blocked zone."},
    "DUPLICATE_ZONE_SET": {"level": "WARNING", "description": "Receipt of a zone set with a zoneSetId that already exists.", "reference": "zoneSetId or actionId of instantAction", "report_duration": "A reasonable amount of time for fleet control to notice the failed update."},
    "RELEASE_LOST": {"level": "CRITICAL", "description": "Losing the release for a RELEASE zone.", "reference": "zoneId", "report_duration": "Until no longer within the RELEASE zone, or the release is granted again."},
    "ZONE_ACTION_CONFLICT": {"level": "CRITICAL", "description": "Conflict between zone behavior and zone actions.", "reference": "zoneId of the ACTION zone", "report_duration": "Until no longer violating the zone behavior."},
    "NODE_UNREACHABLE": {"level": "CRITICAL", "description": "The mobile robot cannot reach a node in its order.", "reference": "nodeId", "report_duration": "Until new order is accepted."},
    "LOCALIZATION_ERROR": {"level": "FATAL", "description": "The mobile robot is not localized.", "reference": None, "report_duration": "Until localization is regained."},
    "NO_ROUTE_TO_TARGET": {"level": "WARNING", "description": "Receipt of an order with at least one unreachable node.", "reference": "orderId", "report_duration": "Until new order is accepted."},
    "OTHER_ORDER_ACTIVE": {"level": "WARNING", "description": "Receipt of a new order while another order is still active.", "reference": "orderId", "report_duration": "Until new order is accepted."},
    "START_NODE_OUT_OF_RANGE": {"level": "WARNING", "description": "Receipt of an order with an unreachable first node.", "reference": "orderId", "report_duration": "Until new order is accepted."},
    "MOBILE_ROBOT_NOT_AVAILABLE": {"level": "WARNING", "description": "Receipt of an order while not in AUTOMATIC, SEMIAUTOMATIC, or INTERVENED operating mode.", "reference": "orderId", "report_duration": "Until operating mode allows new orders."},
    "UNKNOWN_MAP_ID": {"level": "WARNING", "description": "Receipt of an order containing nodes that reference an unknown mapId.", "reference": "orderId", "report_duration": "Until new order is accepted."},
}

# Maps a schema_name the caller passes in to the actual filename in
# data/raw_docs/json_schemas/, matching each schema's own "subtopic" field.
SCHEMA_FILES = {
    "order": "order.schema",
    "state": "state.schema",
    "instantActions": "instantActions.schema",
    "connection": "connection.schema",
    "visualization": "visualization.schema",
    "factsheet": "factsheet.schema",
    "zoneSet": "zoneSet.schema",
    "responses": "responses.schema",
}
