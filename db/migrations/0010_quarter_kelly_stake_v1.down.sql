BEGIN;

ALTER TABLE decisions
    DROP CONSTRAINT IF EXISTS chk_decisions_staking_rule_coherent,
    DROP CONSTRAINT IF EXISTS chk_decisions_no_positive_stake_without_bet,
    DROP CONSTRAINT IF EXISTS chk_decisions_suggested_stake_fraction,
    DROP CONSTRAINT IF EXISTS chk_decisions_staking_decimal_price,
    DROP CONSTRAINT IF EXISTS chk_decisions_staking_probability,
    DROP COLUMN IF EXISTS suggested_stake_fraction,
    DROP COLUMN IF EXISTS quarter_kelly_fraction,
    DROP COLUMN IF EXISTS full_kelly_fraction,
    DROP COLUMN IF EXISTS staking_decimal_price,
    DROP COLUMN IF EXISTS staking_probability,
    DROP COLUMN IF EXISTS staking_rule_version;

COMMIT;
