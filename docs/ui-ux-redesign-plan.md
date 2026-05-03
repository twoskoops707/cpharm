# CPharm UI/UX redesign — plan

End-to-end redesign spanning the **Tk setup wizard** (`wizard/setup_wizard.py` + `wizard/wizard_theme.py`) and the **web automation dashboard** (`automation/dashboard.html`). This document defines principles, information architecture, components, phased rollout, and accessibility/responsive notes.

## Principles

1. **Clarity first** — One primary action per surface; dense controls grouped into scannable sections with consistent hierarchy (title → short helper → controls → status).
2. **MuMu-first** — Copy and flows assume Snapdragon / ARM64 Windows users hit MuMu (nemux) paths early; Google AVD/SDK flows remain secondary but equally discoverable.
3. **Automation parity** — Wizard-produced config (groups, sequences, scheduler) must feel like the same product as the dashboard: shared vocabulary (phones, groups, teach, Tor/identity, URLs).
4. **No silent breakage** — Dashboard keeps stable element IDs and `/api/*` contracts; wizard preserves state keys and merge-install behavior.

## Information architecture

| Surface | Role | Primary tasks |
|--------|------|----------------|
| **Setup Wizard** | Guided install, device creation, automation defaults, Play checklist, launch | Linear steps with footer nav; heavy logging on technical steps |
| **Dashboard** | Day-to-day farm ops | Device grid, APK install, URL blast, groups runner, teach mode, Tor status, activity log |

Navigation model:

- **Wizard**: sequential steps (`STEP_LABELS`); users rarely skip except via dropdown on container pages.
- **Dashboard**: **hub layout** — sticky sidebar (or compact top nav below 900px width) jumps to anchored sections: Devices, Install, Browser, Groups, Teach, Identity, Log — aligned with automation parity.

## Component inventory

### Wizard (Tk)

- **Chrome**: window, optional step selector, footer Back/Next/Cancel.
- **Pages**: `WelcomePage`, `PrerequisitesPage`, runtime/device/connect/automation/groups/play/launch flows — each uses `PageBase.header`, `btn`, cards (`BG2`/`BG3`), readonly logs (`_attach_readonly_log_text`), themed scrollbars (`CPharm_TSCROLL`).
- **Tokens**: `wizard_theme` — `BG*`, `ACCENT*`, `FONT_*`, `SP`, `RADIUS`, buttons helpers `style_primary_button`, etc.

### Dashboard (HTML/CSS/JS inline)

- **Chrome**: header (logo, IP chip, WS dot), optional FAB.
- **Sections**: stats strip, controls, device grid + empty state, APK drop zone, URL + stagger + identity toggles, groups panel, teach card, Tor card, log feed.
- **Integrations**: `fetch('/api/...')`, WebSocket on `cfg.wsPort`; IDs consumed by JS (`btn-start-all`, `grid`, `groups-list`, …).

## Migration phases

| Phase | Scope | Status (branch `feature/ui-ux-redesign`) |
|-------|--------|-------------------------------------------|
| **M1 — Tokens + shell** | Unified teal/slate palette in `wizard_theme.py`; dashboard `:root` + sidebar shell + anchor nav; typography/spacing constants | **Started** — theme + dashboard shell shipped |
| **M2 — Wizard** | Apply tokens to **all** wizard pages (not only Welcome): cards, prerequisites progress, MuMu/device steps | Welcome step POC done; remainder tracked below |
| **M3 — Dashboard** | Deeper component polish (cards, teach timeline, groups cards), optional scheduler UI if exposed | Structure + palette done; card/teach/groups visuals next |
| **M4 — Polish** | Focus order, live regions for WS/log toasts, reduced-motion, copy pass for MuMu-first | Not started |

### Wizard steps — follow-up after Welcome (M2)

- **Install tools** (`PrerequisitesPage`): progress rows and log panel — align pad/spacing to `SP`, accent bars.
- **Runtime / devices / connect**: radio and list density; clearer MuMu vs SDK branching.
- **Automation & Groups**: step editor modals — consistent `FONT_*` and borders.
- **Launch**: dashboard URL callout — `code_row` / accent consistency.

## Accessibility

- **Dashboard**: Maintain semantic landmarks (`aside` nav, `header`); anchor links use visible section IDs; ensure focus-visible styles on shell nav links (future: `:focus-visible` outline using `--g`).
- **Wizard**: Tk limitations — prefer sufficient contrast (teal on dark passes for large text); ensure tab order follows visual order on each page; readonly logs remain selectable (existing behavior).
- **Motion**: Prefer CSS transitions over mandatory motion; respect `prefers-reduced-motion` in M4 for pulse/dot animations.

## Responsive notes

- **Dashboard**: Sidebar becomes a horizontal wrap under **900px**; stats grid already collapses at **380px**.
- **Wizard**: Desktop-first; minimum window size recommendations can be documented on Launch step if needed.

## References

- Discovery: `wizard/setup_wizard.py`, `wizard/wizard_theme.py`, `automation/dashboard.html`, `gui/cpharm_gui.py` (deprecated v1 LDPlayer GUI — not in scope for redesign).
