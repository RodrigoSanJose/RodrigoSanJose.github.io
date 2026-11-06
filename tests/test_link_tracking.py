"""Browser checks with every external request intercepted (no real analytics).

Run with Python's Playwright package installed:
    python -B -m unittest discover -s tests -v
Uses the system Chromium, or CHROMIUM_PATH when set.
"""

import functools
import http.server
from html.parser import HTMLParser
import os
from pathlib import Path
import threading
import unittest
from urllib.parse import urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
UMAMI_SCRIPT_URL = "https://cloud.umami.is/script.js"
UMAMI_WEBSITE_ID = "6c3573da-71d0-4f6e-9d45-f848a93e9aa7"
# Applied before every document, including popups. The mock below intentionally
# records API calls despite the opt-out; it never sends anything to Umami.
UMAMI_OPT_OUT = """
try { localStorage.setItem('umami.disabled', '1'); } catch (error) {}
"""
MOCK_UMAMI = """
window.__analyticsEvents = [];
window.umami = {
    track(name, data) {
        const payload = typeof name === 'function'
            ? name({url: location.pathname + location.search})
            : {name, data};
        const event = {name: payload.name, data: payload.data};
        if ('url' in payload) event.url = payload.url;
        window.__analyticsEvents.push(event);
        window.recordAnalytics(event);
        return Promise.resolve();
    }
};
"""


class ContentPageParser(HTMLParser):
    def __init__(self):
        super().__init__()
        self.has_body = False

    def handle_starttag(self, tag, attrs):
        if tag == "body":
            self.has_body = True


def content_pages():
    """Discover site documents, including future nested pages, by HTML structure."""
    pages = []
    for directory, children, files in os.walk(ROOT):
        children[:] = [name for name in children if not name.startswith(".") and name not in
                       {"assets", "tests", "work", "node_modules", "vendor"}]
        for filename in files:
            if filename.endswith(".html"):
                path = Path(directory) / filename
                parser = ContentPageParser()
                parser.feed(path.read_text(encoding="utf-8"))
                if parser.has_body:
                    pages.append(path.relative_to(ROOT).as_posix())
    return sorted(pages)


class QuietHandler(http.server.SimpleHTTPRequestHandler):
    def log_message(self, *args):
        pass

    def do_GET(self):
        try:
            super().do_GET()
        except ConnectionError:
            # Popups can close while Chromium is still fetching page assets.
            pass


class LinkTrackingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        handler = functools.partial(QuietHandler, directory=str(ROOT))
        cls.server = http.server.ThreadingHTTPServer(("127.0.0.1", 0), handler)
        cls.server_thread = threading.Thread(target=cls.server.serve_forever, daemon=True)
        cls.server_thread.start()
        cls.base = f"http://127.0.0.1:{cls.server.server_port}"
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"),
            args=["--no-sandbox"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()
        cls.server.shutdown()
        cls.server.server_close()
        cls.server_thread.join()

    def setUp(self):
        self.events = []
        self.errors = []
        self.context = self.browser.new_context(accept_downloads=True)
        self.context.add_init_script(UMAMI_OPT_OUT)
        self.context.expose_binding("recordAnalytics", lambda source, event: self.events.append(event))
        self.context.route("**/*", self.route_request)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.goto(self.base + "/index.html", wait_until="load")

    def tearDown(self):
        self.context.close()

    def route_request(self, route):
        url = route.request.url
        if url.startswith(self.base + "/"):
            route.continue_()
        elif url == UMAMI_SCRIPT_URL:
            route.fulfill(status=200, content_type="application/javascript", body=MOCK_UMAMI)
        elif route.request.resource_type == "script":
            route.fulfill(status=200, content_type="application/javascript", body="")
        else:
            # Also intercept link destinations in popups; never visit production.
            route.fulfill(status=200, content_type="text/plain", body="Intercepted by analytics tests")

    def prevent_navigation(self):
        self.page.evaluate("""() => {
            document.addEventListener('click', event => event.preventDefault());
            document.addEventListener('auxclick', event => event.preventDefault());
        }""")

    def mock_events(self):
        return self.page.evaluate("window.__analyticsEvents")

    def dispatch(self, selector, event_type="click", button=0):
        self.page.locator(selector).first.dispatch_event(
            event_type, {"button": button, "bubbles": True, "cancelable": True}
        )

    def test_every_existing_link_and_control_has_one_descriptive_event(self):
        totals = {}
        publications = []
        quote_events = []
        pages = content_pages()
        self.assertTrue(pages, "No content HTML pages discovered")
        for filename in pages:
            with self.subTest(page=filename):
                self.page.goto(self.base + "/" + filename, wait_until="load")
                self.assertEqual(self.page.evaluate("localStorage.getItem('umami.disabled')"), "1")
                scripts = self.page.evaluate("""() => Array.from(document.scripts, script => ({
                    src: script.src, websiteId: script.getAttribute('data-website-id')
                }))""")
                for shared_script in ("link-tracking.js", "page-visibility.js"):
                    self.assertEqual(sum(urlsplit(script["src"]).path.endswith("/assets/js/" + shared_script)
                                         for script in scripts), 1, shared_script)
                loaders = [script for script in scripts if script["src"] == UMAMI_SCRIPT_URL]
                self.assertEqual(len(loaders), 1)
                self.assertEqual(loaders[0]["websiteId"], UMAMI_WEBSITE_ID)
                sweep = self.page.evaluate(r"""() => {
                    document.addEventListener('click', event => event.preventDefault());
                    const controls = Array.from(document.querySelectorAll(
                        'a[href], area[href], button, input[type="button"], input[type="submit"], input[type="reset"], [role="button"]'));
                    const isLink = control => control.matches('a[href], area[href]');
                    const destination = control => new URL(
                        isLink(control) ? control.getAttribute('href') : location.href, document.baseURI);
                    const eligible = control => /^(https?:|mailto:|tel:)$/.test(destination(control).protocol) &&
                        !control.closest('[data-analytics-ignore], [data-umami-event], [inert], [aria-disabled="true"]') &&
                        !control.matches(':disabled, [disabled]');
                    const declaredLabel = control => {
                        const labelledBy = (control.getAttribute('aria-labelledby') || '').split(/\s+/)
                            .map(id => document.getElementById(id)?.textContent || '').join(' ');
                        return [labelledBy, control.getAttribute('aria-label'), control.getAttribute('title'),
                            control.textContent, control.querySelector('img[alt]')?.getAttribute('alt')]
                            .find(label => label && label.trim())?.trim() || '';
                    };
                    const inventory = {
                        controls: controls.length,
                        eligibleControls: controls.filter(eligible).length,
                        publications: controls.filter(control => eligible(control) && control.closest('[data-publication-title]')).length,
                        quotes: controls.filter(control => eligible(control) && control.closest('[data-analytics-section^="Quotes"]')).length
                    };
                    const rows = controls.map(control => {
                        const before = window.__analyticsEvents.length;
                        control.dispatchEvent(new MouseEvent('click', {bubbles: true, cancelable: true, button: 0}));
                        const quoteSection = control.closest('[data-analytics-section^="Quotes"]');
                        const numericContext = {};
                        for (const key of ['q', 'm', 'd', 'r']) {
                            const attribute = 'data-analytics-' + key;
                            const raw = control.closest('[' + attribute + ']')?.getAttribute(attribute).trim();
                            if (raw && /^\d+$/.test(raw) && Number.isSafeInteger(Number(raw))) numericContext[key] = Number(raw);
                        }
                        return {
                            control: control.tagName + (control.id ? '#' + control.id : ''),
                            href: destination(control).href,
                            eligible: eligible(control),
                            action: !isLink(control),
                            declaredLabel: declaredLabel(control),
                            numericContext,
                            publication: control.closest('[data-publication-title]')?.getAttribute('data-publication-title'),
                            publicationSection: Boolean(control.closest('[data-publications]')) ||
                                /^(Journal papers|Conference papers|Preprints)$/.test(
                                    control.closest('section')?.querySelector('h2')?.textContent.trim() || ''),
                            quote: Boolean(quoteSection),
                            quoteNumber: quoteSection && control.textContent.trim().match(/^\[(\d+)\]$/)?.[1],
                            quoteCategory: quoteSection && quoteSection.firstChild.textContent.trim().replace(/:$/, ''),
                            events: window.__analyticsEvents.slice(before)
                        };
                    });
                    return {inventory, rows};
                }""")
                totals[filename] = sweep["inventory"]
                self.assertEqual(len(sweep["rows"]), sweep["inventory"]["controls"])
                for row in sweep["rows"]:
                    with self.subTest(control=row["control"], href=row["href"]):
                        if row["publicationSection"]:
                            self.assertTrue(row["publication"] and row["publication"].strip(),
                                            "Publication link is missing its shared title wrapper")
                        self.assertEqual(len(row["events"]), 1 if row["eligible"] else 0)
                        if not row["eligible"]:
                            continue
                        if row["action"]:
                            self.assertTrue(row["declaredLabel"],
                                            "Action control needs descriptive visible text or an accessible label")
                        event = row["events"][0]
                        self.assertTrue(event["name"].strip())
                        self.assertLessEqual(len(event["name"]), 50)
                        self.assertNotEqual(event["name"], "link-click")
                        self.assertEqual(event["data"]["url"], row["href"])
                        self.assertTrue(event["data"]["label"])
                        self.assertTrue(event["data"]["section"])
                        self.assertEqual(set(event["data"]), {"label", "url", "section"} | set(row["numericContext"]))
                        for key, value in row["numericContext"].items():
                            self.assertEqual(event["data"][key], value)
                        if row["publication"]:
                            self.assertTrue(event["data"]["label"].startswith(row["publication"] + ". "))
                            destination_type = event["data"]["label"][len(row["publication"]) + 2:]
                            self.assertTrue(destination_type)
                            if len(destination_type) <= 30:
                                self.assertTrue(event["name"].endswith(". " + destination_type))
                            publications.append(event)
                        if row["quote"]:
                            if row["quoteNumber"]:
                                category = {
                                    "Related to work": "Work",
                                    "Literature": "Literature",
                                    "Miscellaneous": "Misc",
                                }.get(row["quoteCategory"], row["quoteCategory"])
                                prefix = f"Quote {row['quoteNumber']} ({category}): "
                                self.assertTrue(event["name"].startswith(prefix), event["name"])
                                self.assertTrue(event["data"]["label"].startswith(prefix), event["data"]["label"])
                            else:
                                # Future author/source attribution links need a
                                # descriptive label, without an invented number.
                                self.assertNotRegex(event["name"], r"^[\d\s\[\]().,]+$")
                                self.assertNotRegex(event["data"]["label"], r"^[\d\s\[\]().,]+$")
                            quote_events.append(event)
        self.assertGreater(sum(inventory["eligibleControls"] for inventory in totals.values()), 0)
        self.assertEqual(len(publications), sum(inventory["publications"] for inventory in totals.values()))
        self.assertEqual(len(quote_events), sum(inventory["quotes"] for inventory in totals.values()))
        for events in (publications, quote_events):
            names = {}
            labels = {}
            for event in events:
                # Repeated logical links may share a name across pages. Different
                # descriptive labels must remain distinguishable, and names stable.
                self.assertEqual(names.setdefault(event["name"], event["data"]["label"]), event["data"]["label"])
                self.assertEqual(labels.setdefault(event["data"]["label"], event["name"]), event["name"])
        self.assertEqual(self.errors, [])

    def test_representative_labels_and_nested_icons(self):
        self.prevent_navigation()
        expected = {
            'a[href="https://doi.org/10.48550/arXiv.2511.08304"] i': "Cartesian square-free codes. Arxiv",
            'a[href="https://doi.org/10.1007/s40590-026-00963-5"] i': "Cartesian square-free codes. Published paper",
            'a[href^="https://scholar.google.com/"]': "Google Scholar",
            'a[href^="https://www.linkedin.com/"]': "LinkedIn",
            'a[href="https://github.com/RodrigoSanJose"]': "GitHub",
            'a[href^="mailto:"] span': "Email",
        }
        for selector, name in expected.items():
            with self.subTest(name=name):
                before = len(self.mock_events())
                self.dispatch(selector)
                events = self.mock_events()
                self.assertEqual(len(events), before + 1)
                self.assertEqual(events[-1]["name"], name)
        self.assertEqual(self.mock_events()[-1]["data"]["section"], "Footer")

    def test_dynamic_links_images_sections_and_publication_destinations(self):
        self.prevent_navigation()
        self.page.evaluate("""() => {
            const section = document.createElement('section');
            section.setAttribute('data-analytics-section', 'Dynamic section');
            section.innerHTML = `
                <a id="dynamic-image" href="#two"><img alt="Research overview" src="images/Rodrigo.JPG"></a>
                <span data-publication-title="A future publication with a long and complete descriptive title"
                      data-publication-short="Future publication">
                    <a id="future-pdf" href="future.pdf" aria-label="PDF"><span>PDF</span></a>
                    <a id="future-code" href="https://example.test/code" aria-label="Code"><i>Code</i></a>
                    <a id="future-supplement" href="supplement.zip" aria-label="Supplementary material">Supplementary material</a>
                    <a id="future-custom" href="experiments.zip"
                       data-link-type="Supplementary interactive visualization and complete computational experiment archive">Experiments</a>
                </span>`;
            document.body.append(section);
        }""")
        for selector in ("#dynamic-image img", "#future-pdf span", "#future-code i", "#future-supplement", "#future-custom"):
            self.dispatch(selector)
        events = self.mock_events()
        self.assertEqual(len(events), 5)
        self.assertEqual(events[0]["name"], "Research overview")
        for event, suffix in zip(events[1:], ("PDF", "Code", "Supplementary material")):
            self.assertTrue(event["name"].endswith(". " + suffix))
            self.assertLessEqual(len(event["name"]), 50)
            self.assertEqual(event["data"]["label"], "A future publication with a long and complete descriptive title. " + suffix)
        self.assertLessEqual(len(events[-1]["name"]), 50)
        self.assertEqual(events[-1]["data"]["label"],
                         "A future publication with a long and complete descriptive title. "
                         "Supplementary interactive visualization and complete computational experiment archive")
        self.assertTrue(all(event["data"]["section"] == "Dynamic section" for event in events))

    def test_future_publications_infer_types_and_keep_names_stable(self):
        self.prevent_navigation()
        title_prefix = "An automatic publication regression with a shared and deliberately long title for "
        titles = (title_prefix + "classical coding theory", title_prefix + "quantum coding theory")
        self.page.evaluate("""titles => {
            const section = document.createElement('section');
            section.setAttribute('data-analytics-section', 'Future papers');
            section.setAttribute('data-publications', '');
            const paper = document.createElement('span');
            paper.id = 'future-inferred';
            paper.setAttribute('data-publication-title', titles[0]);
            paper.innerHTML = `
                <a id="inferred-arxiv" href="https://arxiv.org/abs/9999.00001"><i class="ai ai-arxiv"></i></a>
                <a id="inferred-doi" href="https://doi.org/10.1000/future-paper"><span>Journal</span></a>
                <a id="inferred-pdf" href="future-paper.pdf?download=1"><span>Download</span></a>
                <a id="inferred-arxiv-pdf" href="https://arxiv.org/pdf/9999.00001"><span>Download</span></a>
                <a id="inferred-icon-pdf" href="https://example.test/download/123"><i class="fas fa-file-pdf"></i></a>
                <a id="inferred-text-pdf" href="https://example.test/download/456"><span>PDF</span></a>
                <a id="inferred-code" href="https://github.com/example/future-paper"><i class="fab fa-github"></i></a>
                <a id="inferred-supplement" href="supplement.zip" data-link-type="Supplementary material"><span>Materials</span></a>`;
            const otherPaper = document.createElement('span');
            otherPaper.setAttribute('data-publication-title', titles[1]);
            otherPaper.innerHTML = '<a id="other-arxiv" href="https://arxiv.org/abs/9999.00002"><span>Preprint</span></a>';
            section.append(paper, otherPaper);
            document.getElementById('main').append(section);
        }""", titles)
        self.assertIsNone(self.page.locator("#future-inferred").get_attribute("data-publication-short"))
        self.assertEqual(self.page.locator("#future-inferred [aria-label], #future-inferred [data-analytics-name]").count(), 0)
        # Insert another link after the publication already exists; no observer or
        # per-link registration should be necessary for delegated instrumentation.
        self.page.locator("#future-inferred").evaluate("""paper => {
            const link = document.createElement('a');
            link.id = 'inferred-late-pdf';
            link.href = 'https://example.test/generated.pdf';
            link.innerHTML = '<span>New download</span>';
            paper.append(link);
        }""")
        cases = (
            ("inferred-arxiv", "Arxiv", titles[0]),
            ("inferred-doi", "Published paper", titles[0]),
            ("inferred-pdf", "PDF", titles[0]),
            ("inferred-arxiv-pdf", "PDF", titles[0]),
            ("inferred-icon-pdf", "PDF", titles[0]),
            ("inferred-text-pdf", "PDF", titles[0]),
            ("inferred-code", "Code", titles[0]),
            ("inferred-supplement", "Supplementary material", titles[0]),
            ("inferred-late-pdf", "PDF", titles[0]),
            ("other-arxiv", "Arxiv", titles[1]),
            ("inferred-arxiv", "Arxiv", titles[0]),
        )
        names = {}
        for link_id, destination_type, title in cases:
            with self.subTest(link=link_id, title=title):
                before = len(self.mock_events())
                url = self.page.locator("#" + link_id).evaluate("link => link.href")
                self.dispatch("#" + link_id + " > *")
                events = self.mock_events()
                self.assertEqual(len(events), before + 1)
                event = events[-1]
                self.assertEqual(event["data"], {
                    "label": title + ". " + destination_type,
                    "url": url,
                    "section": "Future papers",
                })
                self.assertLessEqual(len(event["name"]), 50)
                self.assertTrue(event["name"].endswith(". " + destination_type))
                logical_link = (title, destination_type)
                self.assertEqual(names.setdefault(logical_link, event["name"]), event["name"])
        self.assertEqual(len(set(names.values())), len(names))

    def test_duplicate_registration_and_activation_buttons(self):
        self.prevent_navigation()
        self.page.add_script_tag(url=self.base + "/assets/js/link-tracking.js")
        selector = 'a[href="https://github.com/RodrigoSanJose"]'
        self.dispatch(selector)
        self.dispatch(selector, "click", 1)
        self.dispatch(selector, "auxclick", 1)
        self.dispatch(selector, "auxclick", 2)
        self.dispatch(selector, "click", 2)
        self.assertEqual([event["name"] for event in self.mock_events()], ["GitHub", "GitHub"])

    def test_dynamic_native_buttons_keyboard_and_numeric_context(self):
        self.page.evaluate("""() => {
            const section = document.createElement('section');
            section.setAttribute('data-analytics-section', 'Future GHW controls');
            section.setAttribute('data-analytics-q', '4');
            section.setAttribute('data-analytics-m', '3');
            section.setAttribute('data-analytics-d', '9');
            section.setAttribute('data-analytics-r', '1');
            section.innerHTML = `
                <div data-analytics-r="2" data-user-email="private@example.test">
                    <button id="future-button" type="button" aria-label="Show future GHW parameters"
                            value="private visitor value" data-arbitrary="private visitor metadata">
                        <span><svg aria-hidden="true" width="10" height="10"><path d="M0 0h10v10H0z"></path></svg></span>
                    </button>
                    <input id="future-input" type="button" aria-label="Reset future GHW filters"
                           value="private visitor value" data-arbitrary="private visitor metadata">
                    <input id="unlabelled-input" type="button" value="private visitor value"
                           data-analytics-q="4.5" data-analytics-m="-3" data-analytics-d="NaN"
                           data-analytics-r="9007199254740992">
                    <input type="text" name="private-name" value="private form contents">
                </div>`;
            window.__nativeActivations = 0;
            section.addEventListener('click', () => window.__nativeActivations++);
            document.getElementById('main').append(section);
        }""")
        # Reinclusion must not duplicate the delegated button handler either.
        self.page.add_script_tag(url=self.base + "/assets/js/link-tracking.js")
        self.page.locator("#future-button svg").click()
        self.page.locator("#future-button").focus()
        for count, key in ((2, "Enter"), (3, "Space")):
            with self.subTest(key=key):
                self.page.keyboard.press(key)
                self.assertEqual(len(self.mock_events()), count)
                self.assertEqual(self.page.evaluate("window.__nativeActivations"), count)
        self.dispatch("#future-button svg", "auxclick", 1)
        self.assertEqual(len(self.mock_events()), 3)
        self.page.locator("#future-input").click()
        events = self.mock_events()
        self.assertEqual(len(events), 4)
        self.assertEqual(self.page.evaluate("window.__nativeActivations"), 4)
        for index, event in enumerate(events):
            label = "Show future GHW parameters" if index < 3 else "Reset future GHW filters"
            self.assertEqual(event, {
                "name": label,
                "data": {
                    "label": label,
                    "url": self.base + "/index.html",
                    "section": "Future GHW controls",
                    "q": 4, "m": 3, "d": 9, "r": 2,
                },
            })
        # Input values are never label fallbacks; invalid table metadata and
        # arbitrary data attributes must not become analytics properties.
        self.page.locator("#unlabelled-input").click()
        self.assertEqual(self.mock_events()[-1], {
            "name": "Button",
            "data": {
                "label": "Button",
                "url": self.base + "/index.html",
                "section": "Future GHW controls",
            },
        })
        self.assertEqual(self.errors, [])

    def test_disabled_inert_and_native_event_controls_are_excluded(self):
        self.page.evaluate("""() => {
            document.body.insertAdjacentHTML('beforeend', `
                <button id="disabled-control" disabled><span>Disabled button</span></button>
                <fieldset disabled><button id="disabled-fieldset-control"><span>Disabled fieldset</span></button></fieldset>
                <div inert><button id="inert-control"><span>Inert button</span></button></div>
                <button id="aria-disabled-control" aria-disabled="true"><span>Unavailable button</span></button>
                <div aria-disabled="true"><button id="aria-disabled-parent-control"><span>Unavailable group</span></button></div>
                <div data-analytics-ignore><button id="ignored-control"><span>Ignored button</span></button></div>
                <button id="native-control" data-umami-event="Native event"><span>Native button</span></button>
                <div data-umami-event="Native group"><button id="native-parent-control"><span>Native group button</span></button></div>
                <button id="native-icon-control"><span data-umami-event="Native icon">Native icon</span></button>
                <button id="ignored-icon-control"><span data-analytics-ignore>Ignored icon</span></button>
                <button id="inert-icon-control"><span inert>Inert icon</span></button>
                <button id="aria-disabled-icon-control"><span aria-disabled="true">Unavailable icon</span></button>`);
        }""")
        for control_id in (
            "disabled-control", "disabled-fieldset-control", "inert-control", "aria-disabled-control",
            "aria-disabled-parent-control", "ignored-control", "native-control", "native-parent-control",
            "native-icon-control", "ignored-icon-control", "inert-icon-control", "aria-disabled-icon-control",
        ):
            with self.subTest(control=control_id):
                # Synthetic dispatch reaches the delegate even where the browser
                # suppresses physical activation of disabled or inert controls.
                self.dispatch("#" + control_id + " span")
                self.assertEqual(self.mock_events(), [])
        self.assertEqual(self.errors, [])

    def test_tracking_failures_are_harmless_and_late_loading_works(self):
        self.prevent_navigation()
        self.page.evaluate("window.__savedUmami = window.umami; delete window.umami")
        self.dispatch('a[href="books.html"]')
        self.page.evaluate("window.umami = {track() {throw new Error('Blocked tracker')}}")
        self.dispatch('a[href="books.html"]')
        self.page.evaluate("window.umami = {track() {return Promise.reject(new Error('Blocked tracker'))}}")
        self.dispatch('a[href="books.html"]')
        self.page.wait_for_timeout(20)
        self.assertEqual(self.errors, [])
        self.assertEqual(self.mock_events(), [])
        self.page.evaluate("window.umami = window.__savedUmami")
        self.dispatch('a[href="books.html"]')
        self.assertEqual(len(self.mock_events()), 1)
        for implementation in (
            "throw new Error('Blocked tracker')",
            "return Promise.reject(new Error('Blocked tracker'))",
        ):
            with self.subTest(tracker=implementation):
                # A new document removes the synthetic sweep's navigation blocker.
                self.page.goto(self.base + "/index.html", wait_until="load")
                self.page.evaluate("window.umami = {track() {" + implementation + "}}")
                self.page.locator('a[href="books.html"]').click()
                self.page.wait_for_url(self.base + "/books.html")
                self.assertEqual(len(self.events), 1)
                self.assertEqual(self.errors, [])

    def test_explicit_exclusions_and_native_umami_events_are_not_double_tracked(self):
        self.prevent_navigation()
        self.page.evaluate("""() => {
            document.body.insertAdjacentHTML('beforeend', `
                <div data-analytics-ignore><a id="ignored" href="#two"><span>Ignored</span></a></div>
                <a id="native" href="#two" data-umami-event="Native event"><span>Native</span></a>`);
        }""")
        self.dispatch("#ignored span")
        self.dispatch("#native span")
        self.assertEqual(self.mock_events(), [])

    def test_keyboard_navigation_and_unavailable_tracker(self):
        self.page.locator('a[href="books.html"]').focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_url(self.base + "/books.html")
        self.assertEqual(len(self.events), 1)
        self.page.evaluate("delete window.umami")
        self.page.locator('#header nav.site-nav a[href="/"]').focus()
        self.page.keyboard.press("Enter")
        self.page.wait_for_url(self.base + "/")
        self.assertEqual(len(self.events), 1)

    def test_books_home_return_has_one_descriptive_event(self):
        self.page.goto(self.base + "/books.html", wait_until="load")
        self.page.locator('#header nav.site-nav a[href="/"]').click()
        self.page.wait_for_url(self.base + "/")
        self.assertEqual(self.events, [{
            "name": "Home",
            "data": {
                "label": "Home",
                "url": self.base + "/",
                "section": "Navigation",
            },
        }])
        self.assertEqual(self.errors, [])

    def test_site_navigation_marks_the_current_page_and_tracks_the_others(self):
        for filename, current, links in (
            ("index.html", "Home", (("/ghw-tables/", "GHW tables"), ("/books.html", "Tier lists"))),
            ("books.html", "Tier lists", (("/", "Home"), ("/ghw-tables/", "GHW tables"))),
        ):
            with self.subTest(page=filename):
                self.page.goto(self.base + "/" + filename, wait_until="load")
                self.prevent_navigation()
                nav = self.page.locator("#header nav.site-nav")
                self.assertEqual(nav.locator('[aria-current="page"]').text_content(), current)
                self.assertEqual(nav.locator("a").count(), 2)
                for href, name in links:
                    self.page.evaluate("window.__analyticsEvents.length = 0")
                    self.dispatch(f'#header nav.site-nav a[href="{href}"]')
                    self.assertEqual(self.mock_events(), [{
                        "name": name,
                        "data": {"label": name, "url": self.base + href, "section": "Navigation"},
                    }])
        self.assertEqual(self.errors, [])

    def test_modifier_middle_and_blank_target_preserve_new_tabs(self):
        for click_options in ({"modifiers": ["Control"]}, {"button": "middle"}, {}):
            with self.subTest(options=click_options):
                selector = ('a[href="books.html"]' if click_options else
                            'a[href="https://github.com/RodrigoSanJose"]')
                before = len(self.mock_events())
                with self.context.expect_page() as popup_info:
                    self.page.locator(selector).click(**click_options)
                popup = popup_info.value
                popup.wait_for_load_state()
                self.assertIn(popup.url, (self.base + "/books.html", "https://github.com/RodrigoSanJose"))
                self.assertEqual(self.page.url, self.base + "/index.html")
                self.assertEqual(len(self.mock_events()), before + 1)
                popup.close()

    def test_download_and_hash_navigation_are_preserved(self):
        self.page.evaluate("""() => {
            document.getElementById('main').insertAdjacentHTML('beforeend', `
                <a id="download" href="phd.pdf" download="thesis.pdf" aria-label="PhD thesis">Download thesis</a>
                <a id="hash" href="#two">Journal papers</a>`);
        }""")
        with self.page.expect_download() as download_info:
            self.page.locator("#download").click()
        self.assertEqual(download_info.value.suggested_filename, "thesis.pdf")
        self.assertEqual(download_info.value.failure(), None)
        self.page.locator("#hash").click()
        self.page.wait_for_url(self.base + "/index.html#two")
        self.assertEqual(len(self.mock_events()), 2)


if __name__ == "__main__":
    unittest.main()
