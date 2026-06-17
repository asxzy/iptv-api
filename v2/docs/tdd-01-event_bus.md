# TDD: Core Event Bus & Streaming Data Container

## Test-Driven Development Plan

### Phase 1: Event Bus Core

#### Test: Basic Pub/Sub
**Given**: An event bus with a subscriber
**When**: A test event is published
**Then**: The subscriber receives the event within 100ms

#### Test: Multiple Subscribers
**Given**: An event bus with 3 subscribers
**When**: An event is published
**Then**: All 3 subscribers receive the event

#### Test: Event Ordering
**Given**: A subscriber listening for ordered events
**When**: 100 events are published rapidly
**Then**: Events are received in publication order

#### Test: Backpressure
**Given**: A slow subscriber and a fast publisher
**When**: Events exceed queue capacity
**Then**: Publisher blocks, preventing unbounded memory growth

#### Test: Graceful Shutdown
**Given**: Active publishers and subscribers
**When**: Shutdown is initiated
**Then**: All queued events are processed, then components stop

### Phase 2: Global Data Store

#### Test: Concurrent Reads/Writes
**Given**: Multiple writers updating different stations
**When**: Reads happen concurrently
**Then**: No race conditions, data remains consistent

#### Test: Atomic Updates
**Given**: A station with multiple media sources
**When**: An atomic rank update is applied
**Then**: All observers see either old or new state, never mixed

#### Test: Snapshot Isolation
**Given**: A store with 1000 stations
**When**: A snapshot is taken during heavy writes
**Then**: Snapshot is consistent, no blocking occurs

### Phase 3: Integration

#### Test: End-to-End Event Flow
**Given**: Full pipeline with Discovery -> Validation events
**When**: Discovery publishes a StationDiscovered event
**Then**: Validation subscriber receives and processes it

#### Test: Real-time Updates
**Given**: A web service reading from the store
**When**: Store is updated via events
**Then**: Web service immediately serves updated data

## Implementation Order
1. `EventBus` class with basic pub/sub
2. `GlobalDataStore` with atomic operations
3. `Event` dataclasses and type definitions
4. Integration tests
5. Performance benchmarks

## Running Tests
```bash
cd v2
python -m pytest core/tests/ -v
```
