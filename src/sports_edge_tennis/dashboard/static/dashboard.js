// Sports Edge Tennis — client-side evidence enhancements.
// Core pages remain server-rendered. These read-only overlays progressively add
// published-pick history and prospective paper-bankroll evidence when available.
(function () {
  "use strict";

  function el(tag, className, text) {
    const node = document.createElement(tag);
    if (className) node.className = className;
    if (text !== undefined && text !== null) node.textContent = String(text);
    return node;
  }

  function percent(raw, digits) {
    if (raw === null || raw === undefined || raw === "") return "—";
    const value = Number(raw);
    if (!Number.isFinite(value)) return "—";
    return `${(value * 100).toFixed(digits || 0)}%`;
  }

  function utcTime(raw) {
    if (!raw) return "—";
    const parsed = new Date(raw);
    if (Number.isNaN(parsed.getTime())) return "—";
    return `${parsed.toISOString().slice(11, 16)} UTC`;
  }

  function lifecycleState(row) {
    const states = {
      VALUE_BET_NOW: ["Value bet now", "HIGH_CONFIDENCE_VALUE"],
      PICK_ONLY_NO_VALUE_NOW: ["Pick still active · no value now", "HIGH_CONFIDENCE_PICK"],
      PICK_ACTIVE_PRICE_PENDING: ["Pick active · price pending", "PRICE_PENDING"],
      SAFETY_VETOED: ["Safety vetoed", "VETOED"],
      MODEL_NO_LONGER_QUALIFIES: ["Original pick kept · latest model no longer qualifies", "NO_BET"],
      MODEL_CHANGED: ["Model updated", "PRICE_PENDING"],
    };
    return states[row.lifecycle_state] || [row.lifecycle_state || "Unknown", "neutral"];
  }

  async function mountPickLifecycle() {
    if (window.location.pathname !== "/today") return;

    const params = new URLSearchParams(window.location.search);
    const slateDate = params.get("date") || new Date().toISOString().slice(0, 10);

    let response;
    try {
      response = await fetch(`/api/dashboard/pick-lifecycle?date=${encodeURIComponent(slateDate)}`, {
        headers: { Accept: "application/json" },
      });
    } catch (_error) {
      return;
    }
    if (!response.ok) return;

    const payload = await response.json();
    if (!payload.picks || payload.picks.length === 0) return;

    const heading = el("section", "section-title lifecycle-heading");
    heading.appendChild(el("h2", null, "Published Pick Timeline"));
    heading.appendChild(
      el(
        "p",
        null,
        "Once a model pick has been shown, it stays here. Later model or price changes update its status instead of making the earlier pick disappear."
      )
    );

    const wrap = el("section", "table-wrap lifecycle-list");
    wrap.setAttribute("aria-label", "Published pick timeline");
    const table = el("table", "data");
    const thead = el("thead");
    const headerRow = el("tr");
    ["First shown", "Match", "Original pick", "Latest model", "Latest status"].forEach((label) => {
      const th = el("th", null, label);
      th.setAttribute("scope", "col");
      headerRow.appendChild(th);
    });
    thead.appendChild(headerRow);
    table.appendChild(thead);

    const tbody = el("tbody");
    payload.picks.forEach((row) => {
      const tr = el("tr");
      tr.appendChild(el("td", null, utcTime(row.first_shown_at)));

      const matchCell = el("td");
      matchCell.appendChild(el("strong", null, row.match));
      matchCell.appendChild(document.createElement("br"));
      matchCell.appendChild(el("span", "stat-note", row.tournament || "Tournament unavailable"));
      tr.appendChild(matchCell);

      const original = el("td");
      original.appendChild(el("strong", null, row.first_pick || "—"));
      original.appendChild(document.createElement("br"));
      original.appendChild(el("span", "stat-note", `${percent(row.first_probability)} at first display`));
      tr.appendChild(original);

      const latest = el("td");
      if (row.latest_pick) {
        latest.appendChild(el("strong", null, row.latest_pick));
        latest.appendChild(document.createElement("br"));
        latest.appendChild(el("span", "stat-note", percent(row.latest_probability)));
      } else {
        latest.appendChild(el("span", "stat-note", "No current qualifying model pick"));
      }
      tr.appendChild(latest);

      const statusCell = el("td");
      const [label, badgeClass] = lifecycleState(row);
      statusCell.appendChild(el("span", `badge badge-${badgeClass}`, label));
      if (row.lifecycle_state === "MODEL_CHANGED" && row.latest_pick) {
        statusCell.appendChild(document.createElement("br"));
        statusCell.appendChild(el("span", "stat-note", `Latest pick: ${row.latest_pick}`));
      } else if (row.latest_market_price) {
        statusCell.appendChild(document.createElement("br"));
        statusCell.appendChild(
          el("span", "stat-note", `${row.latest_bookmaker || "Bookmaker"} @ ${row.latest_market_price}`)
        );
      }
      tr.appendChild(statusCell);
      tbody.appendChild(tr);
    });
    table.appendChild(tbody);
    wrap.appendChild(table);

    // Mount only into the explicit anchor. These evidence overlays belong on
    // Results, never on the Today picks page.
    const mount = document.querySelector('[data-mount="pick-lifecycle"]');
    if (!mount) return;
    mount.appendChild(heading);
    mount.appendChild(wrap);
  }

  function bankrollStat(label, value, note) {
    const card = el("div", "stat");
    card.appendChild(el("span", "stat-value", value));
    card.appendChild(el("span", "stat-label", label));
    if (note) card.appendChild(el("span", "stat-note", note));
    return card;
  }

  async function mountPaperBankroll() {
    if (window.location.pathname !== "/bet-history") return;

    let response;
    try {
      response = await fetch("/api/dashboard/paper-bankroll", {
        headers: { Accept: "application/json" },
      });
    } catch (_error) {
      return;
    }
    if (!response.ok) return;

    const data = await response.json();
    const section = el("section", "paper-bankroll-section");
    const heading = el("div", "section-title");
    heading.appendChild(el("h2", null, "$1,000 Live Paper Bankroll"));
    heading.appendChild(
      el(
        "p",
        null,
        "Simulation only. Every qualifying live bet is replayed from its recorded publication-time price and stored quarter-Kelly stake. No real wager is placed."
      )
    );
    section.appendChild(heading);

    if (!data.initialized) {
      const panel = el("div", "notice");
      panel.appendChild(el("h3", null, "Paper bankroll is ready"));
      panel.appendChild(
        el(
          "p",
          null,
          "Tracking begins when a forward BET has reproducible publication-time odds and stored staking evidence. Missing historical prices are never guessed."
        )
      );
      section.appendChild(panel);
    } else {
      const grid = el("div", "stat-grid");
      grid.appendChild(bankrollStat("Starting Bankroll", `$${data.starting_bankroll}`));
      grid.appendChild(bankrollStat("Current Bankroll", `$${data.current_bankroll}`));
      grid.appendChild(bankrollStat("Realized P/L", `$${data.realized_profit}`));
      grid.appendChild(bankrollStat("Bankroll ROI", data.roi));
      grid.appendChild(bankrollStat("Record", `${data.wins}-${data.losses}`, `${data.pending} pending`));
      grid.appendChild(bankrollStat("Max Drawdown", data.max_drawdown));
      section.appendChild(grid);

      const panel = el("div", "notice");
      panel.appendChild(
        el(
          "p",
          null,
          `Paper bets: ${data.bets} · Settled: ${data.settled} · Open exposure: $${data.open_exposure} · Legacy bets excluded rather than guessed: ${data.legacy_bets_excluded}`
        )
      );
      section.appendChild(panel);
    }

    const mount = document.querySelector('[data-mount="paper-bankroll"]');
    if (!mount) return;
    mount.appendChild(section);
  }

  document.addEventListener("DOMContentLoaded", function () {
    if (document.querySelector('[data-mount="pick-lifecycle"]')) {
      void mountPickLifecycle();
    }
    if (document.querySelector('[data-mount="paper-bankroll"]')) {
      void mountPaperBankroll();
    }
  });
})();
