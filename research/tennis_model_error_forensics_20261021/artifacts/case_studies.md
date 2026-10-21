# High-confidence incorrect Manager Model Picks

All rows are unique first production Manager CONSENSUS evaluations per match, probability >= 80%, settled COMPLETED losses.

## Evidence-flag frequency

| Flag | High-confidence losses | All Manager picks >=60% |
| --- | --- | --- |
| agent3_abstained | 16 | 237 |
| qualification_identity_mismatch | 11 | 90 |
| no_pre_pick_sharp | 10 | 98 |
| picked_thin_stat_history | 7 | 103 |
| picked_thin_surface_history | 7 | 109 |
| model_much_above_sharp | 5 | 55 |
| opponent_strong_surface_form | 2 | 18 |
| picked_thin_history | 2 | 44 |
| opponent_no_visible_history | 2 | 33 |
| opponent_thin_history | 1 | 11 |
| surface_missing | 1 | 12 |

Flags are descriptive, not causal. A flag is only a cause when the case narrative supplies evidence beyond the loss itself.

## 1. Match 131505 - C. Taberner vs S. Pieri

- Date/start: 2026-10-02T08:00:00+00:00
- Tournament: Bari (Italy) - Qualification (CHALLENGER, CLAY, best of 3, QF)
- Picked: C. Taberner at 0.9277
- Actual winner: S. Pieri (2 - 1)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-01T13:02:26.373391+00:00; finalized: 2026-10-01T13:03:01.407314+00:00
- Active agents: 2; probability spread: 0.0037
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.9296 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.9259 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=None
- Picked history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-01T09:40:00+00:00", "prior_10_wins": 6, "prior_matches": 17, "prior_stat_matches": 10, "prior_surface_matches": 8, "prior_surface_stat_matches": 8, "prior_surface_win_rate": 0.625, "prior_surface_wins": 5, "prior_win_rate": 0.5294117647058824, "prior_wins": 9}
- Opponent history: {"days_since_last_match": 2, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-29T13:35:00+00:00", "prior_10_wins": 5, "prior_matches": 39, "prior_stat_matches": 5, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.5, "prior_surface_wins": 2, "prior_win_rate": 0.46153846153846156, "prior_wins": 18}
- Market: {"model_minus_sharp": 0.17193369504335143, "offered_best_price": 1.31, "offered_bookmakers": 4, "sharp_as_of": "2026-10-01T19:05:28.798063+00:00", "sharp_favourite_side": 2, "sharp_opponent_probability": 0.24422619504335152, "sharp_picked_probability": 0.7557738049566486, "sharp_price_player_1": 3.94, "sharp_price_player_2": 1.2732}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4582}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4337}, "match_version_count": 27, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, model_much_above_sharp, qualification_identity_mismatch

## 2. Match 131988 - L. J. Rodriguez vs T. Cigarran

- Date/start: 2026-10-12T19:30:00+00:00
- Tournament: Santa Cruz 2 (CHALLENGER, CLAY, best of 3, Q1)
- Picked: L. J. Rodriguez at 0.9156
- Actual winner: T. Cigarran (0 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-12T07:01:16.383072+00:00; finalized: 2026-10-12T07:01:55.862917+00:00
- Active agents: 2; probability spread: 0.0472
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.8920 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.9392 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 7, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-05T13:10:00+00:00", "prior_10_wins": 4, "prior_matches": 24, "prior_stat_matches": 8, "prior_surface_matches": 7, "prior_surface_stat_matches": 7, "prior_surface_win_rate": 0.5714285714285714, "prior_surface_wins": 4, "prior_win_rate": 0.4583333333333333, "prior_wins": 11}
- Opponent history: {"days_since_last_match": 6, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-06T13:10:00+00:00", "prior_10_wins": 5, "prior_matches": 35, "prior_stat_matches": 8, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.5, "prior_surface_wins": 2, "prior_win_rate": 0.5142857142857142, "prior_wins": 18}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4932}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4868}, "match_version_count": 22, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": true, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, qualification_identity_mismatch

## 3. Match 131620 - B. Zhukayev vs Y. Demin

- Date/start: 2026-10-05T05:00:00+00:00
- Tournament: Wuning 3 (China) - Qualification (CHALLENGER, HARD, best of 3, R32)
- Picked: B. Zhukayev at 0.9031
- Actual winner: Y. Demin (0 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-04T19:04:38.358082+00:00; finalized: 2026-10-04T19:05:19.086580+00:00
- Active agents: 2; probability spread: 0.0081
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.9071 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8990 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 53, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-08-12T13:15:00+00:00", "prior_10_wins": 5, "prior_matches": 25, "prior_stat_matches": 0, "prior_surface_matches": 0, "prior_surface_stat_matches": 0, "prior_surface_win_rate": null, "prior_surface_wins": 0, "prior_win_rate": 0.48, "prior_wins": 12}
- Opponent history: {"days_since_last_match": 21, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-13T08:10:00+00:00", "prior_10_wins": 2, "prior_matches": 6, "prior_stat_matches": 1, "prior_surface_matches": 1, "prior_surface_stat_matches": 1, "prior_surface_win_rate": 0.0, "prior_surface_wins": 0, "prior_win_rate": 0.3333333333333333, "prior_wins": 2}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4813}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4491}, "match_version_count": 26, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, picked_thin_stat_history, picked_thin_surface_history, qualification_identity_mismatch

## 4. Match 131841 - A. Santillan vs K. Saitoh

- Date/start: 2026-10-09T02:00:00+00:00
- Tournament: Wuning 3 (China) - Qualification (CHALLENGER, HARD, best of 3, QF)
- Picked: A. Santillan at 0.8862
- Actual winner: K. Saitoh (2 - 1)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-07T19:05:15.772996+00:00; finalized: 2026-10-07T19:05:49.379236+00:00
- Active agents: 2; probability spread: 0.0057
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.8833 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.8890 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-07T03:05:00+00:00", "prior_10_wins": 5, "prior_matches": 35, "prior_stat_matches": 11, "prior_surface_matches": 8, "prior_surface_stat_matches": 8, "prior_surface_win_rate": 0.5, "prior_surface_wins": 4, "prior_win_rate": 0.42857142857142855, "prior_wins": 15}
- Opponent history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-07T03:05:00+00:00", "prior_10_wins": 5, "prior_matches": 6, "prior_stat_matches": 6, "prior_surface_matches": 6, "prior_surface_stat_matches": 6, "prior_surface_win_rate": 0.8333333333333334, "prior_surface_wins": 5, "prior_win_rate": 0.8333333333333334, "prior_wins": 5}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 6139}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4295}, "match_version_count": 25, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, opponent_strong_surface_form, qualification_identity_mismatch

## 5. Match 131534 - V. Sachko vs S. Pieri

- Date/start: 2026-10-03T10:30:00+00:00
- Tournament: Bari (Italy) - Qualification (CHALLENGER, CLAY, best of 3, SF)
- Picked: V. Sachko at 0.8734
- Actual winner: S. Pieri (1 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-02T17:57:08.907072+00:00; finalized: 2026-10-02T17:57:42.880827+00:00
- Active agents: 2; probability spread: 0.0504
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.8986 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8481 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 2, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-30T11:05:00+00:00", "prior_10_wins": 7, "prior_matches": 49, "prior_stat_matches": 12, "prior_surface_matches": 8, "prior_surface_stat_matches": 8, "prior_surface_win_rate": 0.75, "prior_surface_wins": 6, "prior_win_rate": 0.5102040816326531, "prior_wins": 25}
- Opponent history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-02T09:35:00+00:00", "prior_10_wins": 5, "prior_matches": 41, "prior_stat_matches": 7, "prior_surface_matches": 6, "prior_surface_stat_matches": 6, "prior_surface_win_rate": 0.6666666666666666, "prior_surface_wins": 4, "prior_win_rate": 0.4878048780487805, "prior_wins": 20}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4432}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4582}, "match_version_count": 27, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, qualification_identity_mismatch

## 6. Match 132404 - K. Samrej vs Y. Demin

- Date/start: 2026-10-19T05:30:00+00:00
- Tournament: Guangzhou (CHALLENGER, HARD, best of 3, Q1)
- Picked: K. Samrej at 0.8691
- Actual winner: Y. Demin (0 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-18T13:05:21.615766+00:00; finalized: 2026-10-18T13:05:59.055581+00:00
- Active agents: 2; probability spread: 0.0429
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.8476 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8905 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-18T08:45:00+00:00", "prior_10_wins": 6, "prior_matches": 28, "prior_stat_matches": 10, "prior_surface_matches": 5, "prior_surface_stat_matches": 5, "prior_surface_win_rate": 0.6, "prior_surface_wins": 3, "prior_win_rate": 0.6071428571428571, "prior_wins": 17}
- Opponent history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-18T06:55:00+00:00", "prior_10_wins": 4, "prior_matches": 9, "prior_stat_matches": 4, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.5, "prior_surface_wins": 2, "prior_win_rate": 0.4444444444444444, "prior_wins": 4}
- Market: {"model_minus_sharp": 0.33761790502499334, "offered_best_price": 1.77, "offered_bookmakers": 3, "sharp_as_of": "2026-10-19T05:05:07.167483+00:00", "sharp_favourite_side": 1, "sharp_opponent_probability": 0.4685609050249935, "sharp_picked_probability": 0.5314390949750066, "sharp_price_player_1": 1.781, "sharp_price_player_2": 2.02}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4324}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4491}, "match_version_count": 27, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": true, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, model_much_above_sharp, qualification_identity_mismatch

## 7. Match 131634 - Y. Takahashi vs K. Saitoh

- Date/start: 2026-10-05T02:00:00+00:00
- Tournament: Wuning 3 (China) - Qualification (CHALLENGER, HARD, best of 3, F)
- Picked: Y. Takahashi at 0.8452
- Actual winner: K. Saitoh (2 - 1)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-05T01:05:02.240720+00:00; finalized: 2026-10-05T01:06:44.295929+00:00
- Active agents: 2; probability spread: 0.0230
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.8337 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.8567 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 1, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-04T04:05:00+00:00", "prior_10_wins": 4, "prior_matches": 25, "prior_stat_matches": 2, "prior_surface_matches": 2, "prior_surface_stat_matches": 2, "prior_surface_win_rate": 0.5, "prior_surface_wins": 1, "prior_win_rate": 0.4, "prior_wins": 10}
- Opponent history: {"days_since_last_match": 1, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-04T05:25:00+00:00", "prior_10_wins": 2, "prior_matches": 3, "prior_stat_matches": 3, "prior_surface_matches": 3, "prior_surface_stat_matches": 3, "prior_surface_win_rate": 0.6666666666666666, "prior_surface_wins": 2, "prior_win_rate": 0.6666666666666666, "prior_wins": 2}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 6139}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4502}, "match_version_count": 25, "name_flag_mismatch": false, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": true, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, opponent_thin_history, picked_thin_stat_history, picked_thin_surface_history

## 8. Match 131834 - D. Dzumhur vs A. Shelbayh

- Date/start: 2026-10-08T09:00:00+00:00
- Tournament: Villena (CHALLENGER, CLAY, best of 3, R16)
- Picked: D. Dzumhur at 0.8414
- Actual winner: A. Shelbayh (1 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-07T18:03:19.908068+00:00; finalized: 2026-10-07T18:03:59.631447+00:00
- Active agents: 2; probability spread: 0.0565
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.8697 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8132 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-07T10:20:00+00:00", "prior_10_wins": 6, "prior_matches": 46, "prior_stat_matches": 3, "prior_surface_matches": 1, "prior_surface_stat_matches": 1, "prior_surface_win_rate": 1.0, "prior_surface_wins": 1, "prior_win_rate": 0.43478260869565216, "prior_wins": 20}
- Opponent history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-07T09:10:00+00:00", "prior_10_wins": 6, "prior_matches": 54, "prior_stat_matches": 17, "prior_surface_matches": 1, "prior_surface_stat_matches": 1, "prior_surface_win_rate": 1.0, "prior_surface_wins": 1, "prior_win_rate": 0.5, "prior_wins": 27}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4755}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4946}, "match_version_count": 26, "name_flag_mismatch": false, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, picked_thin_stat_history, picked_thin_surface_history

## 9. Match 131327 - S. Mitsui vs O. Okonkwo

- Date/start: 2026-09-28T14:00:00+00:00
- Tournament: Columbus (CHALLENGER, None, best of 3, F)
- Picked: S. Mitsui at 0.8402
- Actual winner: O. Okonkwo (2 - 0)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-09-28T00:04:12.247917+00:00; finalized: 2026-09-28T00:04:55.205996+00:00
- Active agents: 2; probability spread: 0.0164
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.8320 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.8484 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=None
- Picked history: {"days_since_last_match": 8, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-20T19:40:00+00:00", "prior_10_wins": 3, "prior_matches": 5, "prior_stat_matches": 1, "prior_surface_matches": 4, "prior_surface_stat_matches": 0, "prior_surface_win_rate": 0.75, "prior_surface_wins": 3, "prior_win_rate": 0.6, "prior_wins": 3}
- Opponent history: {"days_since_last_match": 106, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-06-14T12:35:00+00:00", "prior_10_wins": 3, "prior_matches": 6, "prior_stat_matches": 0, "prior_surface_matches": 6, "prior_surface_stat_matches": 0, "prior_surface_win_rate": 0.5, "prior_surface_wins": 3, "prior_win_rate": 0.5, "prior_wins": 3}
- Market: {"model_minus_sharp": 0.096380847107438, "offered_best_price": 1.51, "offered_bookmakers": 3, "sharp_as_of": "2026-09-28T13:35:45.999177+00:00", "sharp_favourite_side": 2, "sharp_opponent_probability": 0.25619834710743805, "sharp_picked_probability": 0.7438016528925621, "sharp_price_player_1": 3.69, "sharp_price_player_2": 1.271}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 5658}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 5051}, "match_version_count": 33, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": true, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": true, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, picked_thin_history, picked_thin_stat_history, qualification_identity_mismatch, surface_missing

## 10. Match 131444 - R. Jodar vs D. Vallejo

- Date/start: 2026-10-01T01:00:00+00:00
- Tournament: Tokyo (ATP_TOUR, HARD, best of 3, R32)
- Picked: R. Jodar at 0.8382
- Actual winner: D. Vallejo (2 - 0)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-09-30T02:03:32.030076+00:00; finalized: 2026-09-30T02:04:08.347636+00:00
- Active agents: 2; probability spread: 0.0281
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.8242 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.8523 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=None
- Picked history: {"days_since_last_match": 5, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-25T18:25:00+00:00", "prior_10_wins": 7, "prior_matches": 64, "prior_stat_matches": 5, "prior_surface_matches": 1, "prior_surface_stat_matches": 1, "prior_surface_win_rate": 0.0, "prior_surface_wins": 0, "prior_win_rate": 0.75, "prior_wins": 48}
- Opponent history: {"days_since_last_match": 1, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-29T05:35:00+00:00", "prior_10_wins": 5, "prior_matches": 62, "prior_stat_matches": 8, "prior_surface_matches": 5, "prior_surface_stat_matches": 5, "prior_surface_win_rate": 0.6, "prior_surface_wins": 3, "prior_win_rate": 0.6612903225806451, "prior_wins": 41}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4923}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4656}, "match_version_count": 30, "name_flag_mismatch": false, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, picked_thin_surface_history

## 11. Match 131790 - G. Cadenasso vs M. Giunta

- Date/start: 2026-10-07T18:00:00+00:00
- Tournament: Palermo (Italy) - Qualification (CHALLENGER, CLAY, best of 3, R16)
- Picked: G. Cadenasso at 0.8331
- Actual winner: M. Giunta (0 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-06T22:05:06.623801+00:00; finalized: 2026-10-06T22:06:30.924884+00:00
- Active agents: 2; probability spread: 0.0572
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.8617 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8044 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 6, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-30T08:35:00+00:00", "prior_10_wins": 3, "prior_matches": 29, "prior_stat_matches": 6, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.5, "prior_surface_wins": 2, "prior_win_rate": 0.4827586206896552, "prior_wins": 14}
- Opponent history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-06T09:50:00+00:00", "prior_10_wins": 6, "prior_matches": 24, "prior_stat_matches": 12, "prior_surface_matches": 8, "prior_surface_stat_matches": 8, "prior_surface_win_rate": 0.625, "prior_surface_wins": 5, "prior_win_rate": 0.5, "prior_wins": 12}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4286}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4402}, "match_version_count": 27, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, qualification_identity_mismatch

## 12. Match 131554 - T. Ichikawa vs B. Bayldon

- Date/start: 2026-10-04T05:00:00+00:00
- Tournament: Wuning 3 (China) - Qualification (CHALLENGER, HARD, best of 3, SF)
- Picked: T. Ichikawa at 0.8306
- Actual winner: B. Bayldon (2 - 0)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-03T19:05:39.157210+00:00; finalized: 2026-10-03T19:06:17.366250+00:00
- Active agents: 2; probability spread: 0.0325
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.8144 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.8468 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 19, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-14T05:05:00+00:00", "prior_10_wins": 5, "prior_matches": 11, "prior_stat_matches": 4, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.5, "prior_surface_wins": 2, "prior_win_rate": 0.5454545454545454, "prior_wins": 6}
- Opponent history: {"days_since_last_match": null, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": null, "prior_10_wins": 0, "prior_matches": 0, "prior_stat_matches": 0, "prior_surface_matches": 0, "prior_surface_stat_matches": 0, "prior_surface_win_rate": null, "prior_surface_wins": 0, "prior_win_rate": null, "prior_wins": 0}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 6142}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4612}, "match_version_count": 26, "name_flag_mismatch": false, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": true, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, opponent_no_visible_history, picked_thin_stat_history

## 13. Match 131362 - C. Ruud vs L. Darderi

- Date/start: 2026-09-30T01:00:00+00:00
- Tournament: Tokyo (ATP_TOUR, HARD, best of 3, R32)
- Picked: C. Ruud at 0.8291
- Actual winner: L. Darderi (2 - 0)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-09-28T07:03:49.519638+00:00; finalized: 2026-09-28T07:04:26.862455+00:00
- Active agents: 2; probability spread: 0.0261
  - AGENT_1_SURFACE_ELO: PREDICTED side=2 p=0.8421 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=2 p=0.8160 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=None
- Picked history: {"days_since_last_match": 3, "h2h_prior_matches": 1, "h2h_prior_wins": 1, "last_match_date": "2026-09-25T12:20:00+00:00", "prior_10_wins": 6, "prior_matches": 42, "prior_stat_matches": 5, "prior_surface_matches": 0, "prior_surface_stat_matches": 0, "prior_surface_win_rate": null, "prior_surface_wins": 0, "prior_win_rate": 0.6666666666666666, "prior_wins": 28}
- Opponent history: {"days_since_last_match": 20, "h2h_prior_matches": 1, "h2h_prior_wins": 0, "last_match_date": "2026-09-08T01:15:00+00:00", "prior_10_wins": 6, "prior_matches": 54, "prior_stat_matches": 6, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.75, "prior_surface_wins": 3, "prior_win_rate": 0.6111111111111112, "prior_wins": 33}
- Market: {"model_minus_sharp": 0.16881713696701062, "offered_best_price": 1.47, "offered_bookmakers": 5, "sharp_as_of": "2026-10-01T01:05:37.536763+00:00", "sharp_favourite_side": 2, "sharp_opponent_probability": 0.33975113696701065, "sharp_picked_probability": 0.6602488630329894, "sharp_price_player_1": 2.86, "sharp_price_player_2": 1.4717}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4471}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4620}, "match_version_count": 38, "name_flag_mismatch": false, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, model_much_above_sharp, picked_thin_surface_history

## 14. Match 131781 - H. Moriya vs K. Saitoh

- Date/start: 2026-10-07T03:00:00+00:00
- Tournament: Wuning 3 (China) - Qualification (CHALLENGER, HARD, best of 3, R16)
- Picked: H. Moriya at 0.8234
- Actual winner: K. Saitoh (0 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-10-06T22:05:06.623801+00:00; finalized: 2026-10-06T22:06:30.924884+00:00
- Active agents: 2; probability spread: 0.0350
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.8059 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8409 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=INSUFFICIENT_MATCHUP_SUPPORT
- Picked history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-06T03:55:00+00:00", "prior_10_wins": 6, "prior_matches": 47, "prior_stat_matches": 11, "prior_surface_matches": 7, "prior_surface_stat_matches": 7, "prior_surface_win_rate": 0.5714285714285714, "prior_surface_wins": 4, "prior_win_rate": 0.48936170212765956, "prior_wins": 23}
- Opponent history: {"days_since_last_match": 0, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-10-06T04:40:00+00:00", "prior_10_wins": 4, "prior_matches": 5, "prior_stat_matches": 5, "prior_surface_matches": 5, "prior_surface_stat_matches": 5, "prior_surface_win_rate": 0.8, "prior_surface_wins": 4, "prior_win_rate": 0.8, "prior_wins": 4}
- Market: {"model_minus_sharp": null, "offered_best_price": null, "offered_bookmakers": null, "sharp_as_of": null, "sharp_favourite_side": null, "sharp_opponent_probability": null, "sharp_picked_probability": null, "sharp_price_player_1": null, "sharp_price_player_2": null}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4478}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 6139}, "match_version_count": 27, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, no_pre_pick_sharp, opponent_strong_surface_form, qualification_identity_mismatch

## 15. Match 117920 - M. Zhukov vs T. Loof

- Date/start: 2026-09-13T11:00:00+00:00
- Tournament: Guangzhou 2 (CHALLENGER, HARD, best of 3, SF)
- Picked: M. Zhukov at 0.8155
- Actual winner: T. Loof (1-2)
- Result source: SPORTRADAR; observations=2; disagree=False
- Picked at: 2026-09-12T13:03:42.141410+00:00; finalized: 2026-09-12T13:10:57.468613+00:00
- Active agents: 2; probability spread: 0.0384
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.7963 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8347 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=None
- Picked history: {"days_since_last_match": null, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": null, "prior_10_wins": 0, "prior_matches": 0, "prior_stat_matches": 0, "prior_surface_matches": 0, "prior_surface_stat_matches": 0, "prior_surface_win_rate": null, "prior_surface_wins": 0, "prior_win_rate": null, "prior_wins": 0}
- Opponent history: {"days_since_last_match": null, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": null, "prior_10_wins": 0, "prior_matches": 0, "prior_stat_matches": 0, "prior_surface_matches": 0, "prior_surface_stat_matches": 0, "prior_surface_win_rate": null, "prior_surface_wins": 0, "prior_win_rate": null, "prior_wins": 0}
- Market: {"model_minus_sharp": 0.25948269389383716, "offered_best_price": 1.69, "offered_bookmakers": 6, "sharp_as_of": "2026-09-13T10:05:30.772992+00:00", "sharp_favourite_side": 1, "sharp_opponent_probability": 0.4439581938938372, "sharp_picked_probability": 0.5560418061061628, "sharp_price_player_1": 1.7246, "sharp_price_player_2": 2.16}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4507}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4508}, "match_version_count": 15, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": true, "result_observation_count": 2, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": false}
- Evidence flags: agent3_abstained, model_much_above_sharp, opponent_no_visible_history, picked_thin_history, picked_thin_stat_history, picked_thin_surface_history, qualification_identity_mismatch

## 16. Match 131463 - L. Lokoli vs S. Pieri

- Date/start: 2026-10-01T08:00:00+00:00
- Tournament: Bari (Italy) - Qualification (CHALLENGER, CLAY, best of 3, R16)
- Picked: L. Lokoli at 0.8043
- Actual winner: S. Pieri (0 - 2)
- Result source: API_TENNIS; observations=1; disagree=False
- Picked at: 2026-09-30T02:03:32.030076+00:00; finalized: 2026-09-30T02:04:08.347636+00:00
- Active agents: 2; probability spread: 0.0170
  - AGENT_1_SURFACE_ELO: PREDICTED side=1 p=0.7958 abstain=None
  - AGENT_2_HIERARCHICAL_MARKOV: PREDICTED side=1 p=0.8127 abstain=None
  - AGENT_3_INTRANSITIVITY: ABSTAINED side=None p= abstain=None
- Picked history: {"days_since_last_match": 1, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-29T13:50:00+00:00", "prior_10_wins": 4, "prior_matches": 34, "prior_stat_matches": 4, "prior_surface_matches": 1, "prior_surface_stat_matches": 1, "prior_surface_win_rate": 1.0, "prior_surface_wins": 1, "prior_win_rate": 0.5294117647058824, "prior_wins": 18}
- Opponent history: {"days_since_last_match": 1, "h2h_prior_matches": 0, "h2h_prior_wins": 0, "last_match_date": "2026-09-29T13:35:00+00:00", "prior_10_wins": 5, "prior_matches": 39, "prior_stat_matches": 5, "prior_surface_matches": 4, "prior_surface_stat_matches": 4, "prior_surface_win_rate": 0.5, "prior_surface_wins": 2, "prior_win_rate": 0.46153846153846156, "prior_wins": 18}
- Market: {"model_minus_sharp": 0.21959188914198935, "offered_best_price": 1.63, "offered_bookmakers": 8, "sharp_as_of": "2026-10-01T09:01:21.104694+00:00", "sharp_favourite_side": 1, "sharp_opponent_probability": 0.41533788914198944, "sharp_picked_probability": 0.5846621108580107, "sharp_price_player_1": 1.641, "sharp_price_player_2": 2.31}
- Data quality: {"identity": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4301}, "identity_opponent": {"bridge_events": 0, "canonical_name_peers": 1, "max_alias_peers": 1, "player_id": 4582}, "match_version_count": 30, "name_flag_mismatch": true, "participants_visible_at_pick": true, "pit_version_is_latest": true, "qualifying_flag": false, "result_observation_count": 1, "result_states_disagree": false, "surface_conflicts_edition": false, "surface_missing": false, "tournament_name_has_qualification": true}
- Evidence flags: agent3_abstained, model_much_above_sharp, picked_thin_stat_history, picked_thin_surface_history, qualification_identity_mismatch

