Sample automation sequences (JSON array of steps — same shape as the wizard editor).

MuMu Player ARM ships examples under Program Files, e.g.
``...\MuMuPlayerARM\manager\default_sequence test.json`` — same ``type`` names as CPharm
(``open_url``, ``tap``, ``wait``, ``swipe``, ``close_app``, ``rotate_identity``). MuMu may store
``wait.seconds`` as a string; loading through the wizard or ``sequence_normalize.py`` coerces numbers.
Optional tap fields ``retries``, ``jitter``, ``pre_wait_ms`` are CPharm-only hints (defaults apply if omitted).
MuMu's manager reads its own JSON path — use CPharm for groups, scheduler, and Tor; import MuMu files via Automation → Load sequence.

Configured target for samples: https://getdabflow.app (DabFlow marketing / PWA shell — timer UI, themes, bottom navigation). Edit ``open_url`` if you use a different site.

Intended flow on getdabflow.app
  1. Open the URL in Chrome (``open_url``).
  2. Wait for fonts, Chart.js, and app scripts (``wait``).
  3. Vertical ``swipe`` (finger drag upward) to scroll the main column so the timer / controls and fixed bottom bar are in view; the live page also loads third-party assets (e.g. ad slots), so a scroll keeps taps off the initial hero fold when needed.
  4. ``tap`` — samples use placeholder coordinates tuned for a common portrait canvas (~1080×1920 logical px): mid-screen (~y 1180) for timer / primary controls, lower (~y 1880) near the fixed ~70px bottom nav area. These are guesses from the page layout (scrollable ``main``, bottom padding for nav); they are NOT calibrated per device.
  5. ``rotate_identity`` — same as other CPharm samples.

sequence_standard.json — Baseline: scroll once, then one tap toward the bottom navigation center (primary engagement). Waits sized for normal load times.
sequence_slow_grouping.json — Same steps with longer waits and heavier pre-tap delay for slow devices, MuMu ARM, or staggered group starts.
sequence_alt_taps.json — Two taps: first mid-screen (timer / mode area), second lower (bottom bar / fallback if the first target does not match your viewport). Higher retries/jitter on the first tap.

Tap coordinates and calibration
  Resolution, DPI, Chrome UI (URL bar), notches, and on-page ads change where controls land. Treat x/y as placeholders until you record taps on your hardware (Automation editor or MuMu capture). Rough rule of thumb for bottom-nav taps: y ≈ (screen height − 35 to 55 px). Re-record if taps hit chrome, ads, or wrong tabs.

If the visible UI differs (A/B test, consent, or full-screen overlay), record a new sequence: note the steps you actually take (extra ``wait``, ``swipe``, or ``tap`` for dismiss), export JSON, and normalize with ``sequence_normalize.py`` if needed.

Load via Automation → Load JSON, or paste into Launch → True-up when the daily scheduler is running.
Optional CLI: ``python automation/sequence_normalize.py path\to\sequence.json out.json``

Human-like per-device variance (same steps JSON; different timing/taps per phone): set env ``CPHARM_HUMAN_VARIATION=1`` (or ``yes``/``true``) before starting the dashboard, or set ``HUMAN_VARIATION_FORCE = True`` in ``automation/config.py``. Uses a stable RNG per device + run cycle (scheduler: calendar day + fire index; groups: iteration + serial). Tunables: ``automation/human_variation.py`` (wait spread, tap jitter caps, step-gap range). Default is off so existing runs are unchanged.
