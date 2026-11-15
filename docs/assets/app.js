/* Runway Rhythms dashboard: static Plotly front end over pre-computed JSON. */
(() => {
  "use strict";

  const DOW = ["Mon", "Tue", "Wed", "Thu", "Fri", "Sat", "Sun"];
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const HOURS = Array.from({ length: 24 }, (_, h) => String(h).padStart(2, "0"));
  const UK = new Set(["EGLL", "EGKK", "EGCC"]);
  const SEQ_BLUE = [[0, "#cde2fb"], [0.17, "#9ec5f4"], [0.33, "#6da7ec"], [0.5, "#3987e5"], [0.67, "#256abf"], [0.83, "#184f95"], [1, "#0d366b"]];
  const SEQ_ORANGE = [[0, "#fde8de"], [0.25, "#f8b997"], [0.5, "#eb6834"], [0.75, "#b8431a"], [1, "#6e260c"]];
  const CURVE_LABEL = { gust_kmh: "Max wind gust (km/h)", precip_mm: "Daily rainfall (mm)", snow_cm: "Daily snowfall (cm)", temp_max: "Max temperature (°C)" };

  const state = { origin: "EGKK", dest: "LEPA", kind: "dh", period: "all", src: "obs", curve: "gust_kmh" };
  const cache = {};
  let META, PUNCT, DELAYS;

  const $ = (id) => document.getElementById(id);
  const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
  const apt = (icao) => META.airports.find((a) => a.icao === icao);
  const aptLabel = (icao) => (icao === "ALL" ? "all destinations" : `${apt(icao).iata} ${apt(icao).city}`);
  const fmt = (v, d = 1) => (v == null || Number.isNaN(v) ? "–" : Number(v).toLocaleString("en-GB", { maximumFractionDigits: d, minimumFractionDigits: d }));
  const pct = (v, d = 0) => (v == null ? "–" : `${fmt(v * 100, d)}%`);

  async function getJSON(path) {
    const r = await fetch(path);
    if (!r.ok) throw new Error(`${path}: ${r.status}`);
    return r.json();
  }
  async function traffic(origin) {
    if (!cache[origin]) cache[origin] = await getJSON(`data/traffic/${origin}.json`);
    return cache[origin];
  }

  /* ------------------------------------------------------------------ plotly helpers */
  function baseLayout(extra = {}) {
    const ink = css("--ink-2"), grid = css("--grid"), axis = css("--axis");
    const ax = { gridcolor: grid, linecolor: axis, zerolinecolor: axis, tickfont: { color: ink, size: 11 }, title: { font: { color: ink, size: 12 } }, automargin: true };
    return Object.assign({
      paper_bgcolor: "rgba(0,0,0,0)",
      plot_bgcolor: "rgba(0,0,0,0)",
      font: { family: "system-ui, -apple-system, Segoe UI, sans-serif", color: ink, size: 12 },
      margin: { l: 50, r: 16, t: 10, b: 40 },
      hoverlabel: { bgcolor: css("--surface"), bordercolor: axis, font: { color: css("--ink") } },
      legend: { orientation: "h", y: 1.12, x: 0, font: { size: 12 } },
      xaxis: { ...ax }, yaxis: { ...ax },
    }, extra, {
      xaxis: { ...ax, ...(extra.xaxis || {}) },
      yaxis: { ...ax, ...(extra.yaxis || {}) },
    });
  }
  const CONFIG = { displayModeBar: false, responsive: true };
  function plot(id, data, layout) { Plotly.react($(id), data, baseLayout(layout), CONFIG); }
  function empty(id, msg) { Plotly.purge($(id)); $(id).innerHTML = `<div class="empty">${msg}</div>`; }
  function clearEmpty(id) { const e = $(id).querySelector(".empty"); if (e) e.remove(); }

  function tiles(id, items) {
    $(id).innerHTML = items.map((t) => `<div class="tile"><div class="k">${t.k}</div><div class="v ${t.cls || ""}">${t.v}</div><div class="d">${t.d || ""}</div></div>`).join("");
  }

  /* ------------------------------------------------------------------ controls */
  function fillOrigins() {
    const opts = META.airports
      .filter((a) => META.routes[a.icao] && META.routes[a.icao].length)
      .sort((a, b) => a.city.localeCompare(b.city))
      .map((a) => `<option value="${a.icao}">${a.iata} · ${a.city} ${a.name !== a.city ? "– " + a.name : ""}</option>`);
    $("origin").innerHTML = opts.join("");
    $("origin").value = state.origin;
  }
  function fillDests() {
    const routes = META.routes[state.origin] || [];
    const opts = [`<option value="ALL">All destinations</option>`].concat(
      routes.map(([d, n]) => { const a = apt(d); return `<option value="${d}">${a.iata} · ${a.city} (${n.toLocaleString("en-GB")} flights)</option>`; })
    );
    $("dest").innerHTML = opts.join("");
    if (state.dest !== "ALL" && !routes.some(([d]) => d === state.dest)) state.dest = routes.length ? routes[0][0] : "ALL";
    $("dest").value = state.dest;
  }
  function setSeg(key, value) {
    state[key] = value;
    document.querySelectorAll(`.seg[data-key="${key}"] button`).forEach((b) => b.setAttribute("aria-checked", String(b.dataset.v === value)));
  }
  function bind() {
    $("origin").addEventListener("change", (e) => { state.origin = e.target.value; fillDests(); render(); });
    $("dest").addEventListener("change", (e) => { state.dest = e.target.value; render(); });
    $("swap").addEventListener("click", () => {
      if (state.dest === "ALL") return;
      const o = state.origin; state.origin = state.dest; state.dest = o;
      $("origin").value = state.origin; fillDests(); render();
    });
    document.querySelectorAll(".seg").forEach((seg) => seg.addEventListener("click", (e) => {
      const b = e.target.closest("button");
      if (!b || b.disabled) return;
      setSeg(seg.dataset.key, b.dataset.v);
      seg.dataset.key === "curve" ? renderCurve() : render();
    }));
    window.matchMedia("(prefers-color-scheme: dark)").addEventListener("change", render);
  }

  /* ------------------------------------------------------------------ 1. heatmap */
  function tableFor(entry, src, kind, period) {
    const t = entry[src] && entry[src][kind];
    if (!t) return null;
    if (period !== "diff") return t[period];
    return t.vac.map((row, i) => row.map((v, j) => (v == null || t.non[i][j] == null ? null : v - t.non[i][j])));
  }

  function renderHeatmap(entry) {
    const { kind, period } = state;
    const src = state.src === "fc" && entry.fc ? "fc" : "obs";
    const z = tableFor(entry, src, kind, period);
    const year = src === "fc" ? META.forecast_year : META.traffic_year;
    const unit = kind === "dh" ? "departures in this hour (avg)" : "flights per day (avg)";
    const isDiff = period === "diff";
    const x = kind === "dh" ? HOURS.map((h) => `${h}:00`) : DOW;
    const y = kind === "dh" ? DOW : MONTHS;
    const text = z.map((row, i) => row.map((v, j) => {
      const where = kind === "dh" ? `${y[i]} ${HOURS[j]}:00–${HOURS[(j + 1) % 24]}:00` : `${y[i]} · ${x[j]}`;
      return v == null ? `${where}<br>no days in this period` : `${where}<br>${isDiff ? (v >= 0 ? "+" : "") : ""}${fmt(v, 2)} ${unit}`;
    }));
    let zmax = 0; z.forEach((r) => r.forEach((v) => { if (v != null) zmax = Math.max(zmax, Math.abs(v)); }));
    const trace = {
      type: "heatmap", z, x, y, text, hoverinfo: "text", xgap: 2, ygap: 2,
      colorscale: isDiff ? [[0, "#184f95"], [0.25, "#6da7ec"], [0.5, css("--div-mid")], [0.75, "#ee8a89"], [1, "#a32828"]] : SEQ_BLUE,
      zmin: isDiff ? -zmax : 0, zmax: zmax || 1, zmid: isDiff ? 0 : undefined,
      colorbar: { title: { text: isDiff ? "vacation − non" : (kind === "dh" ? "flights / hour" : "flights / day"), side: "right", font: { size: 11 } }, thickness: 12, outlinewidth: 0, tickfont: { size: 11 } },
    };
    plot("heatmap", [trace], {
      margin: { l: 50, r: 10, t: 6, b: 46 },
      xaxis: { title: { text: kind === "dh" ? "Local departure hour" : "Weekday" }, showgrid: false, tickangle: 0, dtick: kind === "dh" ? 2 : 1 },
      yaxis: { autorange: "reversed", showgrid: false },
    });
    const periodTxt = { all: "the whole year", vac: "vacation days", non: "non-vacation days", diff: "vacation days minus non-vacation days" }[period];
    $("heatmap-note").textContent = `${src === "fc" ? "Forecast" : "Observed"} ${year}, ${periodTxt}. ` +
      (kind === "dh" ? "Each cell is the average number of departures in that local hour on that weekday." : "Each cell is the average number of flights per day for that weekday in that month.") +
      (isDiff ? " Red cells are busier in vacation periods; blue cells are quieter." : "");
    return { z, src };
  }

  function peakOf(z, kind) {
    let best = { v: -1 };
    z.forEach((r, i) => r.forEach((v, j) => { if (v != null && v > best.v) best = { v, i, j }; }));
    if (best.v < 0) return "–";
    return kind === "dh" ? `${DOW[best.i]} ${HOURS[best.j]}:00` : `${DOW[best.j]}s in ${MONTHS[best.i]}`;
  }

  function renderTraffic(entry) {
    const hasFc = Boolean(entry.fc);
    $("src-fc").disabled = !hasFc;
    if (!hasFc && state.src === "fc") setSeg("src", "obs");
    const { z, src } = renderHeatmap(entry);
    const k = entry.kpi;
    const peakTable = tableFor(entry, src, state.kind, state.period === "diff" ? "all" : state.period);
    tiles("traffic-tiles", [
      { k: `Flights tracked ${META.traffic_year}`, v: entry.n.toLocaleString("en-GB"), d: `${aptLabel(state.origin)} → ${aptLabel(state.dest)}` },
      { k: "Peak slot", v: peakOf(peakTable, state.kind), d: state.kind === "dh" ? "busiest weekday-hour" : "busiest weekday-month" },
      { k: `Vacation day ${META.forecast_year}`, v: k ? fmt(k.vac_per_day, 1) : "–", d: "forecast flights per day" },
      { k: `Regular day ${META.forecast_year}`, v: k ? fmt(k.non_per_day, 1) : "–", d: "forecast flights per day" },
      { k: "Vacation uplift", v: k && k.uplift_pct != null ? `${k.uplift_pct >= 0 ? "+" : ""}${fmt(k.uplift_pct, 1)}%` : "–", cls: k && k.uplift_pct >= 0 ? "up" : "down", d: hasFc ? "vacation vs regular day" : "route too thin to forecast" },
    ]);
    $("traffic-sub").textContent = `${apt(state.origin).iata} ${apt(state.origin).name} → ${state.dest === "ALL" ? "all 29 other airports" : apt(state.dest).iata + " " + apt(state.dest).name}. Switch the heatmap layout, the period and observed or forecast data in the bar above.`;
  }

  /* ------------------------------------------------------------------ 2. forecast */
  function renderForecast(entry) {
    if (!entry.fc) {
      empty("fc-hours", "This route has too few tracked flights for a reliable forecast.");
      empty("fc-months", "Choose a busier route, or All destinations.");
      $("forecast-sub").textContent = "";
      return;
    }
    clearEmpty("fc-hours"); clearEmpty("fc-months");
    const s1 = css("--s1"), s2 = css("--s2");
    const avgDay = (t) => HOURS.map((_, h) => { let s = 0, n = 0; t.forEach((r) => { if (r[h] != null) { s += r[h]; n++; } }); return n ? s / n : null; });
    const vac = avgDay(entry.fc.dh.vac), non = avgDay(entry.fc.dh.non);
    const line = (y, name, color) => ({ type: "scatter", mode: "lines", x: HOURS.map((h) => `${h}:00`), y, name, line: { color, width: 2 }, hovertemplate: `${name}<br>%{x}: %{y:.2f} departures<extra></extra>` });
    plot("fc-hours", [line(non, "Non-vacation", s1), line(vac, "Vacation", s2)], {
      yaxis: { title: { text: "departures per hour" }, rangemode: "tozero" }, xaxis: { dtick: 3, showgrid: false }, hovermode: "x unified",
    });
    const monthAvg = (t) => t.map((r) => { const v = r.filter((x) => x != null); return v.length ? v.reduce((a, b) => a + b, 0) / v.length : null; });
    const bar = (y, name, color) => ({ type: "bar", x: MONTHS, y: monthAvg(y), name, marker: { color, cornerradius: 4 }, hovertemplate: `${name} days in %{x}: %{y:.1f} flights/day<extra></extra>` });
    plot("fc-months", [bar(entry.fc.md.non, "Non-vacation", s1), bar(entry.fc.md.vac, "Vacation", s2)], {
      barmode: "group", bargap: 0.25, bargroupgap: 0.08, yaxis: { title: { text: "flights per day" }, rangemode: "tozero" }, xaxis: { showgrid: false },
    });
    const k = entry.kpi;
    $("forecast-sub").textContent = `In ${META.forecast_year}, a vacation day on this selection is forecast at ${fmt(k.vac_per_day, 1)} flights against ${fmt(k.non_per_day, 1)} on a regular day (${k.uplift_pct >= 0 ? "+" : ""}${fmt(k.uplift_pct, 1)}%). Months with no vacation days show only one bar.`;
  }

  function renderTrafficModel() {
    const m = META.traffic_metrics;
    const imp = (a, b) => { const d = 100 * (a / b - 1); return `${d <= 0 ? "" : "+"}${fmt(d, 1)}% vs baseline`; };
    $("traffic-model").innerHTML = `
      <p>A Poisson gradient-boosted tree predicts daily flights for each of ${m.routes} routes. Its inputs are the route, weekday, month, vacation window (Christmas, February half-term, Easter, May half-term, July–August, October half-term) and public holidays in both countries. 2022 was still a recovery year, so ${META.forecast_year} forecasts are recalibrated to 2024 traffic levels using EUROCONTROL arrival counts at both airports. Hourly patterns come from each route's observed departure-hour profile for that weekday and period type.</p>
      <table><tr><th>Validation (${m.validation})</th><th>Model</th><th>Seasonal baseline</th><th></th></tr>
      <tr><td>Daily MAE (flights)</td><td>${m.daily_mae}</td><td>${m.daily_mae_baseline}</td><td>${imp(m.daily_mae, m.daily_mae_baseline)}</td></tr>
      <tr><td>Daily WAPE</td><td>${pct(m.daily_wape, 1)}</td><td>${pct(m.daily_wape_baseline, 1)}</td><td>${imp(m.daily_wape, m.daily_wape_baseline)}</td></tr>
      <tr><td>Weekly WAPE</td><td>${pct(m.weekly_wape, 1)}</td><td>${pct(m.weekly_wape_baseline, 1)}</td><td>${imp(m.weekly_wape, m.weekly_wape_baseline)}</td></tr>
      <tr><td>Vacation-day MAE</td><td>${m.vacation_mae}</td><td>${m.vacation_mae_baseline}</td><td>${imp(m.vacation_mae, m.vacation_mae_baseline)}</td></tr></table>
      <p>The baseline is the route × weekday × month average from the training weeks. Airline schedules repeat strongly, so this baseline is hard to beat within a year, and the model matches it to within about 2%. The model's advantage is that it encodes vacation windows and public holidays as features. That lets it move moving holidays (Easter, bank holidays, half-terms) to their ${META.forecast_year} dates, which a same-calendar baseline cannot do.</p>`;
  }

  /* ------------------------------------------------------------------ 3. punctuality */
  function routeKey() {
    const { origin: o, dest: d } = state;
    if (d === "ALL") return null;
    if (UK.has(o) && PUNCT[`${o}-${d}`]) return `${o}-${d}`;
    if (UK.has(d) && PUNCT[`${d}-${o}`]) return `${d}-${o}`;
    return null;
  }
  function last12(s) {
    const n = s.n.slice(-12), o = s.otp.slice(-12);
    const tot = n.reduce((a, b) => a + b, 0);
    return tot ? o.reduce((a, v, i) => a + v * n[i], 0) / tot : null;
  }
  function renderPunctuality() {
    const key = routeKey();
    const arrApt = state.dest === "ALL" ? state.origin : state.dest;
    const items = [];
    if (key) {
      clearEmpty("otp");
      const r = PUNCT[key];
      const series = [["British Airways", css("--s1")], ["Other airlines", css("--s2")], ["All airlines", css("--s3")]].filter(([g]) => r[g]);
      plot("otp", series.map(([g, c]) => ({
        type: "scatter", mode: "lines+markers", name: g, x: r[g].m, y: r[g].otp,
        line: { color: c, width: 2 }, marker: { size: 8, color: c, line: { color: css("--surface"), width: 2 } },
        customdata: r[g].m.map((_, i) => [r[g].dly[i], r[g].n[i], r[g].cxl[i]]),
        hovertemplate: `${g} %{x}<br>On time: %{y:.1f}%<br>Avg delay: %{customdata[0]:.1f} min<br>Flights: %{customdata[1]:,}<br>Cancelled: %{customdata[2]:.1f}%<extra></extra>`,
      })), { yaxis: { title: { text: "on time (≤15 min late), %" }, range: [30, 100] }, xaxis: { showgrid: false, type: "date" } });
      $("otp-title").textContent = `Route on-time performance: ${key.split("-").map((c) => apt(c).iata).join(" ↔ ")} (UK CAA)`;
      const all = r["All airlines"], ba = r["British Airways"];
      items.push(
        { k: "On time, last 12 months", v: `${fmt(last12(all), 0)}%`, d: "all airlines on this route" },
        { k: "British Airways", v: ba ? `${fmt(last12(ba), 0)}%` : "–", d: ba ? "on time, last 12 months" : "BA does not fly this route" },
        { k: "Peak-month on-time rate", v: `${fmt(all.otp_vac, 0)}%`, d: `vs ${fmt(all.otp_non, 0)}% in other months (Apr, Jul, Aug, Dec are peak months)` },
      );
    } else {
      empty("otp", "UK CAA route punctuality covers flights to and from Heathrow, Gatwick and Manchester.<br>Choose one of these at either end of the route.");
      $("otp-title").textContent = "Route on-time performance (UK CAA)";
    }
    const D = DELAYS[arrApt];
    const z = D.md;
    plot("atfm-heat", [{
      type: "heatmap", z, x: DOW, y: MONTHS, xgap: 2, ygap: 2, colorscale: SEQ_ORANGE, zmin: 0,
      hovertemplate: "%{y} · %{x}<br>%{z:.2f} min ATFM delay per arrival<extra></extra>",
      colorbar: { title: { text: "min / arrival", side: "right", font: { size: 11 } }, thickness: 12, outlinewidth: 0 },
    }], { yaxis: { autorange: "reversed", showgrid: false }, xaxis: { showgrid: false }, margin: { l: 44, r: 10, t: 6, b: 30 } });
    $("atfm-title").textContent = `Arrival ATFM delay at ${apt(arrApt).iata} ${apt(arrApt).city}, 2022–2024 (month × weekday)`;
    items.push(
      { k: `${apt(arrApt).iata} ATFM delay, vacation days`, v: `${fmt(D.delay_vac, 2)} min`, d: `per arrival vs ${fmt(D.delay_non, 2)} min on other days` },
      { k: `${apt(arrApt).iata} disrupted days`, v: pct(D.disrupted_vac, 1), d: `of vacation days vs ${pct(D.disrupted_non, 1)} of other days` },
    );
    tiles("punct-tiles", items);
    $("punct-sub").textContent = "On-time performance on UK routes comes from the UK CAA (2022–2025). Arrival delay at the destination comes from EUROCONTROL air-traffic-flow (ATFM) regulations (2022–2024). A disrupted day is one where more than 5% of arrivals were held 15 minutes or more.";
  }

  /* ------------------------------------------------------------------ 4. weather */
  function weatherAirport() { return state.dest === "ALL" ? state.origin : state.dest; }

  function renderCurve() {
    const icao = weatherAirport();
    const c = DELAYS[icao].curves[state.curve];
    plot("curve", [{
      type: "scatter", mode: "lines+markers", x: c.x, y: c.y, line: { color: css("--s1"), width: 2 }, marker: { size: 8, color: css("--s1"), line: { color: css("--surface"), width: 2 } },
      hovertemplate: `${CURVE_LABEL[state.curve]}: %{x}<br>Predicted: %{y:.2f} min / arrival<extra></extra>`,
    }], { xaxis: { title: { text: CURVE_LABEL[state.curve] } }, yaxis: { title: { text: "min delay / arrival" }, rangemode: "tozero" }, showlegend: false });
  }

  function renderWeather() {
    const icao = weatherAirport();
    const D = DELAYS[icao], A = apt(icao), net = DELAYS.ALL;
    $("weather-sub").textContent = `Arrivals at ${A.iata} ${A.name}. Daily weather comes from Open-Meteo, joined to EUROCONTROL arrival delay. Weather-coded delay covers ATFM regulations with weather (W) or de-icing (D) reason codes. Model views hold every other input at a typical day for ${A.iata}. At some hubs (CDG for example) the calmest days are not the best ones, because still, high-pressure weather brings fog and low visibility.`;
    const best = D.scenarios.delay[0];
    const worstI = D.scenarios.delay.indexOf(Math.max(...D.scenarios.delay));
    tiles("weather-tiles", [
      { k: "Weather share of delay", v: pct(D.weather_share, 0), d: `of ATFM arrival delay at ${A.iata} (network: ${pct(net.weather_share, 0)})` },
      { k: "Average ATFM delay", v: `${fmt(D.avg_delay, 2)} min`, d: "per arrival, 2022–2024" },
      { k: "Worst modelled scenario", v: D.scenarios.name[worstI], d: `${fmt(D.scenarios.delay[worstI], 2)} min per arrival vs ${fmt(best, 2)} on a calm day` },
      { k: "Disruption risk in that scenario", v: pct(D.scenarios.p[worstI], 0), d: "model probability of a disrupted day" },
    ]);
    const binChart = (id, key, xlab) => {
      const b = D.bins[key];
      plot(id, [{
        type: "bar", x: b.band, y: b.delay, marker: { color: css("--s1"), cornerradius: 4 },
        customdata: b.days.map((d, i) => [d, b.disrupted[i] * 100]),
        hovertemplate: `${xlab} %{x}<br>%{y:.2f} min per arrival<br>%{customdata[0]} days · %{customdata[1]:.0f}% disrupted<extra></extra>`,
      }], { xaxis: { title: { text: xlab }, showgrid: false }, yaxis: { title: { text: "min delay / arrival" }, rangemode: "tozero" }, showlegend: false, bargap: 0.35 });
    };
    binChart("bins-gust", "gust_band", "Max gust (km/h)");
    binChart("bins-rain", "rain_band", "Rainfall (mm)");
    renderCurve();

    const sc = D.scenarios;
    plot("scenarios", [{
      type: "bar", orientation: "h", y: sc.name, x: sc.delay, marker: { color: sc.name.map((_, i) => (i === 0 ? css("--axis") : css("--s2"))), cornerradius: 4 },
      customdata: sc.p.map((p) => p * 100), hovertemplate: "%{y}<br>%{x:.2f} min per arrival<br>P(disrupted day): %{customdata:.0f}%<extra></extra>",
    }], { yaxis: { autorange: "reversed", showgrid: false }, xaxis: { title: { text: "predicted min delay / arrival" }, rangemode: "tozero" }, showlegend: false, margin: { l: 10, r: 16, t: 6, b: 40 } });

    const w = D.weekly;
    plot("weekly", [
      { type: "scatter", mode: "lines", name: "All ATFM delay", x: w.w, y: w.total, line: { color: css("--s1"), width: 2 }, hovertemplate: "Week of %{x|%d %b %Y}<br>All: %{y:.2f} min/arrival<extra></extra>" },
      { type: "scatter", mode: "lines", name: "Weather-coded", x: w.w, y: w.weather, line: { color: css("--s2"), width: 2 }, fill: "tozeroy", fillcolor: css("--s2") + "33", hovertemplate: "Weather: %{y:.2f} min/arrival<extra></extra>" },
    ], { yaxis: { title: { text: "min delay / arrival" }, rangemode: "tozero" }, xaxis: { showgrid: false, type: "date" }, hovermode: "x unified" });

    const rank = META.airports.map((a) => ({ a, s: DELAYS[a.icao] ? DELAYS[a.icao].weather_share : null })).filter((r) => r.s != null).sort((x, y) => x.s - y.s);
    plot("share-rank", [{
      type: "bar", orientation: "h", y: rank.map((r) => `${r.a.iata} ${r.a.city}`), x: rank.map((r) => r.s * 100),
      marker: { color: rank.map((r) => (r.a.icao === icao ? css("--s2") : css("--s1"))), cornerradius: 4 },
      customdata: rank.map((r) => DELAYS[r.a.icao].avg_delay),
      hovertemplate: "%{y}<br>Weather share: %{x:.0f}%<br>Avg ATFM delay: %{customdata:.2f} min/arrival<extra></extra>",
    }], { xaxis: { title: { text: "% of ATFM arrival delay coded weather / de-icing" }, range: [0, 100] }, yaxis: { showgrid: false, tickfont: { size: 11 } }, showlegend: false, margin: { l: 10, r: 16, t: 6, b: 40 }, bargap: 0.25 });

    const imp = META.delay_importance.slice().reverse();
    plot("importance", [{
      type: "bar", orientation: "h", y: imp.map((d) => d.label), x: imp.map((d) => Math.max(d.importance, 0)), marker: { color: css("--s1"), cornerradius: 4 },
      hovertemplate: "%{y}: %{x:.3f} min MAE increase when shuffled<extra></extra>",
    }], { xaxis: { title: { text: "increase in error when the feature is shuffled (min)" } }, yaxis: { showgrid: false }, showlegend: false, margin: { l: 10, r: 16, t: 6, b: 40 }, bargap: 0.3 });
  }

  function renderDelayModel() {
    const m = META.delay_metrics;
    $("delay-model").innerHTML = `
      <p>Gradient-boosted trees are trained on ${m.train_rows.toLocaleString("en-GB")} airport-days (${m.train}) and tested out-of-time on ${m.test_rows.toLocaleString("en-GB")} airport-days (${m.test}). Inputs are daily gusts, wind, rain, snow, temperature, thunderstorms, traffic relative to normal, weekday, month and the vacation flag. The airport is a categorical feature, so each hub has its own weather sensitivity.</p>
      <table>
        <tr><th>Regression: ATFM delay per arrival</th><th></th></tr>
        <tr><td>MAE</td><td>${m.delay_mae} min (airport × month baseline ${m.delay_mae_baseline})</td></tr>
        <tr><td>R²</td><td>${m.delay_r2}</td></tr>
        <tr><th>Classification: disrupted day</th><th>(${m.disrupted_definition})</th></tr>
        <tr><td>Precision</td><td>${m.disrupted_precision}</td></tr>
        <tr><td>Recall</td><td>${m.disrupted_recall}</td></tr>
        <tr><td>ROC-AUC</td><td>${m.disrupted_roc_auc}</td></tr>
        <tr><td>Base rate in test year</td><td>${pct(m.disrupted_rate_test, 1)}</td></tr>
      </table>
      <p>ATFM delay covers only flow-management regulations, not every source of lateness (crew, turnaround, reactionary). It is still a clean signal for how capacity responds to weather.</p>`;
  }

  /* ------------------------------------------------------------------ main */
  async function render() {
    const t = await traffic(state.origin);
    const entry = t[state.dest] || t.ALL;
    renderTraffic(entry);
    renderForecast(entry);
    renderPunctuality();
    renderWeather();
  }

  async function init() {
    [META, PUNCT, DELAYS] = await Promise.all([getJSON("data/meta.json"), getJSON("data/punctuality.json"), getJSON("data/delays.json")]);
    fillOrigins(); fillDests(); bind();
    renderTrafficModel(); renderDelayModel();
    $("generated").textContent = `Data refreshed ${META.generated}.`;
    await render();
  }
  init().catch((err) => { console.error(err); document.querySelector("main").insertAdjacentHTML("afterbegin", `<p class="empty">Could not load data: ${err.message}</p>`); });
})();
