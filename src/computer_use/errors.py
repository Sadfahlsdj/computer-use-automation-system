class AutomationError(Exception):
    category = "automation_error"
    retryable = False


class PolicyViolation(AutomationError):
    category = "policy_violation"


class TargetNotFound(AutomationError):
    category = "target_not_found"


class AmbiguousTarget(AutomationError):
    category = "ambiguous_target"


class CheckpointFailed(AutomationError):
    category = "checkpoint_failed"


class InterventionRequired(AutomationError):
    category = "intervention_required"


class TransientSurfaceError(AutomationError):
    category = "transient_surface_error"
    retryable = True
