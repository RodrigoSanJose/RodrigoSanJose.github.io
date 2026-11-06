/* Cumulative visible-page milestones through the existing Umami tracker. */
(function() {
	'use strict';

	if (window.__umamiPageVisibilityInstalled) return;
	window.__umamiPageVisibilityInstalled = true;

	// Configure seconds here. For every 30 seconds through 600, use:
	// Array.from({ length: 600 / 30 }, function(_, i) { return (i + 1) * 30; });
	var milestones = [30, 60, 120, 300, 600];
	milestones = milestones.filter(function(seconds) {
		return Number.isFinite(seconds) && seconds > 0;
	}).sort(function(a, b) { return a - b; }).filter(function(seconds, index, list) {
		return index === 0 || seconds !== list[index - 1];
	});

	var page = pageUrl();
	var elapsed = 0;
	var visibleSince = null;
	var nextMilestone = 0;
	var timer = null;
	var departed = false;

	function pageUrl() {
		// Fragment changes identify locations within a page, not new visits.
		return location.origin + location.pathname + location.search;
	}

	function clearTimer() {
		if (timer !== null) clearTimeout(timer);
		timer = null;
	}

	function accumulate() {
		if (visibleSince === null) return;
		var now = performance.now();
		elapsed += Math.max(0, now - visibleSince);
		visibleSince = now;
	}

	function sendMilestone(seconds) {
		// Capture this visit before invoking the SDK. On popstate the browser's
		// URL has already changed; the milestone still belongs to the old page.
		var name = 'Page visible. ' + seconds + 's';
		var data = { visible_seconds: seconds, page: page };
		try {
			if (!window.umami || typeof window.umami.track !== 'function') return;
			var url = new URL(page);
			var path = url.pathname + url.search;
			var result = window.umami.track(function(payload) {
				return Object.assign({}, payload, { name: name, url: path, data: data });
			});
			if (result && typeof result.catch === 'function') result.catch(function() {});
		} catch (error) {
			// Analytics failures must not affect the page or its timer lifecycle.
		}
	}

	function sendMilestones() {
		while (nextMilestone < milestones.length && elapsed >= milestones[nextMilestone] * 1000) {
			// Advance even when tracking is unavailable: no replay after consent,
			// opt-out changes, or later navigation to a different page.
			sendMilestone(milestones[nextMilestone++]);
		}
	}

	function schedule() {
		clearTimer();
		if (departed || visibleSince === null || nextMilestone >= milestones.length) return;
		var remaining = milestones[nextMilestone] * 1000 - elapsed;
		timer = setTimeout(function() {
			timer = null;
			// A delayed task must never count an interval whose visibility is
			// uncertain. Normal visibilitychange/pagehide handling settles it.
			if (departed || document.visibilityState !== 'visible') {
				visibleSince = null;
				return;
			}
			// Timers can run late. Measure elapsed time instead of counting ticks.
			accumulate();
			sendMilestones();
			schedule();
		}, Math.max(0, remaining));
	}

	function pause() {
		accumulate();
		visibleSince = null;
		clearTimer();
		sendMilestones();
	}

	function resume() {
		if (departed || document.visibilityState !== 'visible' || visibleSince !== null) return;
		visibleSince = performance.now();
		schedule();
	}

	function newVisit() {
		clearTimer();
		page = pageUrl();
		elapsed = 0;
		nextMilestone = 0;
		visibleSince = null;
		departed = false;
		resume();
	}

	document.addEventListener('visibilitychange', function() {
		if (document.visibilityState === 'visible') resume();
		else pause();
	});
	window.addEventListener('pagehide', function() {
		pause();
		departed = true;
	});
	window.addEventListener('pageshow', function(event) {
		// Restoring from the back/forward cache is a new visit. No time spent
		// away from this document can carry over into that visit.
		if (event.persisted || departed) newVisit();
		else resume();
	});

	// The current site uses full document navigation. These hooks also cover
	// future same-origin client-side routes without resetting on anchor changes.
	['pushState', 'replaceState'].forEach(function(method) {
		var original = history[method];
		history[method] = function() {
			var nextPage = arguments[2] == null ? pageUrl() : new URL(arguments[2], location.href);
			var changesPage = typeof nextPage !== 'string' &&
				(nextPage.origin + nextPage.pathname + nextPage.search) !== page;
			// Flush the old visit while Umami still sees its original page URL.
			if (changesPage) pause();
			var result;
			try {
				result = original.apply(this, arguments);
			} catch (error) {
				if (changesPage) resume();
				throw error;
			}
			if (pageUrl() !== page) newVisit();
			return result;
		};
	});
	window.addEventListener('popstate', function() {
		if (pageUrl() !== page) {
			pause();
			newVisit();
		}
	});

	resume();
})();
