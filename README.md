# Tyler — Portfolio Website

A stunning, motion-driven personal portfolio for Tyler — designer & developer.

Built with zero build step: static HTML, CSS, and vanilla JS. Open `index.html` in a browser or serve the folder.

## Design system

The design system was generated using the [UI/UX Pro Max](https://github.com/nextlevelbuilder/ui-ux-pro-max-skill) skill (installed locally at `.claude/skills/ui-ux-pro-max/`).

- **Pattern:** Portfolio Grid — Hero, Project Grid, About, Process, Contact
- **Style:** Motion-Driven — scroll animations, hover states, parallax, smooth transitions
- **Colors:** Monochrome + blue accent (`#18181B` / `#2563EB` / `#FAFAFA`)
- **Typography:** Archivo (display) / Space Grotesk (body)
- **Modes:** Light and dark, respecting system preference
- **Accessibility:** WCAG AA contrast, visible focus, `prefers-reduced-motion`

## Features

- Sticky blurred navigation with scroll progress
- Animated hero with conic-gradient orb and grid backdrop
- Infinite brand marquee (pauses on hover)
- Filterable project grid with tilt + hover lift
- Animated stat counters via `IntersectionObserver`
- Process timeline, testimonial, and validated contact form
- Light/dark theme toggle (persisted in `localStorage`)
- Cursor-following radial glow on desktop
- Fully responsive from 375 → 1440px

## Run locally

```bash
# any static server works
python3 -m http.server 8080
# then open http://localhost:8080
```

## Regenerate the design system

```bash
python3 .claude/skills/ui-ux-pro-max/scripts/search.py \
  "personal portfolio developer creative modern" \
  --design-system -p "Tyler"
```

## Structure

```
.
├── index.html
├── assets/
│   ├── styles.css
│   └── main.js
├── .claude/skills/ui-ux-pro-max/   # installed skill
└── skill.json
```
