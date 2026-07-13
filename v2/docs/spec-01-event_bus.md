# Spec: Core Event Bus & Streaming Data Container

## Overview
Design and implement the foundational infrastructure for the v2 architecture: an asynchronous event bus and a thread-safe global data store. This system will enable decoupled, reactive component communication and real-time data access across all scan stages.

## Requirements
1. **Event Bus**: Async pub/sub pattern supporting typed events with metadata
2. **Global Data Store**: Thread-safe in-memory container with atomic updates
3. **Event Types**: Support all scan lifecycle events (Discovery, Validation, Scan completion, Scoring)
4. **Scalability**: Support multiple concurrent publishers and subscribers
5. **Observability**: Built-in event logging and metrics collection

## Technical Specifications
- Use Python 3.13+ `asyncio` primitives
- Support backpressure and bounded queues
- Implement graceful shutdown with event draining
- Provide both push (callback) and pull (async iterator) consumption patterns

## Data Flow
```
Publisher -> Event Bus -> [Subscriber1, Subscriber2, ...]
     |                        |
     v                        v
Global Store <-------- Subscriber updates
```

## Acceptance Criteria
- [ ] Events can be published and received by multiple subscribers concurrently
- [ ] Global store supports atomic read-modify-write operations
- [ ] Store snapshots can be taken without blocking writers
- [ ] Event bus handles 1000+ events/second without blocking
- [ ] Graceful shutdown completes within 5 seconds
- [ ] All operations are type-safe with mypy compliance

## Dependencies
- `dataclasses` for event definitions
- `asyncio` for async primitives
- `collections` for data structures
- `typing` for type hints
