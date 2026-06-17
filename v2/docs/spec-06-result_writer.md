# Spec: Result Writer & Global Store Updates

## Overview
A result writer that generates output files from the global data store in real-time. Supports multiple output formats (TXT, M3U) and protocol-split results (IPv4/IPv6).

## Requirements
1. **TXT Output**: Generate tabular text output with station name → URL mapping
2. **M3U Output**: Generate M3U playlist format with extended tags
3. **Real-time Updates**: Update output files as new sources are scored
4. **Protocol Splitting**: Separate IPv4 and IPv6 sources into separate files
5. **Integration**: Read from GlobalDataStore, emit events on write

## Output Formats

### TXT Format
```
station_name,url
```

### M3U Format
```
#EXTM3U
#EXTINF:-1,station_name
url
```

## File Structure
- `output/result.txt` - TXT format (best sources per station)
- `output/result.m3u` - M3U format
- `output/ipv4/result.txt` - IPv4 only
- `output/ipv6/result.txt` - IPv6 only

## Event Types
- `ResultWriterStartedEvent`: Writer begins a write cycle
- `ResultWriterCompletedEvent`: Writer finishes a write cycle
- `ResultWriterErrorEvent`: Writer encounters an error

## Acceptance Criteria
- [ ] Generates TXT output from GlobalDataStore
- [ ] Generates M3U output from GlobalDataStore
- [ ] Updates files in real-time when scores change
- [ ] Splits IPv4/IPv6 correctly
- [ ] Emits events on write cycles
- [ ] Thread-safe concurrent writes
