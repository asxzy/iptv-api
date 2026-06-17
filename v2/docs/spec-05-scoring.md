# Spec: Scoring Component

## Overview
A standalone scoring engine that ranks media sources based on scan results. Supports pluggable scoring algorithms and configurable weights.

## Requirements
1. **Quality Scoring**: Score based on resolution, fps, codec efficiency, bitrate adequacy
2. **Loadability Scoring**: Score based on speed, delay, bandwidth margin
3. **Authenticity Scoring**: Detect and penalize upscaled video
4. **Configurable Weights**: Allow users to tune quality vs speed preference
5. **Composite Score**: Blend multiple metrics into a single comparable score

## Scoring Formula
```
Score = w_Q * Q + w_L * L

Q = w_res * (resolution_score * a_res) + w_enc * encoding_adequacy + w_fps * (fps_score * a_fps)
L = w_start * startup + w_margin * margin
```

Where:
- `Q` = Quality component
- `L` = Loadability component  
- `a_res` = Resolution authenticity (1.0 = genuine, <1.0 = upscaled)
- `a_fps` = FPS authenticity
- `w_*` = Configurable weights

## Event Types
- `ScoreUpdatedEvent`: New score computed for a source
- `RankingUpdatedEvent`: Station ranking changed

## Acceptance Criteria
- [ ] Computes quality score from resolution, fps, codec
- [ ] Computes loadability score from speed, delay
- [ ] Detects and penalizes upscaled video
- [ ] Configurable weights via config file
- [ ] Emits events when scores change
- [ ] Thread-safe concurrent scoring
