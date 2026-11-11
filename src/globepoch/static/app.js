/* Globepoch — guess the year the map was made. */
(function () {
  "use strict";

  const P = JSON.parse(document.getElementById("puzzle-data").textContent);
  const STORE_KEY = "globepoch:v1";
  // One point off per full `yearsPerPoint` years: within 9 years scores 100, 1000+ years scores 0.
  const YEARS_PER_POINT = P.yearsPerPoint;
  const CLOSE_WINDOW = 100; // hint threshold, and the shaded "90+ points" zone on the timeline
  const MAX_GUESSES = P.maxGuesses;
  const $ = (id) => document.getElementById(id);

  // ---------------------------------------------------------------- answer
  // Mirrors site.py: XOR with a tiny LCG keystream. Not security — just no spoilers in view-source.
  function decodeAnswer(blob, number) {
    const bytes = Uint8Array.from(atob(blob), (c) => c.charCodeAt(0));
    let x = (number * 7919 + 17) & 0x7fffffff;
    for (let i = 0; i < bytes.length; i++) {
      x = (Math.imul(x, 1103515245) + 12345) & 0x7fffffff;
      bytes[i] ^= (x >>> 16) & 0xff;
    }
    return JSON.parse(new TextDecoder().decode(bytes));
  }

  // ---------------------------------------------------------------- scoring
  function scoreFor(guess, year) {
    return Math.max(0, P.maxPoints - Math.floor(Math.abs(guess - year) / YEARS_PER_POINT));
  }

  const BAR_COLORS = ["🟥", "🟥", "🟧", "🟧", "🟨", "🟨", "🟩", "🟩", "🟩", "🟩"];
  function emojiBar(score) {
    // Scores bunch up near the top, so stretch them: 99→9 cells, 95→9, 90→7, 75→4, 50→1.
    let filled = Math.round(10 * Math.pow(score / 100, 3));
    if (score > 0) filled = Math.max(1, filled);
    if (score < 100) filled = Math.min(9, filled);
    return BAR_COLORS.map((c, i) => (i < filled ? c : "⬜")).join("");
  }

  function verdict(score, diff) {
    if (diff === 0) return "Bullseye! 🎯";
    if (score === 100) return "Within a decade! 🧭";
    if (score >= 97) return "Master cartographer! 🏆";
    if (score >= 94) return "Sharp eye. 🔍";
    if (score >= 90) return "Right era. 🗺️";
    if (score >= 75) return "In the ballpark. 🧐";
    if (score >= 50) return "Ancient history… 🏺";
    if (score > 0) return "Way off course. 🧭";
    return "Lost at sea. 🌊";
  }
  function tierOf(score) {
    return score >= 95 ? "high" : score >= 85 ? "mid" : score > 0 ? "low" : "zero";
  }

  function diffText(guess, year) {
    const d = guess - year;
    if (d === 0) return "You nailed the exact year.";
    const n = Math.abs(d);
    return `${n} year${n === 1 ? "" : "s"} too ${d < 0 ? "early" : "late"}.`;
  }

  // After a miss the player learns the direction, and whether they're within 100 years.
  function hintFor(guess, year) {
    const d = guess - year;
    return {
      hit: scoreFor(guess, year) === P.maxPoints,
      early: d < 0,
      near: Math.abs(d) <= CLOSE_WINDOW,
    };
  }
  // Share-text token per guess: 🎯 hit, else ⏪/⏩ (too early/late) + 🟠 within 100 yrs / 🔵 further.
  function guessToken(guess, year) {
    const h = hintFor(guess, year);
    if (h.hit) return "🎯";
    return (h.early ? "⏪" : "⏩") + (h.near ? "🟠" : "🔵");
  }
  function hintText(guess, year) {
    const h = hintFor(guess, year);
    if (h.hit) return "Within a decade!";
    return `${h.early ? "Too early, go later" : "Too late, go earlier"} · ${
      h.near ? "within 100 years" : "more than 100 years off"
    }`;
  }
  const closest = (guesses, year) =>
    guesses.reduce((b, g) => (Math.abs(g - year) < Math.abs(b - year) ? g : b));

  // ---------------------------------------------------------------- storage
  function loadState() {
    try {
      const s = JSON.parse(localStorage.getItem(STORE_KEY));
      if (s && typeof s === "object" && s.results) {
        s.progress = s.progress || {};
        return s;
      }
    } catch (e) {
      /* private mode, corrupted data, … */
    }
    return { results: {}, progress: {}, seenHelp: false };
  }
  function saveState(s) {
    try {
      localStorage.setItem(STORE_KEY, JSON.stringify(s));
    } catch (e) {
      /* storage unavailable: the game still works for this visit */
    }
  }

  function computeStats(s, today) {
    const nums = Object.keys(s.results).map(Number).sort((a, b) => a - b);
    const scores = nums.map((n) => s.results[n].score);
    const total = scores.reduce((a, b) => a + b, 0);
    // Streaks count puzzles that scored on their own day; archive ("late") plays don't.
    const scored = (n) => s.results[n] && s.results[n].score > 0 && !s.results[n].late;
    // Current streak: consecutive puzzles with points, ending today (or yesterday, if today is unplayed).
    let n = s.results[today] && !s.results[today].late ? today : today - 1;
    let current = 0;
    while (scored(n)) {
      current++;
      n--;
    }
    let best = 0;
    let run = 0;
    let prev = null;
    for (const k of nums) {
      run = scored(k) ? (prev === k - 1 && run > 0 ? run + 1 : 1) : 0;
      best = Math.max(best, run);
      prev = k;
    }
    const buckets = BUCKETS.map((label) => [label, scores.filter((x) => bucketLabel(x) === label).length]);
    return {
      played: nums.length,
      average: nums.length ? Math.round(total / nums.length) : 0,
      bestScore: scores.length ? Math.max(...scores) : 0,
      current,
      maxStreak: Math.max(best, current),
      buckets,
    };
  }

  // ---------------------------------------------------------------- viewer
  let viewer = null;
  function initViewer() {
    if (typeof OpenSeadragon === "undefined") {
      $("viewer").innerHTML =
        '<p class="viewer-error">The map viewer failed to load. Check your connection and reload.</p>';
      return;
    }
    viewer = OpenSeadragon({
      element: $("viewer"),
      tileSources: P.viewer.source,
      showNavigationControl: false,
      showNavigator: true,
      navigatorPosition: "BOTTOM_LEFT",
      navigatorAutoFade: false,
      navigatorSizeRatio: 0.16,
      maxZoomPixelRatio: 2.5,
      visibilityRatio: 0.6,
      minZoomImageRatio: 0.8,
      constrainDuringPan: true,
      animationTime: 0.9,
      springStiffness: 8,
      gestureSettingsMouse: { clickToZoom: false, dblClickToZoom: true },
      gestureSettingsTouch: { pinchRotate: false },
    });
    $("zoom-in").onclick = () => viewer.viewport.zoomBy(1.6).applyConstraints();
    $("zoom-out").onclick = () => viewer.viewport.zoomBy(1 / 1.6).applyConstraints();
    $("zoom-home").onclick = () => viewer.viewport.goHome();
    $("zoom-full").onclick = () => viewer.setFullPage(!viewer.isFullPage());
    const hint = $("viewer-hint");
    viewer.addOnceHandler("canvas-drag", () => hint.classList.add("gone"));
    viewer.addOnceHandler("canvas-scroll", () => hint.classList.add("gone"));
    setTimeout(() => hint.classList.add("gone"), 6000);
  }

  // ---------------------------------------------------------------- guess UI
  const input = $("year-input");
  const slider = $("year-slider");
  const clampYear = (y) => Math.min(P.maxYear, Math.max(P.minYear, Math.round(y)));

  function setGuess(y, from) {
    if (!Number.isFinite(y)) return;
    y = clampYear(y);
    if (from !== "input") input.value = y;
    slider.value = y;
    $("submit-year").textContent = y;
    const used = (loadState().progress[P.number] || []).length;
    $("guess-count").textContent = `Guess ${used + 1} of ${MAX_GUESSES}`;
    const pct = ((y - P.minYear) / (P.maxYear - P.minYear)) * 100;
    slider.style.setProperty("--pct", pct + "%");
  }

  function initGuessUI(state) {
    input.min = slider.min = P.minYear;
    input.max = slider.max = P.maxYear;
    const scale = $("slider-scale");
    const step = 100;
    for (let y = Math.ceil(P.minYear / step) * step; y <= P.maxYear; y += step) {
      const tick = document.createElement("span");
      tick.textContent = y;
      tick.style.left = ((y - P.minYear) / (P.maxYear - P.minYear)) * 100 + "%";
      scale.appendChild(tick);
    }
    const start = state.draft && state.draft.number === P.number ? state.draft.year : 1750;
    setGuess(start);
    slider.addEventListener("input", () => setGuess(Number(slider.value), "slider"));
    input.addEventListener("input", () => {
      const v = Number(input.value);
      if (input.value.length >= 4 && v >= P.minYear && v <= P.maxYear) setGuess(v, "input");
    });
    input.addEventListener("blur", () => setGuess(Number(input.value) || Number(slider.value)));
    document.querySelectorAll(".nudge").forEach((b) =>
      b.addEventListener("click", () => setGuess(Number(slider.value) + Number(b.dataset.step)))
    );
    $("guess-form").addEventListener("submit", (e) => {
      e.preventDefault();
      setGuess(Number(input.value) || Number(slider.value));
      submitGuess(Number(slider.value));
    });
  }

  // ---------------------------------------------------------------- result
  let answer = null;
  function getAnswer() {
    if (!answer) answer = decodeAnswer(P.answer, P.number);
    return answer;
  }

  function submitGuess(guess) {
    const state = loadState();
    if (state.results[P.number]) return showResult(state);
    const a = getAnswer();
    const previous = state.progress[P.number] || [];
    if (previous.includes(guess)) {
      toast(`You already guessed ${guess}. Try another year.`);
      return;
    }
    const guesses = previous.concat(guess);
    delete state.draft;
    if (hintFor(guess, a.year).hit || guesses.length >= MAX_GUESSES) {
      const best = closest(guesses, a.year); // your closest guess counts
      state.results[P.number] = {
        guess: best,
        guesses,
        score: scoreFor(best, a.year),
        year: a.year,
        date: P.date,
      };
      if (P.number < P.latest) state.results[P.number].late = true;
      delete state.progress[P.number];
      saveState(state);
      renderHints([]);
      showResult(state, true);
    } else {
      state.progress[P.number] = guesses;
      saveState(state);
      renderHints(guesses, true);
      setGuess(Number(slider.value));
    }
  }

  function renderHints(guesses, fresh) {
    const a = guesses.length ? getAnswer() : null;
    const list = $("hints");
    list.innerHTML = "";
    list.hidden = guesses.length === 0;
    guesses.forEach((g, i) => {
      const h = hintFor(g, a.year);
      const li = document.createElement("li");
      li.className = "hint" + (fresh && i === guesses.length - 1 ? " fresh" : "");
      li.dataset.near = h.near;
      li.innerHTML = `<b>${g}</b><span>${hintText(g, a.year)}</span>`;
      list.appendChild(li);
    });
    const marks = $("slider-marks");
    marks.innerHTML = "";
    guesses.forEach((g) => {
      const m = document.createElement("span");
      m.className = "mark";
      m.dataset.near = hintFor(g, a.year).near;
      m.style.left = ((g - P.minYear) / (P.maxYear - P.minYear)) * 100 + "%";
      m.title = g;
      marks.appendChild(m);
    });
  }

  function shareText(state) {
    const r = state.results[P.number];
    const guesses = r.guesses || [r.guess];
    const stats = computeStats(state, P.latest);
    const d = r.guess - r.year;
    const off =
      d === 0
        ? "🎯 Exact year!"
        : `${d < 0 ? "⏪" : "⏩"} ${Math.abs(d)} yr${Math.abs(d) === 1 ? "" : "s"} too ${d < 0 ? "early" : "late"}`;
    const late = P.number < P.latest;
    const streak = !late && stats.current >= 2 ? ` · 🔥 ${stats.current}-day streak` : "";
    return [
      `🗺️ ${P.name} #${P.number}${late ? " (archive)" : ""} — ${r.score}/100 (${guesses.length}/${MAX_GUESSES})`,
      guesses.map((g) => guessToken(g, r.year)).join(" "),
      emojiBar(r.score),
      off + streak,
      P.shareUrl,
    ].join("\n");
  }

  function placePin(el, year, label) {
    const pct = ((year - P.minYear) / (P.maxYear - P.minYear)) * 100;
    el.style.left = Math.min(100, Math.max(0, pct)) + "%";
    el.querySelector("span").textContent = label;
  }

  function showResult(state, animate) {
    const r = state.results[P.number];
    const a = getAnswer();
    $("guess-form").hidden = true;
    $("result").hidden = false;
    document.body.classList.add("solved");

    $("r-verdict").textContent = verdict(r.score, r.guess - a.year);
    const guesses = r.guesses || [r.guess];
    const lead = guesses.length > 1 ? `Your closest guess was ${r.guess}` : `You guessed ${r.guess}`;
    $("r-diff").textContent = `${lead}; it's from ${a.year}. ${diffText(r.guess, a.year)}`;
    $("r-trail").textContent =
      guesses.length > 1 ? "Guesses: " + guesses.map((g) => `${g} ${guessToken(g, a.year)}`).join("  →  ") : "";
    $("r-title").textContent = a.title;
    $("r-meta").textContent = [a.creator, a.place, a.year].filter(Boolean).join(" · ");
    $("r-blurb").textContent = a.blurb;
    $("r-source").href = a.source;
    $("r-credit").textContent = a.credit;

    const lo = ((a.year - CLOSE_WINDOW - P.minYear) / (P.maxYear - P.minYear)) * 100;
    const hi = ((a.year + CLOSE_WINDOW - P.minYear) / (P.maxYear - P.minYear)) * 100;
    const win = $("tl-window");
    win.style.left = Math.max(0, lo) + "%";
    win.style.width = Math.min(100, hi) - Math.max(0, lo) + "%";
    placePin($("tl-answer"), a.year, a.year);
    placePin($("tl-guess"), r.guess, r.guess);
    const timeline = $("timeline");
    timeline.querySelectorAll(".tl-tick").forEach((t) => t.remove());
    guesses
      .filter((g) => g !== r.guess)
      .forEach((g) => {
        const t = document.createElement("div");
        t.className = "tl-tick";
        t.style.left = Math.min(100, Math.max(0, ((g - P.minYear) / (P.maxYear - P.minYear)) * 100)) + "%";
        timeline.appendChild(t);
      });
    $("timeline").classList.toggle("close", Math.abs(r.guess - a.year) < 40);

    $("share-preview").textContent = shareText(state);

    const scoreEl = $("r-score");
    if (animate && !matchMedia("(prefers-reduced-motion: reduce)").matches) {
      const t0 = performance.now();
      const tick = (t) => {
        const k = Math.min(1, (t - t0) / 900);
        scoreEl.textContent = Math.round(r.score * (1 - Math.pow(1 - k, 3)));
        if (k < 1) requestAnimationFrame(tick);
      };
      requestAnimationFrame(tick);
      setTimeout(() => openStats(), 2600);
    } else {
      scoreEl.textContent = r.score;
    }
    scoreEl.parentElement.dataset.tier = tierOf(r.score);
    startCountdown();
    updateArchiveUI();
  }

  // ---------------------------------------------------------------- sharing
  function toast(msg) {
    const t = $("toast");
    t.textContent = msg;
    t.hidden = false;
    clearTimeout(toast.timer);
    toast.timer = setTimeout(() => (t.hidden = true), 2200);
  }

  async function share() {
    const text = shareText(loadState());
    try {
      await navigator.clipboard.writeText(text);
      toast("Copied to the clipboard. Paste it to your friends!");
      return;
    } catch (e) {
      /* fall through */
    }
    if (navigator.share) {
      try {
        await navigator.share({ text });
        return;
      } catch (e) {
        if (e && e.name === "AbortError") return;
      }
    }
    const ta = document.createElement("textarea");
    ta.value = text;
    document.body.appendChild(ta);
    ta.select();
    const ok = document.execCommand && document.execCommand("copy");
    ta.remove();
    toast(ok ? "Copied to clipboard!" : "Couldn't copy. Select the text above and copy it yourself.");
  }

  // ---------------------------------------------------------------- countdown
  let countdownTimer = null;
  function startCountdown() {
    if (countdownTimer) return;
    const el = $("countdown");
    if (!el) return;
    const update = () => {
      const now = new Date();
      const next = Date.UTC(now.getUTCFullYear(), now.getUTCMonth(), now.getUTCDate() + 1);
      const s = Math.max(0, Math.floor((next - now.getTime()) / 1000));
      const pad = (v) => String(v).padStart(2, "0");
      el.textContent = `${pad(Math.floor(s / 3600))}:${pad(Math.floor(s / 60) % 60)}:${pad(s % 60)}`;
    };
    update();
    countdownTimer = setInterval(update, 1000);
  }

  // ---------------------------------------------------------------- modals
  function openStats() {
    const st = computeStats(loadState(), P.latest);
    const items = [
      [st.played, "Played"],
      [st.average, "Avg score"],
      [st.current, "Streak"],
      [st.maxStreak, "Best streak"],
    ];
    $("stat-grid").innerHTML = items
      .map(([v, l]) => `<div class="stat"><strong>${v}</strong><span>${l}</span></div>`)
      .join("");
    const max = Math.max(1, ...st.buckets.map((b) => b[1]));
    const today = loadState().results[P.number];
    $("dist").innerHTML = st.buckets
      .map(([label, count]) => {
        const mine = today && bucketLabel(today.score) === label ? " mine" : "";
        return `<div class="dist-row${mine}"><span class="dist-label">${label}</span><span class="dist-bar" style="--w:${(count / max) * 100}%"><b>${count}</b></span></div>`;
      })
      .join("");
    $("stats-modal").showModal();
  }
  const BUCKETS = ["🎯 100", "95–99", "90–94", "75–89", "50–74", "1–49", "0"];
  function bucketLabel(x) {
    if (x === 100) return BUCKETS[0];
    if (x >= 95) return BUCKETS[1];
    if (x >= 90) return BUCKETS[2];
    if (x >= 75) return BUCKETS[3];
    if (x >= 50) return BUCKETS[4];
    if (x >= 1) return BUCKETS[5];
    return BUCKETS[6];
  }

  // ---------------------------------------------------------------- archive
  const pageHref = (n) => (n === P.latest ? P.base || "./" : `${P.base}${n}/`);
  const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
  const WEEKDAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
  function prettyDate(iso) {
    const d = new Date(iso + "T12:00:00Z");
    return `${WEEKDAYS[d.getUTCDay()]} ${d.getUTCDate()} ${MONTHS[d.getUTCMonth()]} ${d.getUTCFullYear()}`;
  }
  function unplayed(state) {
    return P.days.filter((d) => d.n < P.latest && !state.results[d.n]).map((d) => d.n);
  }

  function openArchive() {
    const state = loadState();
    const played = P.days.filter((d) => state.results[d.n]).length;
    $("archive-summary").textContent =
      `${P.days.length} map${P.days.length === 1 ? "" : "s"} so far · you've played ${played}.`;
    const list = $("archive-list");
    list.innerHTML = "";
    P.days
      .slice()
      .reverse()
      .forEach((d) => {
        const r = state.results[d.n];
        const li = document.createElement("li");
        if (d.n === P.number) li.className = "current";
        const a = document.createElement("a");
        a.href = pageHref(d.n);
        a.innerHTML =
          `<span class="a-num">#${d.n}</span>` +
          `<span class="a-date">${d.n === P.latest ? "Today" : prettyDate(d.date)}</span>` +
          (r
            ? `<span class="a-score" data-tier="${tierOf(r.score)}">${r.score}</span>`
            : state.progress[d.n]
              ? `<span class="a-play">${state.progress[d.n].length}/${MAX_GUESSES} →</span>`
              : `<span class="a-play">Play →</span>`);
        li.appendChild(a);
        list.appendChild(li);
      });
    $("archive-modal").showModal();
    const cur = list.querySelector(".current");
    if (cur) cur.scrollIntoView({ block: "nearest" });
  }

  function updateArchiveUI() {
    const state = loadState();
    const todo = unplayed(state);
    const badge = $("archive-badge");
    badge.hidden = todo.length === 0;
    badge.textContent = todo.length > 99 ? "99+" : todo.length;
    const btn = $("catchup-btn");
    const next = todo.filter((n) => n !== P.number).pop(); // newest unplayed other day
    if (state.results[P.number] && next) {
      btn.hidden = false;
      btn.textContent =
        todo.length === 1 ? "Play the one earlier map you missed →" : `Catch up: ${todo.length} earlier maps to play →`;
      btn.onclick = () => (location.href = pageHref(next));
    } else {
      btn.hidden = true;
    }
  }

  function wireModals() {
    $("help-btn").onclick = () => $("help-modal").showModal();
    $("archive-btn").onclick = openArchive;
    $("stats-btn").onclick = openStats;
    document.querySelectorAll("dialog").forEach((d) =>
      d.addEventListener("click", (e) => {
        if (e.target === d) d.close(); // click on backdrop
      })
    );
  }

  // ---------------------------------------------------------------- boot
  function boot() {
    const state = loadState();
    initViewer();
    initGuessUI(state);
    wireModals();
    $("share-btn").addEventListener("click", share);
    updateArchiveUI();
    // Remember an unsent guess so a reload doesn't lose it.
    window.addEventListener("beforeunload", () => {
      const s = loadState();
      if (!s.results[P.number]) {
        s.draft = { number: P.number, year: Number(slider.value) };
        saveState(s);
      }
    });
    if (state.progress[P.number]) renderHints(state.progress[P.number]);
    if (state.results[P.number]) {
      showResult(state, false);
    } else if (!state.seenHelp) {
      state.seenHelp = true;
      saveState(state);
      $("help-modal").showModal();
    }
  }

  // Exposed for tests/debugging in the console.
  window.Globepoch = { scoreFor, emojiBar, computeStats, decodeAnswer, shareText, guessToken, hintText };
  boot();
})();
