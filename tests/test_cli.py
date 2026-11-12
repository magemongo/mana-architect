from mana_architect.cli import build_parser


def test_build_deck_strategy_option_is_optional() -> None:
    parser = build_parser()
    legacy = parser.parse_args(
        ["build-deck", "commander", "--output", "draft.json"]
    )
    guided = parser.parse_args(
        [
            "build-deck",
            "commander",
            "--strategy",
            "tokens",
            "--output",
            "guided.json",
        ]
    )

    assert legacy.strategy is None
    assert guided.strategy == "tokens"
