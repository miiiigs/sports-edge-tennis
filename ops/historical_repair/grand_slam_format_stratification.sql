-- Read-only Grand Slam match-format stratification for the 2026-10-20 review.
-- Run against a read-only connection. No writes.

WITH latest AS (
    SELECT DISTINCT ON (mv.match_id)
        mv.match_id,
        m.tournament_edition_id,
        mv.tour_scope,
        mv.competition_tier,
        mv.qualifying_flag,
        mv.best_of_sets,
        mv.round_code
    FROM match_versions mv
    JOIN matches m ON m.match_id = mv.match_id
    ORDER BY mv.match_id, mv.as_of DESC, mv.source_observed_at DESC,
             mv.match_version_id DESC
)
SELECT
    t.canonical_name,
    te.season_year,
    latest.tour_scope::text AS tour_scope,
    latest.competition_tier::text AS competition_tier,
    latest.qualifying_flag,
    latest.best_of_sets,
    CASE
        WHEN t.canonical_name ILIKE '%wheelchair%' THEN 'wheelchair'
        WHEN t.canonical_name ILIKE '%doubles%' THEN 'doubles'
        WHEN t.canonical_name ILIKE '%women%' THEN 'women'
        WHEN latest.tour_scope::text = 'ATP_MAIN' THEN 'men_singles_or_unmarked'
        ELSE 'other'
    END AS event_class,
    count(*) AS matches
FROM latest
JOIN tournament_editions te
  ON te.tournament_edition_id = latest.tournament_edition_id
JOIN tournaments t ON t.tournament_id = te.tournament_id
WHERE (
    t.canonical_name ILIKE '%australian open%'
    OR t.canonical_name ILIKE '%roland garros%'
    OR t.canonical_name ILIKE '%wimbledon%'
    OR t.canonical_name ILIKE '%us open%'
)
GROUP BY t.canonical_name, te.season_year, latest.tour_scope,
         latest.competition_tier, latest.qualifying_flag, latest.best_of_sets
ORDER BY te.season_year DESC, t.canonical_name, latest.qualifying_flag,
         latest.best_of_sets;
