from sports_edge_tennis.odds.reconciliation import _participant_name_compatible


def test_participant_name_exact_and_diacritic_normalization():
    assert _participant_name_compatible("João Fonseca", "Joao Fonseca")


def test_participant_name_token_order_is_compatible():
    assert _participant_name_compatible("Balshaw, Felix", "Felix Balshaw")


def test_participant_name_initial_is_compatible():
    assert _participant_name_compatible("F. Balshaw", "Felix Balshaw")
    assert _participant_name_compatible("Balshaw F", "Felix Balshaw")


def test_participant_name_rejects_missing_token():
    assert not _participant_name_compatible("Balshaw", "Felix Balshaw")


def test_participant_name_rejects_fuzzy_surname_substitution():
    assert not _participant_name_compatible("Felix Balshaw", "Felix Bellshaw")


def test_participant_name_rejects_non_initial_prefix():
    assert not _participant_name_compatible("Fe Balshaw", "Felix Balshaw")


def test_participant_name_rejects_extra_middle_name():
    assert not _participant_name_compatible("Felix J Balshaw", "Felix Balshaw")
