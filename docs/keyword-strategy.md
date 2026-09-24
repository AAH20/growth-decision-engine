# Search-demand and discoverability strategy

The title starts with **marketing analytics** because a [2026-09-24 Google Trends comparison in AttentionOS Bench](https://github.com/AAH20/attentionos-bench/blob/main/docs/keyword-research.md) found higher **relative** worldwide and US interest for that term than `marketing dashboard`, `marketing attribution`, `business intelligence dashboard`, and `return on ad spend` over the preceding 12 months. That observation applies only to the exact comparison, geography and period. It is **not an absolute monthly-search figure** or a forecast of GitHub traffic. Google's [Trends FAQ](https://support.google.com/trends/answer/4365533) says its index is normalized to a 0–100 range and low-volume terms can show zero.

Google Ads [Keyword Planner historical metrics](https://support.google.com/google-ads/answer/3022575) are the appropriate next source for average monthly searches, with geography, network and date range held constant. Until a measured export is available, no term here is claimed to have a specific monthly volume. Search positioning must describe shipped functionality rather than inflate the product scope.

| Intent | Phrase | Current truthful landing content | Status |
| --- | --- | --- | --- |
| Broad analytics | marketing analytics | Contribution-profit scorecard and daily BI | Shipped |
| Experiment measurement | incrementality testing | Randomized treatment-control estimate with claim limits | Shipped |
| Developer workflow | open source A/B testing analytics | Local CLI, verifier, fixtures and CI | Shipped |
| Business economics | customer acquisition cost, marketing ROI | Cost allocation and contribution formula | Partial; no CAC/LTV model |
| BI operations | business intelligence dashboard | Daily BI JSON and snapshot diff | **Do not target as dashboard yet** |
| Advanced modeling | marketing mix modeling, multi-touch attribution | None | **Do not target as implemented** |
| Platform-specific | PostHog experiment economics, Vercel Flags ROI, Cloudflare analytics attribution | Architecture and adapter contracts only | Proposed |

## Publishing loop

1. Use the repository name as the brand and the description/title for one broad functional term. Topics should match real features, not proposed integrations.
2. Publish executable examples for concrete questions: “How do I measure contribution profit in an A/B test?” and “How do I detect an experiment scorecard restatement?” Each example must run without credentials.
3. After a documentation site exists, use [Search Console](https://developers.google.com/search/docs/monitor-debug/google-analytics-search-console) for actual impressions, queries and clicks. Track qualified installs and repeat runs downstream.
4. Measure a fixed list of candidate keywords in Keyword Planner for US and Egypt, English and Arabic separately. Save exports and date stamps; compare query intent and competition as well as volume.
5. Revise one title or landing asset at a time. Check whether the change improved qualified visitors and external users rather than merely rank or impressions.

Google's [SEO Starter Guide](https://developers.google.com/search/docs/fundamentals/seo-starter-guide) recommends descriptive titles and useful content. The README should remain a technical contract first; keyword repetition without relevant capability weakens trust.
