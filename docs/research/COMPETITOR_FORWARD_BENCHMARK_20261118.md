# Independent competitor benchmark (research only)

This original offline tool compares **authorized, frozen pre-match** tennis forecasts on identical canonical ATP Tour / Challenger men's singles matches. It does not access vendors, production databases, odds endpoints or change model policies.

Run:
\`\`\`bash
python -m sports_edge_tennis.validation.competitor_forward_audit fixtures.csv forecasts.csv --baseline sports_edge_original
\`\`\`

Required \`fixtures.csv\` columns: \`match_id,tour,draw,player1_id,player2_id,tournament_id,frozen_start_utc,result_status,winner_side\`. Tour must be \`ATP_TOUR\` or \`CHALLENGER\`; draw \`MAIN_DRAW\` or \`QUALIFYING\`. \`frozen_start_utc\` is the *original pre-match scheduled start*, not an after-the-fact reschedule. \`result_status\` of \`COMPLETED\` or \`RETIRED\` requires winning side \`1\` or \`2\`; all other statuses remain in the denominator but are not scored.

Required \`forecasts.csv\` columns: \`source,match_id,player1_id,player2_id,p_player1,published_at_utc,captured_at_utc,rights,model_version\`. Rights are \`internal\`, \`licensed\`, or \`authorized_public_export\` only. Timestamps must be genuine explicit UTC and no later than **30 minutes before frozen start** by default, with publication no later than capture. Wrong identities, missing rights, after-cutoff, unknown matches, ambiguous duplicates and future-info are rejected. Input timestamps are assertions: store independent source snapshots and licences to prove them.

Output: per-source ATP/Challenger coverage, settled-only hit rate, Brier/log loss, paired delta against same-fixture Sports Edge original, tournament/month clustered 95% bootstrap intervals where enough clusters, rejections and file SHA-256. A negative paired delta favors the candidate. No odds/ROI claims without contemporaneous licensed prices. This is *not* an independent verification of third-party archives and must never be presented as a production model upgrade or profitability proof.

This experiment implements the evidence gates in issue #304. Avoid copying third-party noncommercial code/data into Sports Edge.
