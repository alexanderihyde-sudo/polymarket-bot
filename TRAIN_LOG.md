# TRAINER Metrics Log

| Date | Settles | Brain Skill | Champion | Market Skill | Brier Model | Brier Market | SportsEdge | Notes |
|------|---------|-------------|----------|--------------|-------------|--------------|-----------|-------|
| 2026-06-13 04:20 UTC | n=1 | warming_up | logistic | -0.00517 | 0.19262 | 0.18745 | no_data | Initial baseline; brain warming up, mkt_skill negative |
| 2026-06-13 08:00 UTC | n=23 | 0.13 | logistic | -0.00557 | 0.21331 | 0.20774 | no_data | 22 new settles; brain credibility 0.13, market skill -0.0004 drift; lab optimal fade_12c_s8t15 |
| 2026-06-13 08:22 UTC | n=25 | 0.0445 | logistic | -0.00453 | 0.21436 | 0.20983 | no_data | 2 new settles; brain cv_skill 0.0445 on n=29; mlmodel champion gbm beats market marginally; lab confirms fade_12c_s8t15 strategy optimal |
