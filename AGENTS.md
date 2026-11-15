# Project instructions

## Apple-inspired web interface design and review

These instructions apply to planning, implementing, modifying, and reviewing the
web interface in this repository.

### Scope and sources

- This is a web project. Preserve its existing web stack and browser behavior.
  Do not introduce Xcode projects, Swift, SwiftUI, UIKit, AppKit, Apple platform
  deployment targets, or Apple-native build rules unless the user explicitly
  changes the project scope.
- For interface work that should follow Apple design principles, first read
  `.agents/skills/apple-hig/SKILL.md`, then load only the relevant parts of
  `FOUNDATIONS.md`, `PATTERNS.md`, `COMPONENTS.md`, and `CHECKLIST.md`.
- Treat Apple HIG as design guidance for this web interface, not as a substitute
  for web standards. Do not copy Apple-platform-only point sizes, safe-area
  values, native component APIs, or Xcode workflows into the web project.
- Existing product requirements, `DESIGN.md`, the current codebase, semantic
  HTML, browser conventions, responsive design, and web accessibility take
  precedence when an Apple-native rule does not map cleanly to the web.
- Distinguish Apple requirements and recommendations, third-party Skill
  summaries, web-platform requirements, and project-specific decisions. Verify
  version-sensitive details against current primary documentation when they
  materially affect the task.

### Design and implementation

- Start from the user task, information hierarchy, navigation, and content.
  Explain important component choices when they are not obvious.
- Prefer semantic HTML and native browser controls. Add ARIA only when native
  semantics cannot express the required behavior.
- Support responsive layouts, zoom and text resizing, keyboard navigation,
  visible focus, logical focus order, sufficient contrast, meaningful labels,
  and alternatives to color-only or motion-only communication.
- Respect browser and operating-system preferences such as color scheme,
  reduced motion, increased contrast, and reduced transparency when applicable.
- Keep light and dark appearances legible when both are supported. Do not add an
  application appearance switch unless it is part of the product requirements.
- Use animation purposefully, keep the interface usable while it runs, and
  provide a reduced-motion treatment.
- Cover the states relevant to the task, including loading, empty, error,
  disabled, success, and offline states where applicable.
- Apple-inspired visual treatments, including translucent materials, must keep
  content legible and must degrade gracefully in browsers that do not support
  the effect.

### Completion and reporting

- Use the repository's actual web build, lint, test, and preview commands once
  they exist. Do not claim native Apple or Xcode validation for this project.
- Report code checks, successful builds, browser or responsive inspection, and
  accessibility testing separately. State clearly what was not run.
- If a shared design decision changes, update `DESIGN.md` without replacing
  unrelated existing decisions.

