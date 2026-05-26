from dataclasses import dataclass, field
from enum import Enum
from models.schema import TableSpec


class Phase(Enum):
    COLLECTING = "collecting"
    CONFIRMING = "confirming"
    GENERATING = "generating"
    DONE = "done"


@dataclass
class SessionState:
    phase: Phase = Phase.COLLECTING
    tables: list[TableSpec] = field(default_factory=list)
