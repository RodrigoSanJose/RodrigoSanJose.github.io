# Development analytics exclusion

The site owner requires that agent visits, previews, browser automation, and
development checks never enter production Umami statistics. This preference
applies to future conversations working in this repository and to all subagents.

- Prefer local files or a loopback server. Mock the existing Umami API and
  intercept analytics requests before loading any page, including popups.
- Before any site scripts execute in every browser context, set the browser's
  opt-out flag with an initialization script:

  ```js
  try { localStorage.setItem('umami.disabled', '1'); } catch (error) {}
  ```

- Keep request interception as a second safeguard, even with the opt-out flag.
  When inspecting production, block the Umami loader and collection requests
  before the first navigation. Never load the live tracker to verify analytics.
- If browser tooling cannot apply these protections before navigation, inspect
  source or use a protected local browser instead of opening the live site.
- Do not add an unconditional opt-out to production site JavaScript: the flag
  belongs to the development/agent browser, so real visitors retain analytics.
- Do not remove or clear the opt-out in an agent browser. Tests may mock the API
  to record local call attempts, but must never forward them to Umami.

Run the protected local tests with:

```sh
python -B -m unittest discover -s tests -v
```

## Tracking maintenance

- Every website content update includes tracking by default; the user does not
  need to request it separately. This applies to all new/changed links, quotes
  and their sources/attributions, navigation, profiles, icons/images, downloads,
  papers, pages, and dynamically generated links. Reuse the shared delegated
  handler and preserve intentional exclusions and opt-out behavior.
- Give ordinary links descriptive visible text or accessible labels; avoid
  opaque names such as “Source,” “Download,” or “[1]” without context. For new
  numbered quotes, supply source/context labels and names using
  `Quote N (Work|Literature|Misc): Description`, matching the displayed number and
  category; retain the complete description in `label`. Cover attribution/source
  links too. Use existing shared metadata for ambiguous or long labels instead
  of adding listeners or direct tracker calls.
- For papers, reuse the shared publication wrapper: put the full title once in
  `data-publication-title` and
  keep every paper destination inside it. `assets/js/link-tracking.js` derives
  `Paper title. Destination type` automatically; do not add per-link event names,
  `data-umami-event`, or separate listeners. Mark publication sections with
  `data-publications` so verification catches missing title metadata.
- Keep link types clear through URLs, existing icons, or accessible labels; use
  `data-link-type` for unusual/ambiguous destinations. Keep names within 50
  characters, preserving the type and distinguishing papers; use a meaningful
  `data-publication-short` when needed. Retain the complete `label`, resolved
  destination `url`, and useful `section` properties.
- Every new content page must include exactly one each of the existing Umami
  loader, `assets/js/link-tracking.js`, and `assets/js/page-visibility.js`, using
  paths appropriate to that page and the existing website ID. Preserve the
  shared timer's configured milestones, visible-only accumulation, navigation
  lifecycle, memory-only state, and duplicate guards.
- For every added/changed link or quote, including every paper destination,
  verify its descriptive name, full label, URL, placement, and exactly one event
  per activation (or its intentional tracking exclusion), with normal navigation.
  Run the protected tests above; they discover content pages and sweep their
  links. Verify new pages include both shared scripts and preserve timer behavior.
  Always use the opt-out and mocked/intercepted analytics rules above.

## README edits

Preserve the original README title and webpage link unless the user explicitly
requests a content change. Before any README edit, explicitly tell the user that
`README.md` will be changed and describe the intended change. Never silently add
analytics, maintenance, testing, or other documentation to the README.
