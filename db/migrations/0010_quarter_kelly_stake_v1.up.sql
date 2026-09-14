BEGIN;

ALTER TABLE decisions
    ADD COLUMN staking_rule_version TEXT,
    ADD COLUMN staking_probability NUMERIC(18, 12),
    ADD COLUMN staking_decimal_price NUMERIC(18, 8),
    ADD COLUMN full_kelly_fraction NUMERIC(18, 12),
    ADD COLUMN quarter_kelly_fraction NUMERIC(18, 12),
    ADD COLUMN suggested_stake_fraction NUMERIC(18, 12),
    ADD CONSTRAINT chk_decisions_staking_probability
        CHECK (
            staking_probability IS NULL
            OR (staking_probability >= 0 AND staking_probability <= 1)
        ),
    ADD CONSTRAINT chk_decisions_staking_decimal_price
        CHECK (staking_decimal_price IS NULL OR staking_decimal_price > 1),
    ADD CONSTRAINT chk_decisions_suggested_stake_fraction
        CHECK (
            suggested_stake_fraction IS NULL
            OR (suggested_stake_fraction >= 0 AND suggested_stake_fraction <= 0.020000000000)
        ),
    ADD CONSTRAINT chk_decisions_no_positive_stake_without_bet
        CHECK (
            decision_status = 'BET'
            OR suggested_stake_fraction IS NULL
            OR suggested_stake_fraction = 0
        ),
    ADD CONSTRAINT chk_decisions_staking_rule_coherent
        CHECK (
            (
                staking_rule_version IS NULL
                AND staking_probability IS NULL
                AND staking_decimal_price IS NULL
                AND full_kelly_fraction IS NULL
                AND quarter_kelly_fraction IS NULL
                AND suggested_stake_fraction IS NULL
            )
            OR (
                staking_rule_version = 'QUARTER_KELLY_CAP_2_V1'
                AND staking_probability IS NOT NULL
                AND staking_decimal_price IS NOT NULL
                AND full_kelly_fraction IS NOT NULL
                AND quarter_kelly_fraction IS NOT NULL
                AND suggested_stake_fraction IS NOT NULL
            )
        );

COMMIT;
