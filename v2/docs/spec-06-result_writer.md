# Spec: Result Writer & Global Store Updates

## Overview
The Result Writer stage is responsible for generating the final output files (result.txt, result.m3u, ipv4/result.txt, ipv6/result.txt, etc.) from the scored and ranked media sources in the Global Data Store. It should update the files in real-time (or with a small debounce) as new scores arrive, allowing the web service to serve the most up-to-date results without waiting for the full scan to complete.

## Requirements
1. **Atomic Updates**: Update the Global Data Store with scored media sources in an atomic manner.
2. **Real-time File Generation**: Generate result files whenever the Global Data Store is updated with new scores (or on a configurable debounce interval).
3. **File Formats**: Generate the same output formats as the original codebase:
   - result.txt (or result.m3u if open_m3u_result is true)
   - ipv4/result.txt and ipv6/result.txt (separated by IP type)
   - hls/result.txt and hls/result.m3u (if open_rtmp is true)
   - Separate files for multi-source options (txt/multi, etc.)
4. **Integration**: 
   - Listen to ScoreUpdatedEvent (and optionally RankingUpdatedEvent) from the EventBus.
   - Update the Global Data Store with the scored media source.
   - Generate and write the result files.
   - Optionally update a live result store for zero-delay serving by the web service.
5. **Configuration**: Respect the configuration options from config.ini (e.g., open_m3u_result, open_realtime_write, write_interval, etc.).
6. **Sorting**: Sort media sources by composite score (descending) per station, and apply the same sorting logic as the original codebase (which also considers origin preference, etc.).
7. **Performance**: Efficiently update only the changed parts of the result files (or regenerate the entire file if necessary, but with debounce to avoid excessive disk I/O).

## Technical Specifications
- Subscribe to ScoreUpdatedEvent (and optionally RankingUpdatedEvent) from the EventBus.
- When a score update arrives, update the media source in the Global Data Store.
- Trigger a debounced timer to regenerate the result files (if open_realtime_write is true).
- If open_realtime_write is false, only generate results at the end of the scan (when a ScanJobCompletedEvent is received).
- The result file generation should reuse the existing functions from the original codebase (e.g., write_channel_to_file, convert_to_m3u, etc.) to ensure compatibility.
- Maintain an in-memory representation of the current result to avoid regenerating from scratch every time (optional, but recommended for performance).
- Handle the generation of multi-source files (where multiple URLs for the same station are joined by '#').

## Event Types
- Consumes: ScoreUpdatedEvent, RankingUpdatedEvent, ScanJobCompletedEvent
- May emit: ResultUpdatedEvent (optional, for progress reporting)

## Acceptance Criteria
- [ ] Correctly updates Global Data Store with scored media sources.
- [ ] Generates result files in the same format as the original codebase.
- [ ] Respects configuration options (open_m3u_result, open_realtime_write, etc.).
- [ ] Updates result files in real-time when open_realtime_write is true.
- [ ] Only updates results at the end of the scan when open_realtime_write is false.
- [ ] Generates separate IP type files (ipv4, ipv6) when appropriate.
- [ ] Handles multi-source URLs correctly.
- [ ] All tests pass.
- [ ] No regressions in existing functionality.

## Dependencies
- Event Bus (spec-01)
- Global Data Store (spec-02)
- Scoring Component (spec-05)
- Original codebase utility functions for writing results (to be imported from parent directory)

