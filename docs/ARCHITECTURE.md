# Architecture

Target structure only. Nothing below the slate generator is implemented in this
bootstrap phase.

```text
SLATE GENERATOR
        |
        +------> AGENT 1
        |       Surface-adjusted Elo
        |
        +------> AGENT 2
        |       Serve/Return
        |       Exact hierarchical Markov
        |
        +------> AGENT 3
                Intransitivity graph

               BLIND

                 |
              MANAGER
                 |
               VETO
                 |
           PRICE SCANNER
                 |
        DECISION + CLV LOG
```

## Blind isolation

Prediction agents may NOT consume:

```text
sportsbook prices
market consensus
closing lines
opening lines
other-agent outputs
```

The manager/scanner layer is where market comparison begins.

## High-risk engineering invariants

### Point-in-time

Every historical computation must ultimately support `as_of`. No future result may
influence an earlier prediction.

### Identity

Ambiguous player-name resolution must fail closed. Unmatched names must be logged.

### Tennis scoring

Future Agent 2 must support exact:

```text
point -> game -> tiebreak -> set -> match
```

with event/year-specific rules.

### Agent 3

The intransitivity threshold must come from the historical score distribution, not ROI
tuning.

### Manager

No directional disagreement may become a bet.

### Veto

Veto can only kill.

### Market

Bookmaker odds must not be model features.
