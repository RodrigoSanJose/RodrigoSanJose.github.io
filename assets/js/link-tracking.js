/* Shared, cookie-free link events for the existing Umami tracker. */
(function() {
	'use strict';

	// A second script include must not register a second pair of listeners.
	if (window.__umamiLinkTrackingInstalled) return;
	window.__umamiLinkTrackingInstalled = true;

	var maxNameLength = 50;

	function clean(value) {
		return (value || '').replace(/\s+/g, ' ').trim();
	}

	function accessibleLabel(link) {
		var labelledBy = link.getAttribute('aria-labelledby');
		var label = labelledBy && clean(labelledBy.split(/\s+/).map(function(id) {
			var element = document.getElementById(id);
			return element ? element.textContent : '';
		}).join(' '));
		var image = link.querySelector('img[alt]');
		var hiddenLabel = link.querySelector('.label');
		return label || clean(link.getAttribute('aria-label')) ||
			clean(hiddenLabel && hiddenLabel.textContent) || clean(link.getAttribute('title')) ||
			clean(link.textContent) || clean(image && image.getAttribute('alt'));
	}

	// Explicit short names are preferred. This deterministic fallback keeps future
	// long labels recognizable and distinct without depending on DOM order or URL.
	function compact(label, limit) {
		if (label.length <= limit) return label;
		var hash = 2166136261;
		for (var i = 0; i < label.length; i++) {
			hash = Math.imul(hash ^ label.charCodeAt(i), 16777619);
		}
		var suffix = '… #' + (hash >>> 0).toString(16).padStart(8, '0');
		var prefix = label.slice(0, limit - suffix.length);
		var boundary = prefix.lastIndexOf(' ');
		if (boundary > prefix.length / 2) prefix = prefix.slice(0, boundary);
		return prefix.trim() + suffix;
	}

	function destinationType(link, url) {
		var explicit = clean(link.getAttribute('data-link-type'));
		if (explicit) return explicit;
		var arxiv = /(^|\.)arxiv\.org$/i.test(url.hostname);
		if (/\.pdf$/i.test(url.pathname) || (arxiv && /^\/pdf\//i.test(url.pathname)) ||
			link.querySelector('.fa-file-pdf') || /^(download\s+)?pdf$/i.test(accessibleLabel(link))) return 'PDF';
		if (link.querySelector('.ai-arxiv') || arxiv ||
			/\/10\.48550\/arxiv\./i.test(url.pathname)) return 'Arxiv';
		if (link.querySelector('.ai-doi') || /(^|\.)doi\.org$/i.test(url.hostname)) return 'Published paper';
		if (link.querySelector('.fa-github') || /(^|\.)(github\.com|gitlab\.com)$/i.test(url.hostname)) return 'Code';
		return accessibleLabel(link) || 'Related material';
	}

	function placement(link) {
		var section = link.closest('[data-analytics-section]');
		if (section) return clean(section.getAttribute('data-analytics-section'));
		// Strata moves the footer inside the header at desktop widths.
		if (link.closest('footer, #footer, .main-copyright')) return 'Footer';
		if (link.closest('nav')) return 'Navigation';
		if (link.closest('#header')) return 'Header';
		section = link.closest('section');
		var heading = section && section.querySelector('h1, h2, h3');
		return clean(heading && heading.textContent) || clean(section && section.id) || 'Main';
	}

	function linkLabel(link, url) {
		var explicit = clean(link.getAttribute('data-analytics-label'));
		if (explicit) return explicit;
		if (url.protocol === 'mailto:') return 'Email';
		// Profile names also cover future icon-only links without readable text.
		var profiles = [
			[/^scholar\.google\./i, 'Google Scholar'],
			[/(^|\.)linkedin\.com$/i, 'LinkedIn'],
			[/(^|\.)researchgate\.net$/i, 'ResearchGate'],
			[/(^|\.)orcid\.org$/i, 'ORCID'],
			[/(^|\.)scopus\.com$/i, 'Scopus'],
			[/(^|\.)webofscience\.com$/i, 'Web of Science']
		];
		for (var i = 0; i < profiles.length; i++) {
			if (profiles[i][0].test(url.hostname)) return profiles[i][1];
		}
		if (/^(www\.)?github\.com$/i.test(url.hostname) && /^\/[^/]+\/?$/.test(url.pathname)) return 'GitHub';
		var label = accessibleLabel(link);
		if (label) return label;
		// Prefer the target section's title over an opaque fragment identifier.
		if (url.origin === location.origin && url.pathname === location.pathname && url.hash) {
			var target = document.getElementById(decodeURIComponent(url.hash.slice(1)));
			var heading = target && target.querySelector('h1, h2, h3');
			if (target) return clean(heading ? heading.textContent : target.textContent) || target.id;
		}
		return decodeURIComponent(url.pathname.split('/').filter(Boolean).pop() || url.hostname);
	}

	function tableContext(control, data) {
		// Only mathematical parameters explicitly supplied by the table renderer.
		// Never copy arbitrary data attributes, input values, or form contents.
		['q', 'm', 'd', 'r'].forEach(function(key) {
			var attribute = 'data-analytics-' + key;
			var owner = control.closest('[' + attribute + ']');
			var raw = owner && clean(owner.getAttribute(attribute));
			if (!raw || !/^\d+$/.test(raw)) return;
			var value = Number(raw);
			if (Number.isSafeInteger(value)) data[key] = value;
		});
	}

	function buttonLabel(control) {
		var label = clean(control.getAttribute('data-analytics-label')) || accessibleLabel(control);
		if (label) return label;
		var type = (control.getAttribute('type') || '').toLowerCase();
		if (type === 'submit') return 'Submit form';
		if (type === 'reset') return 'Reset form';
		return 'Button';
	}

	function trackActivation(event) {
		if ((event.type === 'click' && event.button !== 0) ||
			(event.type === 'auxclick' && event.button !== 1)) return;
		var target = event.target;
		var control = target && target.closest && target.closest(
			'a[href], area[href], button, input[type="button"], input[type="submit"], input[type="reset"], [role="button"]'
		);
		if (!control || target.closest('[data-analytics-ignore], [inert], [aria-disabled="true"]') ||
			control.closest('[data-analytics-ignore], [inert], [aria-disabled="true"]') ||
			control.matches(':disabled, [disabled]')) return;
		var link = control.matches('a[href], area[href]') ? control : null;
		// Middle-click activates links, not ordinary buttons.
		if (event.type === 'auxclick' && !link) return;
		// Native Umami attribute events have their own listener. Do not double count.
		if (target.closest('[data-umami-event]') || control.closest('[data-umami-event]')) return;
		if (!window.umami || typeof window.umami.track !== 'function') return;

		try {
			var url = new URL(link ? link.getAttribute('href') : location.href, document.baseURI);
			if (!/^(https?:|mailto:|tel:)$/.test(url.protocol)) return;
			var publication = control.closest('[data-publication-title]');
			var label, name;
			if (publication) {
				var title = clean(publication.getAttribute('data-publication-title')).replace(/\.$/, '');
				var type = destinationType(control, url);
				label = title + '. ' + type;
				// Bound custom future destination labels as well as paper titles.
				// The standard destination types fit unchanged in this budget.
				var ending = '. ' + compact(type, 30);
				var shortTitle = clean(publication.getAttribute('data-publication-short')) || title;
				name = label.length <= maxNameLength ? label : compact(shortTitle, maxNameLength - ending.length) + ending;
			} else {
				label = link ? linkLabel(control, url) : buttonLabel(control);
				name = compact(clean(control.getAttribute('data-analytics-name')) || label, maxNameLength);
			}
			var data = { label: label, url: url.href, section: placement(control) };
			tableContext(control, data);
			// Use only the existing API: it retains Umami's exclusions/opt-out rules.
			// Never delay, cancel, or reimplement navigation, including modifier clicks.
			var result = window.umami.track(name, data);
			if (result && typeof result.catch === 'function') result.catch(function() {});
		} catch (error) {
			// Analytics is optional: blocked storage, invalid URLs, or tracker errors
			// must never interfere with following a link.
		}
	}

	// Delegation includes nested icons/images and links inserted after page load.
	// Keyboard Enter already produces click; a key listener would count it twice.
	document.addEventListener('click', trackActivation, true);
	document.addEventListener('auxclick', trackActivation, true);
})();
