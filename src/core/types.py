from enum import Enum


class TaskType(str, Enum):
    PII_REDACTION = "pii_redaction"
    MENU_SWEEP = "menu_sweep"
