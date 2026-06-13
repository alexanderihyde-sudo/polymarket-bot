# TRAINER Metrics Log

| Date | Settles | Brain Skill | Champion | Market Skill | Brier Model | Brier Market | SportsEdge | Notes |
|------|---------|-------------|----------|--------------|-------------|--------------|-----------|-------|
| 2026-06-13 04:20 UTC | n=1 | warming_up | logistic | -0.00517 | 0.19262 | 0.18745 | no_data | Initial baseline; brain warming up, mkt_skill negative |
| 2026-06-13 08:00 UTC | n=23 | 0.13 | logistic | -0.00557 | 0.21331 | 0.20774 | no_data | 22 new settles; brain credibility 0.13, market skill -0.0004 drift; lab optimal fade_12c_s8t15 |
| 2026-06-13 08:22 UTC | n=25 | 0.0445 | logistic | -0.00453 | 0.21436 | 0.20983 | no_data | 2 new settles; brain cv_skill 0.0445 on n=29; mlmodel champion gbm beats market marginally; lab confirms fade_12c_s8t15 strategy optimal |
| 2026-06-13 09:02 UTC | n=38 | 0.0967 | logistic | -0.00568 | 0.23811 | 0.21190 | no data | 13 new settles; brain skill +0.0522; mkt_skill drift -0.00115; lab: fade_12c_s8t15 optimal |
| 2026-06-13 09:24 UTC | n=42 | 0.1402 | logistic | -0.00436 | 0.21578 | 0.21142 | no_data | 4 new settles; brain cv_skill improved +0.0435; mkt_skill improved (less negative); gbm champion Brier 0.21578 vs market 0.21142 (skill -0.00436); fade_12c_s8t15 confirmed optimal |
| 2026-06-13 09:52 UTC | n=43 | 0.1447 | logistic | -0.00324 | 0.21642 | 0.21318 | no_data | 1 new settle; brain cv_skill improved +0.0045; mkt_skill improved (less negative) +0.00112; gbm Brier slightly higher vs market; lab confirms fade_12c_s8t15 optimal |
| 2026-06-13 10:23 UTC | n=47 | 0.0858 | logistic | -0.00325 | 0.21658 | 0.21334 | no data | 4 new settles; brain cv_skill dropped 0.0858 (was 0.1447, -5.89%); mkt_skill stable -0.00325; mlmodel gbm 0.21658 vs market 0.21334 |
| 2026-06-13 11:00 UTC | n=53 | 0.0915 | logistic | -0.00481 | 0.21903 | 0.21422 | no_data | 6 new settles; brain cv_skill +0.0057 from 0.0858; market_skill regressed -0.00156 (trend reversal); mlmodel gbm; lab: fade_12c_s8t15 optimal |
