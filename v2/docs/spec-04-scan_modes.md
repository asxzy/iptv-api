# Spec: Fast/Full/Deep Scan Modes

## Overview
Implement three scan modes that progressively measure source quality. Each mode builds on the previous, with increasing measurement depth and accuracy.

## Scan Modes

### Fast Mode
- **Goal**: Verify media is accessible and playable
- **Method**: Quick connectivity check + content type validation
- **Output**: Basic availability status
- **Time**: < 5 seconds per source
- **Use Case**: Quick overview of all sources

### Full Mode
- **Goal**: Measure speed and basic media properties
- **Method**: Download sample, measure bandwidth, probe with ffprobe
- **Output**: Speed (MB/s), delay, resolution, codec, bitrate, fps
- **Time**: 10-15 seconds per source
- **Use Case**: Rank sources by speed and basic quality
- **Includes**: All Fast Mode checks

### Deep Mode
- **Goal**: Detect actual image quality, penalize upscaling
- **Method**: Detailed quality analysis, resolution authenticity, frame analysis
- **Output**: Quality score, upscale detection, SSIM metrics
- **Time**: 30-60 seconds per source
- **Use Case**: Ensure top-ranked sources truly have the claimed quality
- **Includes**: All Full Mode checks + deep analysis

## Requirements
1. **Mode Selection**: Configurable per-scan or per-source
2. **Parallel Workers**: Multiple concurrent scans per mode
3. **Progressive Results**: Results available immediately when each mode completes
4. **Resource Management**: Limit concurrent FFmpeg/ffprobe processes

## Event Types
- `ScanStartedEvent`: Scanning begins for a source
- `FastScanCompleteEvent`: Fast mode finished
- `FullScanCompleteEvent`: Full mode finished
- `DeepScanCompleteEvent`: Deep mode finished
- `ScanErrorEvent`: Scan failed

## Acceptance Criteria
- [ ] Three scan modes with progressive depth
- [ ] Results from faster modes available immediately
- [ ] Parallel execution with configurable worker count
- [ ] Resource limits enforced (FF process count)
- [ ] Events emitted at each mode completion
- [ ] Mode can be configured per-source or globally
