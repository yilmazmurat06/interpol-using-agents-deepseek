---
name: interpol-frontend
description: 'Use this skill when designing or improving the Interpol Red Notice web UI: notice card grid, filter panel, alarm markers, SSE live updates, and nationality flag display. Produces production-grade, visually distinctive HTML/CSS/JS inside a Flask/Jinja2 template.'
argument-hint: 'Describe the UI component or page to build or improve.'
---

# Interpol Frontend Designer

## When to Use
- Building or redesigning `container_b/templates/index.html`
- Adding or improving filter panel, notice cards, alarm badges, or SSE update behavior
- Fixing visual regressions or layout issues in the web UI 
- Improving UX for search, sort, or live-update interactions

---

## Pre-Implementation Checklist (complete before writing any markup)

- [ ] Never replace an existing `<img>` element during SSE updates — update fields in-place only (LL-006)
- [ ] Images have unknown aspect ratios — always use `object-fit: contain` with a dark background container, never `object-fit: cover` (LL-007)
- [ ] Always use `countryFlag(isoCode)` for nationality display — never hardcode a globe emoji or bare ISO code (LL-008)
- [ ] Filtering must be server-side: send query params to `GET /api/notices`, never filter the DOM client-side
- [ ] Filter dropdowns must be populated from `GET /api/filters` on page load — never hardcoded
- [ ] SSE connection must use `GET /api/stream` — on `notice_update` event call `updateCardInPlace()`, never `buildCard()` on existing cards
- [ ] Images are loaded via Interpol CDN URLs in `<img src>` — the browser fetches them, not the server

---

## Design Thinking — Do This Before Writing CSS

Commit to a bold, intentional aesthetic direction before touching a line of code. The Interpol use case has a clear emotional register: **urgency, authority, surveillance**. Use this as a creative constraint, not a limitation.

Ask these questions:
- **Tone**: Dark and forensic? Cold and institutional? Tense and cinematic? Pick one and execute it completely.
- **Unforgettable element**: What is the one thing a user will remember? (e.g., the way alarm cards pulse, the typographic weight of a name, the contrast between a mugshot and dark background)
- **Differentiation**: This is a serious tool. Avoid decorative flourishes that feel out of place. Gravitas is the design goal.

**Typography rules:**
- Choose fonts that feel authoritative and distinctive — not Inter, Roboto, Arial, or system fonts
- Pair a strong display font for names/headings with a legible mono or condensed body font for metadata
- Load from Google Fonts or use `@font-face`

**Color rules:**
- Commit to a dominant palette — dark backgrounds perform better for a surveillance/monitoring tool
- Use CSS variables for every color, spacing unit, and radius
- Alarm state must be visually unmistakable — use color, animation, or border to signal it
- Accent color should feel deliberate, not decorative

**Motion rules:**
- One well-orchestrated page load with staggered card reveals is better than scattered micro-interactions
- Alarm state transitions should animate (pulse, flash, or glow — not a simple color swap)
- SSE card updates should have a brief visual acknowledgment (flash, border highlight) so the user sees what changed
- CSS-only animations preferred; no animation library dependencies unless already in the project

**Layout rules:**
- Card grid should adapt from 1 to 4 columns based on viewport
- Filter panel should be collapsible on mobile
- Negative space is intentional — dense information does not mean cramped information
- Alarm cards should visually separate from normal cards (position, border, or section)

---

## Component Specifications

### Notice Card
Must display:
- Photo (`<img src="{{notice.image_url}}">`) — never fetch server-side
- Full name (forename + name) — typographically dominant
- Age calculated from `date_of_birth` (show "Age unknown" if null)
- Nationality flags using `countryFlag(isoCode)` for each nationality
- Sex icon or label
- Charges from `arrest_warrants` — list the `charge` field of each warrant
- Issuing countries from `arrest_warrants` — list the `issuing_country_id` of each warrant
- Alarm badge — visually prominent when `is_alarm = true`
- `received_at` timestamp — subtle, not dominant

Image container rules (LL-007):
```css
.card-thumb-wrap {
  height: 200px;
  background: var(--color-surface-dark);
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
}
.card-thumb-wrap img {
  max-width: 100%;
  max-height: 200px;
  object-fit: contain;
}
```

### Filter Panel
Must include:
- Text search by name
- Nationality multi-select (populated from `GET /api/filters` → `nationalities`)
- Gender selector (All / M / F)
- Issuing country multi-select (populated from `GET /api/filters` → `issuing_countries`)
- Charges keyword input
- Alarms-only toggle

On any filter change: call `GET /api/notices` with current filter state as query params and re-render the grid.

### SSE Live Updates
```javascript
const evtSource = new EventSource('/api/stream');
evtSource.addEventListener('notice_update', (e) => {
  const notice = JSON.parse(e.data);
  const existing = document.getElementById(`card-${notice.notice_id.replace('/', '-')}`);
  if (existing) {
    updateCardInPlace(existing, notice); // NEVER replace — LL-006
  } else {
    prependCard(buildCard(notice));
  }
});
```

`updateCardInPlace()` must only update:
- Name fields (text content)
- Nationality badges (innerHTML of the flag container)
- Charges text
- Alarm badge class and visibility
- `received_at` timestamp

`updateCardInPlace()` must NEVER touch:
- The `<img>` element or its `src` attribute (LL-006)
- The card's root element identity (`id`, position in DOM)

### Nationality Flags (LL-008)
```javascript
function countryFlag(code) {
  if (!code || code.length !== 2) return code;
  return Array.from(code.toUpperCase())
    .map(c => String.fromCodePoint(c.charCodeAt(0) + 0x1F1A5))
    .join('');
}
```

Call `decorateNationalities()` on page load to patch server-rendered cards. New cards from SSE use `countryFlag()` inside `buildCard()`.

---

## Technical Constraints

- Template engine: Jinja2 (Flask) — server renders initial card grid; JS handles SSE updates
- No frontend build step — vanilla JS or CDN-loaded libraries only
- No jQuery
- Fonts via Google Fonts CDN or `@font-face` — no npm
- All API calls use `fetch()` with relative URLs

---

## Done Criteria

- [ ] Cards display photo, name, age, nationality flags, sex, charges, issuing countries, alarm badge
- [ ] Alarm cards are visually distinct and animated
- [ ] Filter panel populates from `/api/filters` and triggers server-side queries on change
- [ ] SSE updates modify cards in-place without replacing `<img>`
- [ ] Images display correctly across portrait, square, and landscape aspect ratios
- [ ] Nationality shown as Unicode flag emoji, not bare ISO code
- [ ] UI is visually distinctive — not generic, not purple-gradient-on-white, not Inter font
- [ ] Layout is responsive across mobile, tablet, and desktop
- [ ] No hardcoded filter values in the HTML

---

## Common Mistakes to Avoid

| Mistake | Fix |
|---------|-----|
| Replacing entire card DOM on SSE update | Call `updateCardInPlace()` only |
| Using `object-fit: cover` on mugshot photos | Use `contain` + fixed-height wrapper |
| Hardcoding 🌍 emoji for nationalities | Use `countryFlag(isoCode)` |
| Filtering cards client-side with JS | Send filter params to `GET /api/notices` |
| Hardcoding nationality or country lists | Always load from `GET /api/filters` |
| Inter / Roboto / Arial fonts | Pick something distinctive and intentional |
| Purple gradient on white background | Commit to a strong, context-appropriate palette |
| Alarm = just a red border | Alarm = animated, unmissable, memorable |
