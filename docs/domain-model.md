# CoFounder OS Canonical Domain Model

## Purpose

D02 establishes a single vocabulary for orchestration state. These records are
the contract between the router, agents, approval workflow, artifact handling,
audit logging, persistence, and future user interfaces.

API transport models remain in `app/models.py`. Canonical product state lives
in `app/domain/models.py`.

## Relationship Map

```text
Run
├── Task[]
│   ├── AgentMessage[]
│   ├── RouteDecision[]
│   ├── Approval?
│   ├── input Artifact[]
│   └── output Artifact[]
├── Artifact[]
└── AuditEvent[]
```

All cross-record relationships use UUIDs. This prevents nested mutable state
from becoming the source of truth and allows later storage adapters to use
files, SQLite, PostgreSQL, or an event store without changing the contract.

## Records

### Run

Represents one user objective from creation through completion. It owns the
top-level lifecycle and references tasks and artifacts.

### Task

Represents a dependency-aware unit of work. It records assignment, lifecycle,
input and output artifacts, and an optional approval gate.

### AgentMessage

Represents communication among the user, system, agents, assistants, and tools.
Correlation and parent-message identifiers support tracing.

### RouteDecision

Records why a virtual model was mapped to a concrete model and provider. It
captures candidates, fallback usage, and observed routing latency.

### Approval

Represents a human or policy gate. Request and decision fields are separate so
pending approvals remain complete audit records.

### Artifact

Represents addressable inputs and outputs. The record stores a URI rather than
embedding content, allowing local files and future object storage to share one
contract.

### AuditEvent

Represents a significant action and its outcome. It is designed to be appended
rather than mutated by business logic.

## Contract Rules

- Every record has a UUID, schema version, UTC creation timestamp, and metadata.
- Unknown fields are rejected.
- Mutable defaults are isolated per record.
- Statuses and classifications use string enums for stable JSON.
- Cross-record relationships use UUID references.
- Artifact content remains outside the record.
- API compatibility objects and domain records remain separate.
- D02 introduces no database, queue, service, model, or port changes.

## Schema Version

The initial schema version is `1.0`. Additive changes may remain compatible.
Breaking changes require a new schema version and explicit migration logic.
