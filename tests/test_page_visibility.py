"""Visible-time browser checks with a controlled clock and no real analytics.

Run: python -B -m unittest discover -s tests -v
Requires Python Playwright and Chromium (or set CHROMIUM_PATH).
"""

import datetime
import mimetypes
import os
from pathlib import Path
import unittest
from urllib.parse import unquote, urlsplit

from playwright.sync_api import sync_playwright


ROOT = Path(__file__).resolve().parents[1]
SITE = "https://site.test"
INITIAL_TIME = datetime.datetime(2026, 1, 1, tzinfo=datetime.timezone.utc)
MILESTONES = [30, 60, 120, 300, 600]
MOCK_UMAMI = """
window.__visibilityEvents = [];
window.umami = {
    track(event, data) {
        const current = {url: location.pathname + location.search};
        const payload = typeof event === 'function' ? event(current) : {...current, name: event, data};
        if (payload.name.startsWith('Page visible. ')) {
            window.__visibilityEvents.push({name: payload.name, data: payload.data, url: payload.url});
        }
        return Promise.resolve();
    }
};
"""


class PageVisibilityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.playwright = sync_playwright().start()
        cls.browser = cls.playwright.chromium.launch(
            executable_path=os.environ.get("CHROMIUM_PATH", "/usr/bin/chromium"),
            args=["--no-sandbox"],
        )

    @classmethod
    def tearDownClass(cls):
        cls.browser.close()
        cls.playwright.stop()

    def setUp(self):
        self.errors = []
        self.script_override = None
        self.context = self.browser.new_context()
        # Agent browsing must opt out even though every request below is mocked.
        self.context.add_init_script("""
            try { localStorage.setItem('umami.disabled', '1'); } catch (error) {}
            window.__testVisibility = 'visible';
            Object.defineProperty(document, 'visibilityState', {
                configurable: true,
                get: () => window.__testVisibility
            });
        """)
        self.context.route("**/*", self.route_request)
        self.page = self.context.new_page()
        self.page.on("pageerror", lambda error: self.errors.append(str(error)))
        self.page.clock.install(time=INITIAL_TIME)
        self.page.clock.pause_at(INITIAL_TIME)
        self.page.goto(SITE + "/index.html", wait_until="load")

    def tearDown(self):
        self.context.close()

    def route_request(self, route):
        request = route.request
        url = urlsplit(request.url)
        if request.url == "https://cloud.umami.is/script.js":
            route.fulfill(content_type="application/javascript", body=MOCK_UMAMI)
        elif request.url.startswith(SITE + "/"):
            relative = unquote(url.path).lstrip("/") or "index.html"
            path = (ROOT / relative).resolve()
            if path.is_relative_to(ROOT) and path.is_file():
                body = path.read_bytes()
                if relative == "assets/js/page-visibility.js" and self.script_override:
                    body = self.script_override.encode()
                route.fulfill(
                    content_type=mimetypes.guess_type(path)[0] or "application/octet-stream",
                    body=body,
                )
            else:
                route.fulfill(status=404, body="Missing local test resource")
        elif request.resource_type == "script":
            route.fulfill(content_type="application/javascript", body="")
        else:
            route.fulfill(content_type="text/plain", body="Intercepted by visibility tests")

    def advance(self, seconds):
        self.page.clock.run_for(seconds * 1000)

    def events(self):
        return self.page.evaluate("window.__visibilityEvents")

    def seconds(self):
        return [event["data"]["visible_seconds"] for event in self.events()]

    def visibility(self, state):
        self.page.evaluate("""state => {
            window.__testVisibility = state;
            document.dispatchEvent(new Event('visibilitychange'));
        }""", state)

    def lifecycle(self, event):
        self.page.evaluate("""type => {
            window.dispatchEvent(new PageTransitionEvent(type, {persisted: true}));
        }""", event)

    def test_default_milestones_once_with_numeric_page_properties(self):
        self.advance(29)
        self.assertEqual(self.events(), [])
        self.advance(1)
        self.assertEqual(self.seconds(), [30])
        self.advance(570)
        self.assertEqual(self.seconds(), MILESTONES)
        for event, milestone in zip(self.events(), MILESTONES):
            self.assertEqual(event["name"], f"Page visible. {milestone}s")
            self.assertEqual(event["data"], {"visible_seconds": milestone, "page": SITE + "/index.html"})
            self.assertEqual(event["url"], "/index.html")
            self.assertIsInstance(event["data"]["visible_seconds"], (int, float))
        self.advance(600)
        self.assertEqual(self.seconds(), MILESTONES)

    def test_hidden_time_pauses_and_resumes_accumulated_visible_time(self):
        self.advance(20)
        self.visibility("hidden")
        self.advance(900)
        self.assertEqual(self.events(), [])
        self.visibility("visible")
        self.advance(9)
        self.assertEqual(self.events(), [])
        self.advance(1)
        self.assertEqual(self.seconds(), [30])
        self.visibility("hidden")
        self.advance(900)
        self.visibility("visible")
        self.advance(30)
        self.assertEqual(self.seconds(), [30, 60])

    def test_late_timer_checks_visibility_before_counting_hidden_interval(self):
        # Even if a visibilitychange task has not reached the listener yet, a
        # callback must not interpret a hidden document's elapsed time as visible.
        self.page.evaluate("window.__testVisibility = 'hidden'")
        self.advance(60)
        self.assertEqual(self.events(), [])
        self.visibility("visible")
        self.advance(30)
        self.assertEqual(self.seconds(), [30])

    def test_delayed_callback_uses_elapsed_time_and_catches_crossed_milestones(self):
        # Fast-forward simulates a suspended main thread: due timers run once
        # after the elapsed interval, rather than once at every nominal deadline.
        self.page.clock.fast_forward(125000)
        self.assertEqual(self.seconds(), [30, 60, 120])
        self.advance(175)
        self.assertEqual(self.seconds(), [30, 60, 120, 300])

    def test_anchor_changes_keep_the_same_visit_and_page_identity(self):
        self.advance(20)
        self.page.evaluate("location.hash = 'two'")
        self.advance(10)
        self.assertEqual(self.seconds(), [30])
        self.assertEqual(self.events()[0]["data"]["page"], SITE + "/index.html")
        self.page.evaluate("history.pushState({}, '', '#four')")
        self.advance(30)
        self.assertEqual(self.seconds(), [30, 60])

    def test_hard_navigation_starts_fresh_on_both_content_pages(self):
        self.advance(20)
        self.page.goto(SITE + "/books.html", wait_until="load")
        self.advance(29)
        self.assertEqual(self.events(), [])
        self.advance(1)
        self.assertEqual(self.seconds(), [30])
        self.assertEqual(self.events()[0]["data"]["page"], SITE + "/books.html")

    def test_soft_navigation_and_history_start_fresh_for_new_page(self):
        self.advance(20)
        self.page.evaluate("history.pushState({}, '', '/books.html')")
        self.advance(29)
        self.assertEqual(self.events(), [])
        self.advance(1)
        self.assertEqual(self.seconds(), [30])
        self.assertEqual(self.events()[-1]["data"]["page"], SITE + "/books.html")
        self.page.evaluate("history.back()")
        self.page.wait_for_url(SITE + "/index.html")
        self.advance(29)
        self.assertEqual(self.seconds(), [30])
        self.advance(1)
        self.assertEqual(self.seconds(), [30, 30])
        self.assertEqual(self.events()[-1]["data"]["page"], SITE + "/index.html")
        self.page.evaluate("history.replaceState({}, '', '/index.html?view=research')")
        self.advance(30)
        self.assertEqual(self.events()[-1]["data"]["page"], SITE + "/index.html?view=research")

    def test_bfcache_restoration_excludes_time_away_and_starts_new_visit(self):
        self.advance(30)
        self.lifecycle("pagehide")
        self.advance(900)
        self.assertEqual(self.seconds(), [30])
        self.lifecycle("pageshow")
        self.advance(29)
        self.assertEqual(self.seconds(), [30])
        self.advance(1)
        self.assertEqual(self.seconds(), [30, 30])

    def test_overdue_old_milestone_on_popstate_keeps_original_native_page(self):
        self.advance(5)
        self.page.evaluate("""() => {
            // Hold the next deadline's callback while performance.now advances.
            // This models a busy main thread without using private timer state.
            window.__nativeTimeout = window.setTimeout;
            window.setTimeout = (callback, delay, ...args) =>
                window.__nativeTimeout(() => {}, delay, ...args);
            history.pushState({}, '', '/books.html');
        }""")
        self.advance(31)
        self.assertEqual(self.events(), [])
        self.page.evaluate("""() => {
            window.setTimeout = window.__nativeTimeout;
            history.back();
        }""")
        self.page.wait_for_url(SITE + "/index.html")
        self.assertEqual(self.seconds(), [30])
        self.assertEqual(self.events()[0]["url"], "/books.html")
        self.assertEqual(self.events()[0]["data"]["page"], SITE + "/books.html")
        self.advance(29)
        self.assertEqual(self.seconds(), [30])
        self.advance(1)
        self.assertEqual(self.seconds(), [30, 30])
        self.assertEqual(self.events()[-1]["url"], "/index.html")

    def test_deferred_sdk_callback_keeps_captured_milestone_and_page(self):
        self.page.evaluate("""() => {
            window.__pendingTracks = [];
            window.umami = {track(callback) {
                window.__pendingTracks.push(callback);
                return Promise.resolve();
            }};
        }""")
        # A delayed timer crosses three milestones before the SDK evaluates any
        # callback. Then navigation changes the SDK's automatic page context.
        self.page.clock.fast_forward(125000)
        self.page.evaluate("history.pushState({}, '', '/books.html')")
        payloads = self.page.evaluate("""() => window.__pendingTracks.map(callback =>
            callback({url: location.pathname + location.search}))""")
        self.assertEqual([payload["name"] for payload in payloads], [
            "Page visible. 30s", "Page visible. 60s", "Page visible. 120s"
        ])
        for payload, seconds in zip(payloads, [30, 60, 120]):
            self.assertEqual(payload["url"], "/index.html")
            self.assertEqual(payload["data"], {"visible_seconds": seconds, "page": SITE + "/index.html"})

    def test_duplicate_inclusion_and_tracker_failures_are_harmless(self):
        self.page.add_script_tag(url=SITE + "/assets/js/page-visibility.js")
        self.advance(30)
        self.assertEqual(self.seconds(), [30])
        self.page.evaluate("window.__savedUmami = window.umami; delete window.umami")
        self.advance(30)
        self.page.evaluate("window.umami = {track() {throw new Error('Blocked')}}")
        self.advance(60)
        self.page.evaluate("window.umami = {track() {return Promise.reject(new Error('Blocked'))}}")
        self.advance(180)
        self.page.evaluate("window.umami = window.__savedUmami")
        self.advance(300)
        self.assertEqual(self.seconds(), [30, 600])
        self.assertEqual(self.errors, [])

    def test_milestones_are_configurable_without_persistent_timer_state(self):
        source = (ROOT / "assets/js/page-visibility.js").read_text()
        self.assertIn("[30, 60, 120, 300, 600]", source)
        self.script_override = source.replace("[30, 60, 120, 300, 600]", "[30, 60, 90, 120]", 1)
        self.page.goto(SITE + "/books.html", wait_until="load")
        self.advance(120)
        self.assertEqual(self.seconds(), [30, 60, 90, 120])
        self.assertEqual(self.context.cookies(), [])
        self.assertEqual(self.page.evaluate("Object.keys(localStorage)"), ["umami.disabled"])
        self.assertEqual(self.page.evaluate("Object.keys(sessionStorage)"), [])


if __name__ == "__main__":
    unittest.main()
