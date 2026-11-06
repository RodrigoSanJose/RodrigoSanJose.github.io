# Personal webpage of Rodrigo San-José
Link to the webpage: https://rodrigosanjose.github.io/.

## Link analytics

Both content pages load `assets/js/link-tracking.js` and the existing Umami
Cloud tracker. The shared handler tracks links, including nested icons/images,
keyboard Enter, modifier clicks, middle clicks, downloads, and dynamically added
links. It never cancels or delays navigation. It sends only `label` (the complete
descriptive label), `url` (the resolved destination), and `section` (placement)
through `window.umami.track`. Umami's existing exclusions and opt-out behavior
remain responsible for whether requests are sent. No cookies, custom visitor IDs,
form values, or additional analytics service are introduced.

Tracking is part of every content update, including new ordinary links, quotes,
source/attribution links, navigation items, profiles, icons/images, and downloads;
it does not require a separate request. The shared delegated handler covers new
ordinary links automatically, including links inserted after page load. Give
icon links an accessible
`aria-label`; visible text, `aria-labelledby`, `title`, and image `alt` are also
supported. Use `data-analytics-label` to override the full event label,
`data-analytics-name` for a readable event name of at most 50 characters, and
`data-analytics-section` on a link or container to identify placement. Long labels
without a short name receive a deterministic shortened name with a distinguishing
hash; the complete label is always retained in the `label` property.

Mark publication sections with `data-publications` and keep each new paper's
citation and links in the same metadata wrapper. Its title is supplied once for
tracking; all present and future links inside it derive their names automatically,
including links inserted after page load. No per-link event names or JavaScript
changes are needed when adding a paper:

```html
<span data-publication-title="Complete paper title"
      data-publication-short="Meaningful short paper title">
    Authors. Complete paper title. Journal citation.
    <a href="https://arxiv.org/abs/..." aria-label="Arxiv"><i class="ai ai-arxiv"></i></a>
    <a href="paper.pdf" aria-label="PDF">PDF</a>
    <a href="supplement.zip" data-link-type="Supplementary material">Supplement</a>
</span>
```

Publication names use `Title. Destination type`. Arxiv, Published paper, PDF, and
Code are inferred from existing icon classes and destination URLs (including
arXiv PDF URLs without a `.pdf` extension). Other types
use `data-link-type` or the link's accessible label. Choose an unambiguous short
title so it and the longest destination type fit within 50 characters (33 title
characters when using `. Published paper`). The full title remains in `label`.
Quote sources use `Quote N (Work|Literature|Misc): Description`, matching each
visible reference number. Their full labels and section metadata retain the
source context. When adding a quote, include its descriptive accessible label
and a short event name in that format; the shared handler tracks it and its
source/attribution links without any new JavaScript listener.

`data-analytics-ignore` on a link or ancestor excludes it from this handler.
Native `data-umami-event` links are left to Umami to prevent duplicate events;
prefer the shared handler's metadata for new links. The handler is idempotent.
If Umami is unavailable or not loaded at activation time, navigation continues
and the event is skipped without replay. Browser context-menu actions do not
emit a page click event and cannot be observed by this handler. Destination sites
and links inside PDF viewers are outside this site's instrumentation.

In Umami, select this website and a date range, then open **Events** and select
an event to inspect its **Event data** properties (`label`, `url`, and `section`).
Events appear after this source is deployed and visitors activate links.

## Visible page time

Both pages load `assets/js/page-visibility.js`. Its milestone list is configured
at the top of that shared file: 30, 60, 120, 300, and 600 seconds by default.
Events are named `Page visible. 30s`, `Page visible. 60s`, and so on, with numeric
`visible_seconds` and a `page` URL property (without the fragment).

The timer accumulates actual elapsed time only while the document is visible,
pauses while hidden, and fires each milestone once per visit. Tab switches and
fragment navigation retain the accumulated time. New page visits, including
history restoration and path/query changes through the History API, start fresh;
time spent away from a page is never added. Timer state stays in memory. Missing
or failing trackers skip reached milestones without replay or navigation changes.

## Excluding development visits

Agent browser contexts set `localStorage.setItem('umami.disabled', '1')` before
site scripts run and intercept external requests as a second safeguard. The
repository's `AGENTS.md` preserves this requirement for future agent work here.
The flag applies to that browser and origin; it is not a global account setting.
To opt your own browser out, run that command in its developer console while on
the website, then reload. Production JavaScript never sets this flag for visitors.

## Local verification

With Python's `playwright` package and system Chromium installed, run:

```sh
python -m unittest discover -s tests -v
```

Set `CHROMIUM_PATH` if Chromium is installed elsewhere. Tests discover content
pages automatically, check their shared script includes and publication wrappers,
and sweep their links. They serve loopback or intercepted local responses,
replace Umami with a mock, and intercept every external request, including popup
destinations. They verify event names, full labels, uniqueness, placement, dynamic
paper/link additions, navigation, downloads, duplicate registration, timer behavior,
and missing/failing trackers without sending analytics. Follow the tracking
maintenance instructions in `AGENTS.md` for any content update, such as “Add this
link,” “Add this quote,” or “Update my website with this new paper [link]”;
tracking and its verification are part of that update.
